import DOMPurify from 'dompurify'
import katex from 'katex'
import { marked } from 'marked'
import { useMemo } from 'react'

import 'katex/dist/katex.min.css'

/**
 * 把 Track 里的富文本渲染成人读的样子（题目、判据、提示、AI 评审都走这里）。
 *
 * **为什么必须渲染而不能 `{text}` 直出**：`ticket.yaml` 的 `brief.problem` / `grader.correctness.expect`
 * 本来就是**按 Markdown 写的**（`**加粗**`、`` `内联代码` `、`1) 2)` 列表、缩进代码块、`①②` 分行）。
 * 当纯文本直出时这些记号原样露出来，而且换行被 HTML 折叠掉——整段话糊成一坨，正是"题目挤成一团"。
 *
 * 三层处理，各管一件事：
 *
 * 1. **marked + GFM**：段落、列表、代码块、表格、引用。
 *    ``breaks: true``（GFM 换行）是有意的：中文题面常在**不打空行**的情况下靠单换行分行
 *    （`① …… ② ……`），按标准 Markdown 会把它们并成一段，那才是真的读不下去。
 * 2. **KaTeX**：`$…$` / `$$…$$`。数学只在**文本节点**里替换——先在 DOM 上走一遍、跳过
 *    `code`/`pre` 里的内容，所以 SQL 里的 `$`（MyBatis 的 `${…}`）不会被当成公式。
 * 3. **DOMPurify**：内容可能来自**社区贡献的 Track**（`trust_level: community`，§13 把 Track
 *    当供应链攻击面），所以渲染结果一律先消毒。`marked` 也已关掉原始 HTML（`html: false`），
 *    两道一起挡 `<script>` / `onerror=` / `javascript:`。
 *
 * 渲染顺序是"先消毒、后插公式"，公式串由 KaTeX 自己转义（默认 `trust: false`，不接受裸 HTML），
 * 因此后插这一步不引入未消毒内容。
 */

const MATH = /\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g

marked.setOptions({ gfm: true, breaks: true })

/**
 * CJK 题面的**排版规范化**：把作者"为了在 YAML 里看得清"而敲的硬换行，还原成他真正的意图。
 *
 * 中文题面在 `|` 块里通常每行 ~50 字就换行（编辑器里好看），同时又会用单换行做**有意义的**分行
 * （`① … ② …`、`【线上问题｜…】` 与正文分行）。两者在源码里长得一模一样，但对读者是相反的：
 * 前者要接起来，后者要断开。纯 Markdown 只有"全部接起来"（丢结构）或"全部断开"（句子被劈成两半，
 * 就是截图上 `product 表现在有 / 5 万多行` 那种）两种选择，都不对。
 *
 * 于是按**标点与形状**判断，规则只有两条，且只在两侧都不像代码/块级结构时生效：
 *
 * 1. **不接**：上一行以句末或收尾标点结尾（`。！？；：…）】」` 等）⇒ 作者在这里说完了；
 *    或者下一行本身就是块级开头（标题 / 列表项 / 引用 / 表格 / 围栏）⇒ 接了会把列表黏成一行。
 * 2. **缩进 ≥4 的行一律不动**：那是 Markdown 的缩进代码块（题面里的 SQL 就靠它）。
 *
 * 另外把"看起来是代码的缩进块"明确**围栏**起来：缩进代码块在列表项里 markdown 不认（5 个空格的
 * 缩进会被当成列表项的续行），于是题面里的 SQL 会散成普通段落——这是截图上最扎眼的一处。
 */
const SENTENCE_END = /[。！？；：!?;:…—]$|[】」』”’]$/
/**
 * 注意这里**不含**括号：`…Invalid bound statement (not found)` 是句子中间，作者换行只是排版；
 * 而真正收尾的中文句号/分号已经在集合里，所以去掉括号不会让两句黏在一起。
 */
/** 行首的"新条目"记号：Markdown 的列表、以及中文题面常用的 ①② / 一、/ （1）。 */
const ENUM_START = /^(?:[①-⑳]|[一二三四五六七八九十]+[、.)]|[（(]\d+[）)]|\d+、)/
const BLOCK_START = new RegExp(
  `^(?:#{1,6}\\s|[-*+]\\s|\\d+[.)]\\s|>|\\||\`\`\`|~~~)|${ENUM_START.source}`,
)
const HEADING_OR_QUOTE = /^(#{1,6}\s|>|\||```|~~~)/
/** 代码相的特征：赋值/占位符/尖括号/分号，或 SQL 关键字开头。 */
const CODEISH = /[={}<>[\];]|^\s*(WHERE|AND|OR|SELECT|FROM|ORDER\s+BY|GROUP\s+BY|LIMIT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|--|#|<[a-z]+>)/i
const INDENTED = /^\s{4,}\S/

/**
 * 接两行时决定中间要不要空格：中文与拉丁字母/数字之间加一个（否则会出现「有5 万多行」这种）。
 * 中文之间不加——中文本来就不用空格分词。
 */
export function joinCjk(prev: string, next: string): string {
  const boundary = /[\u4e00-\u9fff]$/.test(prev) && /^[A-Za-z0-9]/.test(next)
  const reverse = /[A-Za-z0-9]$/.test(prev) && /^[\u4e00-\u9fff]/.test(next)
  // 箭头两侧留空格：作者自己就是这么写的（`A → B`），接行时不该把它挤成 `)→ B`。
  const arrow = /[→←⇒⇐]$/.test(prev) || /^[→←⇒⇐]/.test(next)
  return `${prev}${boundary || reverse || arrow ? ' ' : ''}${next}`
}

export function normalizeCjkLayout(src: string): string {
  const lines = src.split('\n')
  const out: string[] = []
  let fence = false
  let i = 0

  while (i < lines.length) {
    const line = lines[i]
    if (/^\s*(```|~~~)/.test(line)) {
      fence = !fence
      out.push(line)
      i += 1
      continue
    }
    if (fence) {
      out.push(line)
      i += 1
      continue
    }

    // --- 缩进的代码块：明确围栏，免得在列表项里散成段落 ---
    if (INDENTED.test(line)) {
      const run: string[] = []
      let j = i
      let blanks = 0
      while (j < lines.length) {
        const candidate = lines[j]
        if (candidate.trim() === '') {
          // 只容忍块内一个空行（作者常用它分开 WHERE 与 ORDER BY）
          if (blanks >= 1 || !INDENTED.test(lines[j + 1] ?? '')) break
          blanks += 1
          run.push('')
          j += 1
          continue
        }
        if (!INDENTED.test(candidate)) break
        run.push(candidate)
        j += 1
      }
      const codeLines = run.filter((l) => l.trim() !== '')
      const looksLikeCode = codeLines.filter((l) => CODEISH.test(l)).length >= 2
      const isProse = codeLines.some((l) => /[。；：]$/.test(l.trim()))
      if (looksLikeCode && !isProse) {
        const indent = Math.min(
          ...codeLines.map((l) => l.length - l.trimStart().length),
        )
        const lang = /^\s*</.test(codeLines[0])
          ? 'xml'
          : /\b(SELECT|WHERE|AND|CREATE|INSERT|UPDATE|DELETE|ALTER|DROP|ORDER\s+BY)\b/i.test(
                codeLines.join(' '),
              )
            ? 'sql'
            : ''
        out.push('```' + lang, ...run.map((l) => (l.trim() === '' ? '' : l.slice(indent))), '```')
        i = j
        continue
      }
      // 不是代码（例如 ①② 那种分点叙述）就原样交给下面的接行逻辑逐行处理
    }

    // --- 硬换行：作者没说完就接起来 ---
    let merged = line
    while (i + 1 < lines.length) {
      const next = lines[i + 1]
      if (next.trim() === '' || merged.trim() === '') break
      if (INDENTED.test(merged) || INDENTED.test(next)) break
      if (SENTENCE_END.test(merged.trim()) || HEADING_OR_QUOTE.test(merged.trim())) break
      if (BLOCK_START.test(next.trim())) break
      merged = joinCjk(merged.trimEnd(), next.trim())
      i += 1
    }
    out.push(merged)
    i += 1
  }

  return out.join('\n')
}

/** 只替换**文本节点**里的公式：跳过 `code` / `pre`，避免把代码里的 `$` 当公式。 */
function renderMathInTextNodes(root: ParentNode): void {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const raw = node.nodeValue ?? ''
      if (!raw.includes('$')) return NodeFilter.FILTER_REJECT
      for (let el = node.parentElement; el; el = el.parentElement) {
        const tag = el.tagName.toLowerCase()
        if (tag === 'code' || tag === 'pre' || tag === 'script' || tag === 'style') {
          return NodeFilter.FILTER_REJECT
        }
      }
      return NodeFilter.FILTER_ACCEPT
    },
  })

  const targets: Text[] = []
  while (walker.nextNode()) targets.push(walker.currentNode as Text)

  for (const node of targets) {
    const holder = document.createElement('span')
    holder.innerHTML = (node.nodeValue ?? '').replace(MATH, (match, display, inline) => {
      const tex = (display ?? inline) as string
      try {
        return katex.renderToString(tex, {
          displayMode: display !== undefined,
          throwOnError: false,
          // 不因为一个 `\foo` 之类的小问题在控制台刷警告；渲染不出来时 KaTeX 会画出红色原文。
          strict: 'ignore',
        })
      } catch {
        return match // 真渲染不了就原样留着，绝不吞掉作者写的东西
      }
    })
    node.replaceWith(...Array.from(holder.childNodes))
  }
}

export function renderMarkdown(src: string): string {
  const html = marked.parse(normalizeCjkLayout(src), {
    gfm: true,
    breaks: true,
    async: false,
  }) as string
  const clean = DOMPurify.sanitize(html, {
    ADD_ATTR: ['target', 'rel'],
    // KaTeX 用行内 style 排版（间距、上下标位置），必须放行；DOMPurify 会做 CSS 消毒。
    ADD_TAGS: ['math', 'semantics', 'annotation', 'mrow', 'mi', 'mo', 'mn', 'msup', 'msub', 'mfrac'],
  })
  const doc = document.implementation.createHTMLDocument('')
  const holder = doc.createElement('div')
  holder.innerHTML = clean
  renderMathInTextNodes(holder)
  // 空段落（换行残留）没有信息，只会制造额外的间距。
  return holder.innerHTML.replace(/<p>\s*<\/p>/g, '')
}

interface Props {
  /** Markdown 源码。可能是 Track 作者写的题面，也可能是 AI 评审的产出。 */
  text: string
  /** ``compact`` 用于列表项/提示这类已经在别处排好版的位置：去掉段落上下边距。 */
  variant?: 'block' | 'compact'
  className?: string
}

export function Markdown({ text, variant = 'block', className }: Props) {
  // 内容不变就不重算：题面可能很长，光 marked + KaTeX 不值得每次渲染都跑一遍。
  const html = useMemo(() => renderMarkdown(text), [text])
  return (
    <div
      className={`prose prose--${variant}${className ? ` ${className}` : ''}`}
      data-testid="markdown"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}

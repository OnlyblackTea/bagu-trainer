import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Markdown, normalizeCjkLayout, renderMarkdown } from '../components/Markdown'

/**
 * 题面/判据/提示是按 Markdown 写的（见 ticket.yaml），所以这一段渲染器直接决定"题目读不读得下去"。
 * 这里守三件事：**记号真的被渲染**、**作者的分行意图被还原**、**社区 Track 的内容进不了 <script>**。
 */

describe('CJK 题面的排版规范化（接行 vs 断行）', () => {
  it('为了排版敲的硬换行接回去：一句话不该被劈成两半', () => {
    // 真 Track 的原句就是这么断的（作者在 YAML 里每 ~50 字换一行）
    const src = '运营反馈：搜索越来越慢。product 表现在有\n5 万多行，每次搜索都卡。'
    expect(renderMarkdown(src)).toContain('product 表现在有 5 万多行')
  })

  it('以逗号结尾的续行也接回去（逗号是"还没说完"，不是句末）', () => {
    const src = '① 正确性：插入 5 条 fixture，其中 3 条命中，\n   断言恰好返回 3 条；\n② 索引：断言 type != ALL。'
    const html = renderMarkdown(src)
    expect(html).toContain('其中 3 条命中，断言恰好返回 3 条；')
    // ② 是新条目 ⇒ 必须另起一行
    expect(html).toMatch(/断言恰好返回 3 条；<br>② 索引/)
  })

  it('右括号不是句末：`(not found)` 后面接的续行要接回去', () => {
    const src = '① 未绑定 → BindingException: Invalid bound statement (not found)\n→ 正确性用例失败；'
    expect(renderMarkdown(src)).toContain('(not found) → 正确性用例失败')
  })

  it('①②/1) 这些新条目不许被黏进上一行', () => {
    const html = renderMarkdown('初始为【红】：\n① 没绑定\n② 没建索引')
    expect(html).toMatch(/① 没绑定<br>② 没建索引/)
  })

  it('列表项不许被黏成一行（否则整个列表塌掉）', () => {
    const src = '- 第一项\n- 第二项\n- 第三项'
    const html = renderMarkdown(src)
    expect(html.match(/<li>/g)).toHaveLength(3)
  })

  it('标题/引用/表格后面不许接行', () => {
    expect(renderMarkdown('## 小节\n正文')).toContain('<h2>小节</h2>')
    expect(normalizeCjkLayout('| a | b |\n|---|---|\n| 1 | 2 |')).not.toContain('| a | b |---')
  })

  it('缩进的类代码块被明确围栏起来（否则在列表项里会散成普通段落）', () => {
    const src = [
      '1) src/main/resources/mapper/ProductMapper.xml',
      '   取消注释并补齐，语义要求：',
      '     WHERE category_id = #{categoryId}',
      '       AND status      = #{status}',
      '       AND price BETWEEN #{minPrice} AND #{maxPrice}',
      '',
      '     ORDER BY price ASC',
      '     LIMIT #{limit}',
      '   返回列用 resultMap="productMap"。',
    ].join('\n')
    const html = renderMarkdown(src)
    expect(html).toContain('<pre><code class="language-sql"')
    // 缩进被去掉、内部空行保留（作者用它分开 WHERE 与 ORDER BY）
    expect(html).toContain('WHERE category_id = #{categoryId}')
    expect(html).toContain('ORDER BY price ASC')
    expect(html).not.toContain('     WHERE')
    // 前后的散文不受影响
    expect(html).toContain('取消注释并补齐')
    expect(html).toContain('返回列用')
  })

  it('缩进的中文分点叙述**不**被误当成代码', () => {
    const src = '改对后为【绿】：\n  ① 正确性：恰好返回 3 条；\n  ② 索引：type != ALL。'
    const html = renderMarkdown(src)
    expect(html).not.toContain('<pre>')
    expect(html).toContain('① 正确性')
  })

  it('已经围栏的代码块原样保留（不再被规范化动一次）', () => {
    const src = '```sql\nWHERE a = 1\n  AND b = 2\n```'
    expect(normalizeCjkLayout(src)).toBe(src)
  })
})

describe('Markdown 渲染题面', () => {
  it('加粗 / 行内代码 / 有序列表都渲染成结构，而不是原样露出记号', () => {
    const html = renderMarkdown('**索引名必须叫 idx_product_search**\n\n1) 改 XML\n2) 改 SQL')
    expect(html).toContain('<strong>索引名必须叫 idx_product_search</strong>')
    expect(html).toContain('<ol>')
    expect(html).toContain('<li>改 XML</li>')
    expect(html).not.toContain('**')
  })

  it('单换行就换行：中文题面常靠单换行分行（①②），并成一段就读不下去了', () => {
    const html = renderMarkdown('初始为【红】：\n① 没绑定\n② 没建索引')
    expect(html).toContain('<br>')
    expect(html).toMatch(/① 没绑定<br>② 没建索引/)
  })

  it('围栏代码块渲染成 pre>code，且不再套一层行内代码底色', () => {
    const html = renderMarkdown('语义要求：\n\n```sql\nSELECT 1;\n```\n')
    expect(html).toContain('<pre>')
    expect(html).toMatch(/<pre><code[^>]*>SELECT 1;/)
    // 行内 code 才有边框底色，块内的不该有（CSS 依赖这个结构）
    expect(html).not.toMatch(/<pre><code[^>]*><code/)
  })

  it('表格按 GFM 渲染（判据/对照表用得上）', () => {
    const html = renderMarkdown('| 列 | 含义 |\n|---|---|\n| price | 价格 |\n')
    expect(html).toContain('<table>')
    expect(html).toContain('<th>含义</th>')
  })
})

describe('Markdown 里的 TeX（KaTeX）', () => {
  it('行内 $…$ 与块级 $$…$$ 都渲染成 KaTeX', () => {
    const inline = renderMarkdown('选择率 $\\frac{3}{50000}$ 很低。')
    expect(inline).toContain('katex')
    expect(inline).not.toContain('$\\frac')

    const block = renderMarkdown('复杂度：\n\n$$O(\\log n)$$')
    expect(block).toContain('katex-display')
  })

  it('**代码里的 $ 不是公式**：MyBatis 的 ${…} 不能被当成数学', () => {
    const inlineCode = renderMarkdown('用 `${minPrice}` 占位（这是字面量）。')
    expect(inlineCode).toContain('<code>${minPrice}</code>')
    expect(inlineCode).not.toContain('katex')

    const fenced = renderMarkdown('```sql\nSELECT * FROM t WHERE p > ${min}\n```')
    expect(fenced).toContain('${min}')
    expect(fenced).not.toContain('katex')
  })

  it('渲染不出来的公式标红但**不吞内容**（宁可难看，也不能把作者写的东西吃掉）', () => {
    const html = renderMarkdown('$\\notARealCommand{$')
    expect(html).toContain('katex-error')
    expect(html).toContain('\\notARealCommand{')
  })
})

describe('Markdown 消毒（社区 Track 是供应链攻击面，§13）', () => {
  it('script / 事件处理器 / javascript: 链接都被清掉', () => {
    const html = renderMarkdown(
      '<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n\n[x](javascript:alert(1))',
    )
    expect(html).not.toContain('<script')
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('javascript:')
  })

  it('正常内容不被误伤', () => {
    const html = renderMarkdown('看 [文档](https://example.com) 与 `code`')
    expect(html).toContain('href="https://example.com"')
    expect(html).toContain('<code>code</code>')
  })
})

describe('Markdown 组件', () => {
  it('block / compact 两种排版的类名不同（列表里的提示要去掉段落边距）', () => {
    const { rerender } = render(<Markdown text="一句话" />)
    expect(screen.getByTestId('markdown')).toHaveClass('prose--block')
    rerender(<Markdown text="一句话" variant="compact" />)
    expect(screen.getByTestId('markdown')).toHaveClass('prose--compact')
  })
})

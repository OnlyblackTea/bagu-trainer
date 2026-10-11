import type { ReactNode } from 'react'

interface Props {
  /** 锚点 id（题面目录里那几个 `#ticket-*` 之外的小节用它）。 */
  id?: string
  title: string
  /** 标题右侧的一句说明：这一节回答什么问题。 */
  hint?: string
  children: ReactNode
}

/**
 * 文档区里的一节（中间那一栏就是由若干节拼出来的一篇"文档"）。
 *
 * 为什么要有这一层：中栏原来是"每个组件一张卡片"的堆叠，读者看不出先后与从属。改成文档之后，
 * 每节有**编号感的标题 + 一句说明它回答什么问题**，读的时候知道自己在文档的哪一段；也正因为
 * 有了这层壳，各节内部才可以只管自己的内容（表格、代码、清单），不用再各写一个 `<h2 class="card__title">`。
 */
export function DocSection({ id, title, hint, children }: Props) {
  return (
    <section className="doc__section" id={id}>
      <h2 className="doc__title">
        {title}
        {hint && <span className="doc__hint">{hint}</span>}
      </h2>
      <div className="doc__body">{children}</div>
    </section>
  )
}

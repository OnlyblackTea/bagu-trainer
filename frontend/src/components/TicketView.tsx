import { useEffect, useState } from 'react'

import { api } from '../api'
import type { StageEntry, Ticket } from '../types'
import { Markdown } from './Markdown'

interface Props {
  stage: StageEntry
  /** 取卷必须带 Track：ticket id 只在 Track 内唯一，多 Track 下扁平路径会撞名 409。 */
  trackId: string
}

/** 题面里的小节锚点。题面可以很长（真 Track 的题面是半屏 Markdown），有目录才跳得动。 */
const SECTIONS = [
  { id: 'problem', label: '题面' },
  { id: 'files', label: '改动面' },
  { id: 'grader', label: '判据' },
  { id: 'hints', label: '提示' },
] as const

/**
 * 屏幕 2：发卷（spec §C.6）。
 *
 * 内容是 Track 固定的（作者写好的 `ticket.yaml`），**不经 LLM**。
 *
 * 排版上刻意做成**一篇文档**而不是一坨文字：题面按 Markdown 渲染（作者本来就是按 Markdown 写的，
 * 见 :mod:`./Markdown`），正文限宽到约 68 字符、行高 1.8，小节各有标题与锚点。
 *
 * **D2 防剧透**：本屏只给题面与判据（problem / concepts / files_to_change / hints / grader），
 * **绝不展示解法 ref、分支内容或任何 `-sol` 文本**。解法只在工作区 diff 里、且仅对已 provision
 * 的实例可达。
 */
export function TicketView({ stage, trackId }: Props) {
  const [ticket, setTicket] = useState<Ticket | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setTicket(null)
    setError(null)
    if (stage.kind === 'board_only' || !stage.ticket_id) {
      return // 未出卷阶段没有工单可取，也不去取
    }
    let cancelled = false
    api
      .getTrackTicket(trackId, stage.ticket_id)
      .then((t) => {
        if (!cancelled) setTicket(t)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [trackId, stage.kind, stage.ticket_id])

  if (stage.kind === 'board_only') {
    return (
      <article className="card ticket" data-testid="ticket-view">
        <h2 className="card__title">工单 · {stage.stage}</h2>
        <p className="muted">
          这个阶段<strong>只上榜、不出卷</strong>
          ：它是环境入职/体检类阶段，没有可考核的工单（`scope.files_to_change` 要求非空、
          判别性门要求"骨架红"）。任务书留在 Track 仓库的 `TASKS/` 里，不进平台状态机。
        </p>
      </article>
    )
  }

  if (error) {
    return (
      <article className="card ticket" data-testid="ticket-view">
        <h2 className="card__title">工单 · {stage.stage}</h2>
        <div className="alert alert--error">{error}</div>
      </article>
    )
  }

  if (!ticket) {
    return (
      <article className="card ticket" data-testid="ticket-view">
        <h2 className="card__title">工单 · {stage.stage}</h2>
        <p className="muted">加载中…</p>
      </article>
    )
  }

  return (
    <article className="card ticket" data-testid="ticket-view">
      <header className="ticket__head">
        <div className="ticket__kicker">
          阶段 {ticket.stage} · <code>{ticket.id}</code> · 工单 v{ticket.ticket_version}
        </div>
        <h2 className="ticket__title">{ticket.title}</h2>
        {ticket.concepts.length > 0 && (
          <div className="ticket__concepts">
            {ticket.concepts.map((c) => (
              <span className="badge" key={c}>
                {c}
              </span>
            ))}
          </div>
        )}
      </header>

      <nav className="ticket__toc" aria-label="题面小节">
        {SECTIONS.map((s) => (
          <a key={s.id} href={`#ticket-${s.id}`}>
            {s.label}
          </a>
        ))}
      </nav>

      <section className="ticket__section" id="ticket-problem">
        <h3 className="ticket__label">题面</h3>
        <Markdown text={ticket.problem} />
      </section>

      <section className="ticket__section" id="ticket-files">
        <h3 className="ticket__label">
          改动面
          <span className="muted">你亲手写这些文件（也就评这些）</span>
        </h3>
        <ul className="files">
          {ticket.files_to_change.map((f) => (
            <li key={f}>
              <code>{f}</code>
            </li>
          ))}
        </ul>
      </section>

      <section className="ticket__section" id="ticket-grader">
        <h3 className="ticket__label">
          判据
          <span className="muted">红 → 绿，确定性，非 LLM 打分</span>
        </h3>
        <dl className="kv">
          <dt>测试组</dt>
          <dd>
            <code>{ticket.grader.test_group}</code>
          </dd>
          <dt>canonical 测试</dt>
          <dd>
            <code>{ticket.grader.canonical_test}</code>
          </dd>
        </dl>
        {ticket.grader.expect && (
          <div className="ticket__expect">
            <Markdown text={ticket.grader.expect} />
          </div>
        )}
      </section>

      {ticket.hints.length > 0 && (
        <section className="ticket__section" id="ticket-hints">
          <h3 className="ticket__label">
            提示
            <span className="muted">卡住了再展开</span>
          </h3>
          <div className="hints">
            {ticket.hints.map((h) => {
              const preview = hintPreview(h.text)
              return (
                <details key={h.id} className="hint">
                  <summary>
                    <span className="badge">{h.id}</span>
                    <span className="hint__first">{preview ?? '展开查看'}</span>
                  </summary>
                  <Markdown text={h.text} variant="compact" />
                </details>
              )
            })}
          </div>
        </section>
      )}
    </article>
  )
}

/**
 * 折叠标题上的一行预览——**只给长提示**。
 *
 * 短提示（≤40 字）故意不给预览：展开就一眼看完，放个"截断版"只是把同一句话在 DOM 里放两遍，
 * 屏幕阅读器会念两遍，测试也会撞上"匹配到多个元素"。
 */
function hintPreview(text: string): string | null {
  if (text.length <= 40) return null
  const line = text.split('\n')[0].replace(/[*`]/g, '')
  return line.length > 42 ? `${line.slice(0, 42)}…` : line
}

import { useEffect, useState } from 'react'

import { api } from '../api'
import type { StageEntry, Ticket } from '../types'

interface Props {
  stage: StageEntry
}

/**
 * 屏幕 2：发卷（spec §C.6）。
 *
 * 内容是 Track 固定的（作者写好的 `ticket.yaml`），**不经 LLM**；这里只渲染题面。
 *
 * **D2 防剧透**：本屏只给题面与判据（problem / concepts / files_to_change / hints /
 * grader），**绝不展示解法 ref、分支内容或任何 `-sol` 文本**。解法只在工作区 diff 里、
 * 且仅对已 provision 的实例可达。
 */
export function TicketView({ stage }: Props) {
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
      .getTicket(stage.ticket_id)
      .then((t) => {
        if (!cancelled) setTicket(t)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [stage.kind, stage.ticket_id])

  if (stage.kind === 'board_only') {
    return (
      <section className="card" data-testid="ticket-view">
        <h2 className="card__title">工单 · {stage.stage}</h2>
        <p className="muted">
          这个阶段<strong>只上榜、不出卷</strong>
          ：它是环境入职/体检类阶段，没有可考核的工单（`scope.files_to_change` 要求非空、
          判别性门要求"骨架红"）。任务书留在 Track 仓库的 `TASKS/` 里，不进平台状态机。
        </p>
      </section>
    )
  }

  if (error) {
    return (
      <section className="card" data-testid="ticket-view">
        <h2 className="card__title">工单 · {stage.stage}</h2>
        <div className="alert alert--error">{error}</div>
      </section>
    )
  }

  if (!ticket) {
    return (
      <section className="card" data-testid="ticket-view">
        <h2 className="card__title">工单 · {stage.stage}</h2>
        <p className="muted">加载中…</p>
      </section>
    )
  }

  return (
    <section className="card" data-testid="ticket-view">
      <h2 className="card__title">
        {ticket.title}
        <span className="badge">{ticket.id}</span>
        <span className="badge">v{ticket.ticket_version}</span>
        <span className="badge">阶段 {ticket.stage}</span>
      </h2>

      <p>{ticket.problem}</p>

      <h3 className="card__title">考点</h3>
      <ul className="tight">
        {ticket.concepts.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>

      <h3 className="card__title">改动面（你亲手写这些文件）</h3>
      <ul className="tight">
        {ticket.files_to_change.map((f) => (
          <li key={f}>
            <code>{f}</code>
          </li>
        ))}
      </ul>

      <h3 className="card__title">判据（红 → 绿）</h3>
      <dl className="kv">
        <dt>测试组</dt>
        <dd>
          <code>{ticket.grader.test_group}</code>
        </dd>
        <dt>canonical 测试</dt>
        <dd>
          <code>{ticket.grader.canonical_test}</code>
        </dd>
        {ticket.grader.expect && (
          <>
            <dt>期望</dt>
            <dd>{ticket.grader.expect}</dd>
          </>
        )}
      </dl>

      {ticket.hints.length > 0 && (
        <>
          <h3 className="card__title">提示</h3>
          <ul className="tight">
            {ticket.hints.map((h) => (
              <li key={h.id}>
                <span className="badge">{h.id}</span> {h.text}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

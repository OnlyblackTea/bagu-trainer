import { useEffect, useState } from 'react'

import { api } from '../api'
import type { AttemptOut, InstanceHistoryOut } from '../types'
import { ReportView } from './ReportView'

interface Props {
  instanceId: string
  /** 每次提交后 +1，用来触发重新拉历史（历史是"提交"的产物，不是挂载的产物）。 */
  refreshKey: number
}

/** 一条测试状态迁移 → 人话。红→绿和绿→红是**相反**的消息，措辞必须分开。 */
function changeLabel(frm: string | null, to: string | null): string {
  if (frm === null) return '新增'
  if (to === null) return '消失'
  if (frm !== 'pass' && to === 'pass') return '修好了'
  if (frm === 'pass' && to !== 'pass') return '弄坏了'
  return '变了'
}

function seconds(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

/** 耗时变化说"多了/少了多少"，而不是给两个带正号的绝对值（"+120 s → +20 s" 读起来是错的）。 */
function elapsedChange(before: number | null | undefined, after: number | null | undefined): string {
  if (before === null || before === undefined || after === null || after === undefined) return ''
  const diff = after - before
  if (diff === 0) return '（一样快）'
  return `（${diff < 0 ? '少了' : '多了'} ${seconds(Math.abs(diff))}）`
}

/**
 * 历史面板（spec §11：提交即答辩，需要能回看过程）。
 *
 * 刷同一道题会提交很多次，而"这次和上次差在哪"正是练习的价值所在。所以每条尝试给四样东西：
 * **判定**（过没过）、**耗时**（慢在哪一段）、**报告**（展开逐条看）、**与上一次的比对**
 * （只有真正变了的测试才列出来——十几条里报一条没动的等于没说）。
 *
 * 历史全部来自后端（`GET /instances/{id}/history`），与进度端点是**同一份落库数据**，所以刷新
 * 页面、甚至重启服务之后，这里照样是完整的。
 */
export function HistoryView({ instanceId, refreshKey }: Props) {
  const [history, setHistory] = useState<InstanceHistoryOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .history(instanceId)
      .then((h) => {
        if (!cancelled) {
          setHistory(h)
          setError(null)
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [instanceId, refreshKey])

  if (error) {
    return (
      <section className="card" data-testid="history-view">
        <h2 className="card__title">历史尝试</h2>
        <div className="alert alert--error">{error}</div>
      </section>
    )
  }

  if (!history) {
    return (
      <section className="card" data-testid="history-view">
        <h2 className="card__title">历史尝试</h2>
        <p className="muted">加载中…</p>
      </section>
    )
  }

  if (history.attempts.length === 0) {
    return (
      <section className="card" data-testid="history-view">
        <h2 className="card__title">历史尝试</h2>
        <p className="muted">还没有提交过——提交一次，这里就会留下记录（含阅卷阶段的耗时）。</p>
      </section>
    )
  }

  return (
    <section className="card" data-testid="history-view">
      <h2 className="card__title">
        历史尝试
        <span className="badge">{history.attempts.length} 次</span>
      </h2>
      <p className="muted">
        同一道题的每一次提交都在这里，含阅卷运行记录（阶段与耗时）与**与上一次的差异**。
      </p>

      <ol className="attempts">
        {history.attempts.map((attempt: AttemptOut) => {
          const verdict = attempt.verdict
          return (
            <li className="attempt" key={attempt.submission.id} data-testid="history-attempt">
              <div className="row">
                <span className="badge">第 {attempt.submission.seq} 次</span>
                {verdict ? (
                  <span className={`badge badge--${verdict === 'pass' ? 'passed' : 'failed'}`}>
                    {verdict === 'pass' ? '通过' : '未通过'}
                  </span>
                ) : (
                  <span className="badge badge--stale">未判定</span>
                )}
                <span className="badge">阅卷 {attempt.grading?.status ?? '无记录'}</span>
                <span className="badge">耗时 {seconds(attempt.grading?.elapsed_ms)}</span>
                <span className="muted">{attempt.submission.created_at}</span>
              </div>

              {attempt.grading?.error && (
                <div className="alert alert--error">
                  阅卷没跑起来：{attempt.grading.error.reason}（{attempt.grading.error.detail}）
                </div>
              )}

              {attempt.delta && (
                <div className="delta" data-testid="history-delta">
                  <strong>对比第 {attempt.delta.against} 次：</strong>
                  <span className="muted">
                    耗时 {seconds(attempt.delta.elapsed_ms_before)} →{' '}
                    {seconds(attempt.delta.elapsed_ms_after)}
                    {elapsedChange(attempt.delta.elapsed_ms_before, attempt.delta.elapsed_ms_after)}
                  </span>
                  {attempt.delta.tests.length === 0 ? (
                    <span className="muted"> · 逐条测试结果没有变化</span>
                  ) : (
                    <ul className="tight">
                      {attempt.delta.tests.map((c) => (
                        <li key={c.name}>
                          <span className="badge">{changeLabel(c.frm, c.to)}</span>{' '}
                          <code>{c.name}</code>{' '}
                          <span className="muted">
                            {c.frm ?? '（无）'} → {c.to ?? '（无）'}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {attempt.report ? (
                <details>
                  <summary className="muted">
                    看第 {attempt.submission.seq} 次的阅卷报告
                    {attempt.report.correctness.passed ? '（绿）' : '（红）'}
                  </summary>
                  <ReportView report={attempt.report} />
                </details>
              ) : (
                <p className="muted">这次没有阅卷报告。</p>
              )}
            </li>
          )
        })}
      </ol>
    </section>
  )
}

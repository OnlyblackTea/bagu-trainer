import { useEffect, useState } from 'react'

import { api } from './api'
import { DecisionView } from './components/DecisionView'
import { ReviewView } from './components/ReviewView'
import { SimulatedBadge } from './components/SimulatedBadge'
import { StageBoard } from './components/StageBoard'
import { TicketView } from './components/TicketView'
import { WorkspaceView } from './components/WorkspaceView'
import type {
  DecisionOut,
  GradingScenario,
  InstanceOut,
  ReviewOut,
  ReviewScenario,
  StageEntry,
  SubmissionOut,
  TrackDetail,
} from './types'

/** 自托管演练所的唯一用户（部署者本人）。 */
const LEARNER = 'deployer'

export default function App() {
  const [track, setTrack] = useState<TrackDetail | null>(null)
  const [instances, setInstances] = useState<InstanceOut[]>([])
  const [selected, setSelected] = useState<StageEntry | null>(null)

  // 当前阶段这条链上的产物：提交 → 评审 → 决策。换阶段就清空。
  const [submission, setSubmission] = useState<SubmissionOut | null>(null)
  const [review, setReview] = useState<ReviewOut | null>(null)
  const [decision, setDecision] = useState<DecisionOut | null>(null)

  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const instanceOf = (stage: StageEntry): InstanceOut | undefined =>
    instances.find((i) => i.ticket_id === stage.ticket_id)

  /** 所有动作都走这里：统一 busy / 错误呈现（短码已在 api 层翻成人话）。 */
  async function run(fn: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const refreshInstances = () => api.listInstances(LEARNER).then(setInstances)

  useEffect(() => {
    let cancelled = false
    void run(async () => {
      const list = await api.listTracks()
      const details = await Promise.all(list.map((t) => api.getTrack(t.id)))
      if (cancelled) return
      setTrack(details[0] ?? null)
      setInstances(await api.listInstances(LEARNER))
    })
    return () => {
      cancelled = true
    }
    // 只在挂载时拉一次；后续刷新都走具体动作。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function selectStage(stage: StageEntry) {
    setSelected(stage)
    setSubmission(null)
    setReview(null)
    setDecision(null)
    setNotice(null)
    setError(null)
  }

  const current = selected ? instanceOf(selected) : undefined

  return (
    <div className="app">
      <header className="app__header">
        <h1 className="app__title">八股实战演练所</h1>
        <p className="app__tagline">
          内容（Track）+ git + 本地阅卷 + 确定性门。平台只 <strong>gate + 指示</strong>
          ；被评分的代码由你在本地亲手写。
        </p>
        <p className="app__meta">
          部署者即唯一用户 · <SimulatedBadge />
          阅卷 / AI 评审 / Forge 三处均为占位后端
        </p>
      </header>

      {error && (
        <div className="alert alert--error" role="alert" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}
      {notice && (
        <div className="alert alert--info" style={{ marginBottom: 16 }}>
          {notice}
        </div>
      )}

      <main className="app__main">
        {track ? (
          <StageBoard
            track={track}
            stateOf={instanceOf}
            selectedStage={selected?.stage ?? null}
            onSelect={selectStage}
            onEnroll={() =>
              run(async () => {
                const res = await api.enroll(LEARNER)
                setInstances(res.instances)
                setNotice('已入学：根阶段可开始，其后阶段保持未解锁。')
              })
            }
            busy={busy}
          />
        ) : (
          <section className="card">
            <p className="muted">加载 Track…</p>
          </section>
        )}

        {selected && (
          <div className="panel-stack">
            <TicketView stage={selected} />

            {current ? (
              <>
                <WorkspaceView
                  instance={current}
                  busy={busy}
                  onStart={() =>
                    run(async () => {
                      await api.start(current.id)
                      await refreshInstances()
                    })
                  }
                />

                <ReviewView
                  instance={current}
                  submission={submission}
                  review={review}
                  busy={busy}
                  onSubmit={(scenario: GradingScenario) =>
                    run(async () => {
                      setSubmission(await api.submit(current.id, { scenario }))
                      setReview(null)
                      setDecision(null)
                      await refreshInstances()
                      setNotice('已提交并跑完阅卷桩。接下来发起评审。')
                    })
                  }
                  onReview={(scenario: ReviewScenario) =>
                    run(async () => {
                      const reviewOut = await api.review(submission!.id, { scenario })
                      setReview(reviewOut)
                      setDecision(null)
                      await refreshInstances()
                      setNotice('评审完成（占位后端）。可以取确定性结论了。')
                    })
                  }
                />

                <DecisionView
                  instance={current}
                  submission={submission}
                  review={review}
                  decision={decision}
                  busy={busy}
                  onDecide={() =>
                    run(async () => {
                      const out = await api.decide(submission!.id)
                      setDecision(out)
                      // 决策响应带全量实例：直接用它刷新看板，不再多发一轮请求。
                      setInstances(out.instances)
                      setNotice(
                        out.decision.verdict === 'pass'
                          ? '通过：下一阶段已解锁。'
                          : '未通过：可按评审批注返工后重来。',
                      )
                    })
                  }
                  onRetry={() =>
                    run(async () => {
                      await api.retry(current.id)
                      setSubmission(null)
                      setReview(null)
                      setDecision(null)
                      await refreshInstances()
                      setNotice('已回到「开发中」，可再走一遍提交 → 评审 → 决策。')
                    })
                  }
                />
              </>
            ) : (
              <section className="card">
                <p className="muted">
                  这个阶段还没有实例——先「入学」。
                </p>
              </section>
            )}
          </div>
        )}
      </main>
    </div>
  )
}

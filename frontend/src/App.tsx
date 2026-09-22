import { useEffect, useRef, useState } from 'react'

import { api, ApiError, STALE_RESOURCE_CODES } from './api'
import { DecisionView } from './components/DecisionView'
import { GradingView } from './components/GradingView'
import { HistoryView } from './components/HistoryView'
import { ReviewView } from './components/ReviewView'
import { SimulatedBadge } from './components/SimulatedBadge'
import { StageBoard } from './components/StageBoard'
import { TicketView } from './components/TicketView'
import { TrackSwitcher } from './components/TrackSwitcher'
import { WorkspaceView } from './components/WorkspaceView'
import type {
  DecisionOut,
  GradingProgressOut,
  GradingReportOut,
  GradingScenario,
  InstanceOut,
  ReviewOut,
  ReviewScenario,
  StageEntry,
  SubmissionOut,
  TrackDetail,
  TrackSummary,
} from './types'

/** 自托管演练所的唯一用户（部署者本人）。 */
const LEARNER = 'deployer'

/** 轮询间隔。桩是瞬时的，真阅卷几十秒——700ms 既不刷爆后端，也不会让人觉得卡住。 */
const POLL_MS = 700

export default function App() {
  const [tracks, setTracks] = useState<TrackSummary[]>([])
  const [track, setTrack] = useState<TrackDetail | null>(null)
  const [instances, setInstances] = useState<InstanceOut[]>([])
  const [selected, setSelected] = useState<StageEntry | null>(null)

  // 当前阶段这条链上的产物：提交 → 阅卷（进度 + 报告）→ 评审 → 决策。换阶段就清空。
  const [submission, setSubmission] = useState<SubmissionOut | null>(null)
  const [progress, setProgress] = useState<GradingProgressOut | null>(null)
  const [report, setReport] = useState<GradingReportOut | null>(null)
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
      const message = e instanceof Error ? e.message : String(e)
      // 本地那份实例已经不在了（后端重启过 / 数据被重置 / 实例被重建）：光报错没用，
      // 界面上那个实例是个幻影，点它只会再 404。**刷新看板 + 清掉选中项**才是恢复动作。
      if (e instanceof ApiError && STALE_RESOURCE_CODES.includes(e.detail) && track) {
        selectStage(null) // 顺手清掉 error/notice，所以下面两个 set 必须放在它**之后**
        await refreshInstances(track.id).catch(() => {})
        setError(message) // 「为什么」留在错误条里，不要只留一句"已刷新"
        setNotice('看板已刷新——重新点一个阶段继续。')
      } else {
        setError(message)
      }
    } finally {
      setBusy(false)
    }
  }

  // 实例按 Track 过滤：两个 Track 可以有同名 ticket id，不过滤就会把 A 的进度画进 B 的看板。
  const refreshInstances = (trackId: string) =>
    api.listInstances(LEARNER, trackId).then(setInstances)

  // 轮询的"代"号：换阶段/换 Track 或重新提交都 +1，在飞的那轮轮询看到代号变了就自己停下。
  // 不加这个，切走之后旧轮询还会往界面上写它那个阶段的进度。
  const pollGeneration = useRef(0)
  // 历史是"提交"的产物：每提交一次 / 每判一次都 +1，让历史面板重新拉一次。
  const [historyKey, setHistoryKey] = useState(0)

  /** 提交后的轮询：把阶段进度一路画到报告就绪（或失败/无记录）。 */
  async function pollGrading(submissionId: string, generation: number) {
    for (;;) {
      const p = await api.progress(submissionId)
      if (pollGeneration.current !== generation) return
      setProgress(p)
      if (p.status === 'failed') return
      if (p.report_ready) {
        setReport(await api.report(submissionId))
        // 报告到手 ⇒ 历史里那一条也从"在跑"变成"有报告"，让它重拉一次。
        setHistoryKey((n) => n + 1)
        return
      }
      if (p.status === 'unknown') return
      await new Promise((resolve) => setTimeout(resolve, POLL_MS))
    }
  }

  useEffect(() => {
    let cancelled = false
    void run(async () => {
      const list = await api.listTracks()
      const first = list[0]
      if (cancelled) return
      setTracks(list)
      if (!first) return
      setTrack(await api.getTrack(first.id))
      if (!cancelled) setInstances(await api.listInstances(LEARNER, first.id))
    })
    return () => {
      cancelled = true
    }
    // 只在挂载时拉一次；后续刷新都走具体动作。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function selectTrack(trackId: string) {
    if (track?.id === trackId) return
    void run(async () => {
      // 切 Track 必须清空选中项与整条产物链：两个 Track 的同名 stage / 同名 ticket id
      // 会让"看板是 A、题目是 B"这种错悄悄发生。
      selectStage(null)
      setInstances([])
      setTrack(await api.getTrack(trackId))
      await refreshInstances(trackId)
    })
  }

  /** 清空"当前阶段这条链"的全部产物，并让在飞的轮询失效。 */
  function clearChain() {
    pollGeneration.current += 1
    setSubmission(null)
    setProgress(null)
    setReport(null)
    setReview(null)
    setDecision(null)
  }

  function selectStage(stage: StageEntry | null) {
    clearChain()
    setSelected(stage)
    setNotice(null)
    setError(null)
  }

  const current = selected ? instanceOf(selected) : undefined

  /**
   * 已加载 Track 的整屏内容。
   *
   * 收成一个**参数非空**的函数而不是直接内联：里面的每个动作都在异步闭包里用到 `track.id`，
   * 而 TS 不会把 JSX 条件表达式里的非空收窄带进这些闭包——于是要么处处 `!`（把"真可能为空"
   * 压掉），要么把非空这件事在类型上一次性说清。这里选后者。
   */
  function pane(t: TrackDetail) {
    return (
      <>
        {/* 左栏：去哪（导航）。窄、紧凑、一屏看完整条解锁链。 */}
        <aside className="rail rail--nav">
          <StageBoard
            track={t}
            stateOf={instanceOf}
            selectedStage={selected?.stage ?? null}
            onSelect={selectStage}
            onEnroll={() =>
              run(async () => {
                const res = await api.enroll(LEARNER, t.id)
                setInstances(res.instances)
                setNotice('已入学：根阶段可开始，其后阶段保持未解锁。')
              })
            }
            busy={busy}
          />
        </aside>

        {/* 中栏：做什么 + 结果（读题、git、提交、阅卷、决策都在这一列按流程往下排）。 */}
        <main className="work">
          {selected ? (
            <>
              <TicketView stage={selected} trackId={t.id} />

              {current ? (
                <>
                  <WorkspaceView
                    instance={current}
                    busy={busy}
                    onStart={() =>
                      run(async () => {
                        await api.start(current.id)
                        await refreshInstances(t.id)
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
                        // 提交是异步的（202）：先把 submission 与进度画出来，再等轮询。
                        const accepted = await api.submit(current.id, { scenario })
                        pollGeneration.current += 1
                        const generation = pollGeneration.current
                        setSubmission(accepted.submission)
                        setProgress(accepted.progress)
                        setReport(null)
                        setReview(null)
                        setDecision(null)
                        await refreshInstances(t.id)
                        setNotice('已受理：阅卷在后台跑，进度见下。')
                        setHistoryKey((n) => n + 1)
                        // 不 await：轮询自己会更新进度，界面不该被它挂住。
                        void pollGrading(accepted.submission.id, generation).catch(
                          (e: unknown) => {
                            if (pollGeneration.current === generation) {
                              setError(e instanceof Error ? e.message : String(e))
                            }
                          },
                        )
                      })
                    }
                    onReview={(scenario: ReviewScenario) =>
                      run(async () => {
                        const reviewOut = await api.review(submission!.id, { scenario })
                        setReview(reviewOut)
                        setDecision(null)
                        await refreshInstances(t.id)
                        setNotice('评审完成（占位后端）。可以取确定性结论了。')
                      })
                    }
                  />

                  <GradingView submission={submission} progress={progress} report={report} />

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
                        // 决策响应带"这个 learner 的全部实例"：按当前 Track 过滤后再刷新看板。
                        setInstances(out.instances.filter((i) => i.track === t.id))
                        setHistoryKey((n) => n + 1)
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
                        clearChain()
                        await refreshInstances(t.id)
                        setNotice('已回到「开发中」，可再走一遍提交 → 评审 → 决策。')
                      })
                    }
                  />
                </>
              ) : (
                <section className="card">
                  <p className="muted">这个阶段还没有实例——先在左侧「入学」。</p>
                </section>
              )}
            </>
          ) : (
            <section className="card empty">
              <h2 className="card__title">选一个阶段开始</h2>
              <p className="muted">
                左边是这条 Track 的解锁链。点一个<strong>已出卷</strong>阶段，题面会出现在这里。
              </p>
            </section>
          )}
        </main>

        {/* 右栏：判到哪了 + 记录（窄栏，扫一眼就够；宽内容留在中栏）。 */}
        <aside className="rail rail--state">
          {current && (
            <section className="card status-card" data-testid="instance-status">
              <h2 className="card__title">当前实例</h2>
              <dl className="kv kv--tight">
                <dt>工单</dt>
                <dd>
                  <code>{current.ticket_id}</code>
                </dd>
                <dt>实例</dt>
                <dd>
                  <code>{current.id}</code>
                </dd>
              </dl>
              <p className="muted">
                工单 v{current.ticket_version} · 测试 v{current.test_version} · 引擎锚{' '}
                {current.engine_version}
              </p>
            </section>
          )}

          {current && <HistoryView instanceId={current.id} refreshKey={historyKey} />}

          {!current && (
            <section className="card">
              <h2 className="card__title">历史尝试</h2>
              <p className="muted">选中一个阶段后，这里显示它的每一次提交与阅卷记录。</p>
            </section>
          )}
        </aside>
      </>
    )
  }

  return (
    <div className={`app${track ? '' : ' app--boot'}`}>
      <header className="topbar">
        <div className="topbar__brand">
          <h1 className="topbar__title">八股实战演练所</h1>
          <p className="topbar__tagline">
            内容（Track）+ git + 本地阅卷 + 确定性门。平台只 <strong>gate + 指示</strong>
            ；被评分的代码由你在本地亲手写。
          </p>
        </div>
        <div className="topbar__side">
          <TrackSwitcher
            tracks={tracks}
            currentId={track?.id ?? ''}
            onSelect={selectTrack}
            busy={busy}
          />
          <p className="topbar__meta">
            部署者即唯一用户 · <SimulatedBadge />
            {/* 不再笼统说"三处均为占位后端"：真假**逐项**看每处的「模拟」徽标
                （真阅卷的报告带 simulated:false，不盖这个章）。 */}
            各环节真假如实标注：见每处的「模拟」徽标
          </p>
        </div>
      </header>

      {(error || notice) && (
        <div className="app__flash">
          {error && (
            <div className="alert alert--error" role="alert">
              {error}
            </div>
          )}
          {notice && <div className="alert alert--info">{notice}</div>}
        </div>
      )}

      {track ? (
        pane(track)
      ) : (
        <main className="work">
          <section className="card">
            <p className="muted">加载 Track…</p>
          </section>
        </main>
      )}
    </div>
  )
}

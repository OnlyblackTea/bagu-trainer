import { useEffect, useRef, useState } from 'react'

import { api, ApiError, STALE_RESOURCE_CODES } from './api'
import { stateLabel } from './labels'
import { DecisionView } from './components/DecisionView'
import { GradingView } from './components/GradingView'
import { HistoryView } from './components/HistoryView'
import { HomeView } from './components/HomeView'
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

/**
 * 同一 ticket 可能有多个实例（**重做/重发**后旧的留档成 `stale`）：只认活着的那一个。
 *
 * 取"第一个匹配"会把界面钉死在作废实例上——按钮全 409，而新实例就在列表里。全 stale 时退回最新
 * 一个，好让看板仍显示"已作废"，而不是让那个阶段凭空消失。
 */
export function liveInstanceOf(
  instances: InstanceOut[],
  ticketId: string | null | undefined,
): InstanceOut | undefined {
  if (!ticketId) return undefined
  const mine = instances.filter((i) => i.ticket_id === ticketId)
  return mine.find((i) => i.state !== 'stale') ?? mine[mine.length - 1]
}

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
  /** 「重做这一题」是作废性动作，走两步：先出确认块，再执行。 */
  const [confirmReissue, setConfirmReissue] = useState(false)

  /**
   * 这个阶段当前该操作哪个实例。
   *
   * 同一个 ticket 可以同时存在多个实例——**重做/重发**之后旧的会留档成 `stale`。所以不能"取第一个
   * 匹配 ticket 的"：那会把界面永久钉在那个已经作废的实例上（点任何按钮都是 409），而新实例明明
   * 就在列表里。规则：非 stale 的那个优先；全 stale 时退回最新的一个（让看板还能显示"已作废"，
   * 而不是凭空少一个阶段）。
   */
  const instanceOf = (stage: StageEntry): InstanceOut | undefined =>
    liveInstanceOf(instances, stage.ticket_id)

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

  /**
   * **从服务端把这条链恢复出来**（"刷新页面"不能把人卡死）。
   *
   * 为什么必须有：`submission/report/review/decision` 原本只活在内存里，而界面上每一步的可用性
   * 都挂在它们身上——刷新（或换阶段再回来）之后它们是 `null`，于是「发起 AI 评审」按不动
   * （`canReview` 要 submission）、「取确定性结论」也按不动（要 review），而提交按钮又要求
   * `in_progress`……**整个界面就没有一个能按的按钮**。真发生过：学员交卷拿到红报告，刷新之后
   * 卡在那一屏，什么也做不了。
   *
   * 数据本来就有：`GET /instances/{id}/history` 的每一条 attempt 都带提交、运行、报告、评审、
   * 判定（`HistoryView` 一直在用它）。这里取**最新一条**塞回链上。顺带把"还在跑"的那次阅卷重新
   * 挂上轮询——否则刷新之后进度条永远停在原地。
   */
  async function rehydrateFlow(instanceId: string) {
    const history = await api.history(instanceId)
    const latest = history.attempts[history.attempts.length - 1]
    if (!latest) return
    setSubmission(latest.submission)
    setReport(latest.report ?? null)
    setReview(latest.review ?? null)
    // 历史里给的是**判定本身**，而决策动作的返回值是 `{decision, instances}`（那份 instances 是
    // 决策时顺手刷新看板用的）。这里补一个空的 instances：看板由 refreshInstances 负责。
    setDecision(latest.decision ? { decision: latest.decision, instances: [] } : null)
    setProgress(latest.grading ?? null)
    const grading = latest.grading
    if (latest.submission && !latest.report && grading && grading.status !== 'failed') {
      // 还在跑（或被打断）：重新挂上轮询，让进度条继续走。
      const generation = ++pollGeneration.current
      void pollGrading(latest.submission.id, generation).catch(() => {})
    }
  }

  // 选中阶段（或它换了个实例）就把链恢复出来。用 id 做依赖：换实例必须重新取一次。
  const currentId = selected ? instanceOf(selected)?.id : null
  useEffect(() => {
    if (!currentId) return
    void rehydrateFlow(currentId).catch(() => {
      // 恢复失败不该打断使用者：下面那些按钮本来就是"可选动作"，不是数据来源。
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId])

  function selectStage(stage: StageEntry | null) {
    clearChain()
    setSelected(stage)
    setNotice(null)
    setError(null)
    setConfirmReissue(false)
  }

  /**
   * 重做这一题：把当前实例作废（留档）+ 领一个新实例。
   *
   * 刷新后 `instanceOf` 会自动指向新实例（它不 stale），所以**不需要**在这里改选中项；旧实例的
   * 提交/报告/判定仍留在它的历史里（`HistoryView` 按实例 id 取，选中新实例就看新实例）。
   */
  function reissueInstance(instanceId: string, trackId: string) {
    void run(async () => {
      const out = await api.reissue(instanceId)
      clearChain()
      await refreshInstances(trackId)
      setNotice(
        `已重发 ${out.instance.id}（从该阶段初始态重新开始）；` +
          `${out.previous.id} 留档为「已作废」，它的提交与报告仍可查。`,
      )
    })
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
    /**
     * 已解锁的下一题：「进入下一题 →」的落点。
     *
     * 取第一个**可开始**（`available`）的已出卷阶段——那正是 DAG 刚解锁的那一个。
     * 找不到就不渲染那个按钮（没有下一题时给一个点了没反应的按钮最糟）。
     */
    const nextUnlocked = t.stages.find(
      (s) =>
        s.kind !== 'board_only' && s.stage !== selected?.stage && instanceOf(s)?.state === 'available',
    )

    return (
      <>
        {/* 左栏：去哪（导航）。窄、紧凑、一屏看完整条解锁链。 */}
        <aside className="rail rail--nav">
          <StageBoard
            track={t}
            stateOf={instanceOf}
            selectedStage={selected?.stage ?? null}
            onSelect={selectStage}
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
                    report={report}
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
                        // 提交前平台与远端对过一次账：学员在**别的机器**上写完 push 之后，这次阅卷
                        // 到底看的是哪一版，必须当场说清——"跑了但跑的是旧代码"是最难自查的一种结果。
                        const sync = accepted.sync
                        if (sync?.remote_only_changes) {
                          setError(
                            `阅卷已受理，但它看的可能不是你最新的代码：${sync.note}` +
                              '（工作树干净时平台会自动快进；这次没有，所以按本地内容走。）',
                          )
                        }
                        setNotice('已受理：阅卷在后台跑，进度见下。')
                        if (sync?.fast_forwarded) {
                          setNotice('已受理：这次阅卷看的是远端最新的 dev（提交前已自动同步）。')
                        }
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
                    report={report}
                    busy={busy}
                    nextStage={
                      nextUnlocked ? { stage: nextUnlocked.stage, title: nextUnlocked.title } : null
                    }
                    onGoNext={() => selectStage(nextUnlocked ?? null)}
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
                  <p className="muted">这个阶段还没有实例——回到主页点「入学」。</p>
                </section>
              )}
            </>
          ) : (
            <HomeView
              track={t}
              learner={LEARNER}
              stateOf={instanceOf}
              onSelect={selectStage}
              onEnroll={() =>
                run(async () => {
                  const res = await api.enroll(LEARNER, t.id)
                  setInstances(res.instances)
                  setHistoryKey((n) => n + 1)
                  setNotice('已入学：根阶段可开始，其后阶段保持未解锁。')
                })
              }
              busy={busy}
              refreshKey={historyKey}
            />
          )}
        </main>

        {/* 右栏：判到哪了 + 记录（窄栏，扫一眼就够；宽内容留在中栏）。 */}
        <aside className="rail rail--state">
          {current && (
            <section className="card status-card" data-testid="instance-status">
              <h2 className="card__title">
                当前实例
                {/* 状态词只有一套（labels.ts）：左栏看板、工作区、决策结果与这里说的是同一个词。 */}
                <span className={`badge badge--${current.state}`}>{stateLabel(current.state)}</span>
              </h2>
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
              {current.state === 'stale' ? (
                <p className="muted">这个实例已作废（重做过或内容漂移）——去下面选新发的那个。</p>
              ) : confirmReissue ? (
                <div className="hint-block" data-testid="reissue-confirm">
                  <p className="muted">
                    重做会把 <code>{current.id}</code> 作废（留档，提交与报告都还在历史里），并重发一个
                    同版本的新实例，从该阶段的初始态重新开始。你自己登记的 workspace 目录会被继承。
                  </p>
                  <div className="row">
                    <button
                      className="primary"
                      data-testid="reissue-confirm-yes"
                      disabled={busy}
                      onClick={() => {
                        setConfirmReissue(false)
                        reissueInstance(current.id, t.id)
                      }}
                    >
                      确认重做
                    </button>
                    <button
                      data-testid="reissue-confirm-no"
                      disabled={busy}
                      onClick={() => setConfirmReissue(false)}
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  data-testid="reissue"
                  title="交卷后想接着改、或这一关的结论不算数（桩/误判）时用它"
                  disabled={busy}
                  onClick={() => setConfirmReissue(true)}
                >
                  重做这一题
                </button>
              )}
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

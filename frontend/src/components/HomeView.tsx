import { useEffect, useState } from 'react'

import { api } from '../api'
import { KIND_LABEL, stateLabel } from '../labels'
import type {
  AttemptSummaryOut,
  InstanceOut,
  MetaOut,
  StageEntry,
  TrackDetail,
} from '../types'
import { EXPECTED_PLATFORM_VERSION } from '../version'

interface Props {
  track: TrackDetail
  learner: string
  stateOf: (stage: StageEntry) => InstanceOut | undefined
  onSelect: (stage: StageEntry) => void
  onEnroll: () => void
  busy: boolean
  /** 每次提交/判定后 +1，用来重拉"最近练习"。 */
  refreshKey: number
}

/** 一眼看出"这题现在该干什么"。与左栏看板的徽标口径一致，只是这里给一句人话。 */
function nextAction(instance: InstanceOut | undefined, boardOnly: boolean): string {
  if (boardOnly) return '只上榜、不出卷（任务书在 Track 仓库的 TASKS/ 里）'
  if (!instance) return '先「入学」'
  switch (instance.state) {
    case 'available':
      return '可开始'
    case 'in_progress':
      return '开发中——改完本地代码再「提交」'
    case 'submitted':
      return '已提交——等阅卷跑完，再发起评审'
    case 'grading':
      return '评审完成——可以「取确定性结论」了'
    case 'passed':
      return '已通过'
    case 'failed':
      return '未通过——按评审意见返工，再「返工重来」'
    case 'stale':
      return '已作废（版本漂移）'
    default:
      return '未解锁——先通过前置阶段'
  }
}

function seconds(ms: number | null): string {
  if (ms === null) return ''
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

function when(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

/**
 * 屏幕 0：主页 / 总览（设计 spec §C.3 步骤 1-2）。
 *
 * 打开平台先看到"我练到哪了、下一题是哪道、最近几次结果如何"，而不是一句"选一个阶段开始"。
 * 三块，按"决策顺序"排：
 *
 * 1. **进度**：这个 Track 通过了几关、解题线在哪；
 * 2. **下一题**（一个显眼的行动入口）+ **题目列表**（自己挑，而不是被推着走）；
 * 3. **最近练习**：跨实例、跨 Track 的最近几次提交与结论（`GET /attempts`）——"我上次干到哪了"
 *    是跨 Track 的问题，所以由后端合并后再给，前端不循环请求。
 */
export function HomeView({
  track,
  learner,
  stateOf,
  onSelect,
  onEnroll,
  busy,
  refreshKey,
}: Props) {
  const [recent, setRecent] = useState<AttemptSummaryOut[] | null>(null)
  const [meta, setMeta] = useState<MetaOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .attempts(learner, 8)
      .then((r) => {
        if (!cancelled) {
          setRecent(r.attempts)
          setError(null)
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    // 后端自述：进度到底存哪、重启会不会丢——学员有权知道，而不是"应该保存了吧"。
    api
      .meta()
      .then((m) => {
        if (!cancelled) setMeta(m)
      })
      .catch(() => {
        /* /meta 拿不到不影响做题；宁可少一句说明，也不要因此挡住主流程 */
      })
    return () => {
      cancelled = true
    }
  }, [learner, refreshKey])

  const assessed = track.stages.filter((s) => s.kind !== 'board_only')
  const passed = assessed.filter((s) => stateOf(s)?.state === 'passed').length
  const enrolled = track.stages.some((s) => stateOf(s) !== undefined)
  const next = assessed.find((s) => {
    const state = stateOf(s)?.state
    return state !== 'passed' && state !== 'stale'
  })
  const nextInstance = next ? stateOf(next) : undefined

  return (
    <div className="home" data-testid="home-view">
      <section className="card home__progress">
        <h2 className="card__title">
          {track.name}
          <span className="badge">{track.id}</span>
          <span className="badge">信任级别 {track.trust_level}</span>
        </h2>
        <div className="board-progress">
          <div className="board-progress__bar board-progress__bar--lg">
            <span style={{ width: `${assessed.length ? (passed / assessed.length) * 100 : 0}%` }} />
          </div>
          <span className="muted">
            已通过 {passed} / {assessed.length} 阶段
            {passed === assessed.length && assessed.length > 0 && ' · 这条 Track 全通了 🎉'}
          </span>
        </div>

        {!enrolled && (
          <div className="hint-block">
            <p className="muted">
              还没入学。入学后平台会为每个已出卷阶段铸造一个实例，进度从这里开始记。
            </p>
            <button
              className="primary"
              data-testid="enroll"
              onClick={onEnroll}
              disabled={busy}
            >
              入学（{learner}）
            </button>
          </div>
        )}

        {enrolled && next && (
          <div className="home__next" data-testid="home-next">
            <div>
              <div className="muted">下一题</div>
              <div className="home__next-title">
                {next.title ?? next.stage}
                <span className="badge">{next.ticket_id ?? next.stage}</span>
              </div>
              <div className="muted">{nextAction(nextInstance, false)}</div>
            </div>
            <button className="primary" onClick={() => onSelect(next)} disabled={busy}>
              {nextInstance?.state === 'in_progress' ? '继续这一题' : '打开这一题'}
            </button>
          </div>
        )}
      </section>

      <section className="card" data-testid="home-problems">
        <h2 className="card__title">
          题目
          <span className="badge">{assessed.length} 道已出卷</span>
          {track.stages.length > assessed.length && (
            <span className="badge">{track.stages.length - assessed.length} 个只上榜</span>
          )}
        </h2>
        <ul className="problems">
          {track.stages.map((stage) => {
            const instance = stateOf(stage)
            const boardOnly = stage.kind === 'board_only'
            return (
              <li key={stage.stage}>
                <button
                  type="button"
                  className="problem"
                  data-testid={`home-problem-${stage.stage}`}
                  disabled={boardOnly || busy}
                  onClick={() => onSelect(stage)}
                >
                  <span className="problem__title">
                    {stage.title ?? '（未出卷）'}
                    <span className="stage__dir">{stage.stage}</span>
                  </span>
                  <span className="problem__state">
                    {boardOnly ? (
                      <span className="badge badge--board_only">{KIND_LABEL.board_only}</span>
                    ) : instance ? (
                      <span className={`badge badge--${instance.state}`}>
                        {stateLabel(instance.state)}
                      </span>
                    ) : (
                      <span className="badge">未入学</span>
                    )}
                    <span className="muted">{nextAction(instance, boardOnly)}</span>
                  </span>
                  {stage.prerequisites.length > 0 && (
                    <span className="stage__prereq">前置：{stage.prerequisites.join('、')}</span>
                  )}
                </button>
              </li>
            )
          })}
        </ul>
      </section>

      <section className="card" data-testid="home-recent">
        <h2 className="card__title">最近练习</h2>
        {error && <div className="alert alert--error">{error}</div>}
        {!error && recent === null && <p className="muted">加载中…</p>}
        {!error && recent?.length === 0 && (
          <p className="muted">还没有提交记录——挑一道题开始，这里会留下每次提交与结论。</p>
        )}
        {!error && recent && recent.length > 0 && (
          <ul className="attempts attempts--compact">
            {recent.map((a) => (
              <li className="attempt" key={a.submission_id} data-testid="home-attempt">
                <div className="row">
                  <span className="badge">第 {a.seq} 次</span>
                  <code>{a.ticket_id}</code>
                  {a.verdict ? (
                    <span className={`badge badge--${a.verdict === 'pass' ? 'passed' : 'failed'}`}>
                      {a.verdict === 'pass' ? '通过' : '未通过'}
                    </span>
                  ) : (
                    <span className="badge badge--stale">未判定</span>
                  )}
                  {a.tampering > 0 && (
                    <span className="badge badge--failed">判定面改动 {a.tampering} 处</span>
                  )}
                  {a.elapsed_ms !== null && <span className="badge">阅卷 {seconds(a.elapsed_ms)}</span>}
                  <span className="muted">{when(a.created_at)}</span>
                </div>
                <div className="muted">
                  当前状态：{stateLabel(a.state)}
                  {a.correctness_passed === false && ' · 阅卷红'}
                  {a.correctness_passed === true && ' · 阅卷绿'}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {meta && (
        <section className="card meta-card" data-testid="home-meta">
          <h2 className="card__title">这台服务</h2>
          {meta.platform_version !== EXPECTED_PLATFORM_VERSION && (
            <div className="alert alert--error" data-testid="version-skew">
              前端是 <code>{EXPECTED_PLATFORM_VERSION}</code>，后端自述是{' '}
              <code>{meta.platform_version}</code>：两边不是同一次部署。旧后端没有新接口，症状就是
              「后端没有这个接口」（HTTP 404）。请拉最新代码后重新部署并重启后端，再刷新本页。
            </div>
          )}
          <dl className="kv kv--tight">
            <dt>版本</dt>
            <dd>
              <span className="badge">前端 {EXPECTED_PLATFORM_VERSION}</span>
              <span className={`badge${meta.platform_version === EXPECTED_PLATFORM_VERSION ? '' : ' badge--failed'}`}>
                后端 {meta.platform_version}
              </span>
            </dd>
            <dt>进度存在</dt>
            <dd>
              <code>{meta.store_target}</code>
            </dd>
            <dt>存储后端</dt>
            <dd>
              <span className="badge">{meta.store_backend}</span>
              <span className="muted">
                {meta.store_backend === 'memory'
                  ? '重启即失忆——只适合临时演示'
                  : '重启后进度还在'}
              </span>
            </dd>
            <dt>阅卷</dt>
            <dd>
              <span className="badge">{meta.grading_backend}</span>
              <span className="muted">
                {meta.grading_backend === 'stub' ? '桩：预置红绿' : '真跑 Track 的 canonical 测试'}
              </span>
            </dd>
            <dt>Forge</dt>
            <dd>
              <span className="badge">{meta.forge_backend}</span>
              <span className="muted">
                {meta.forge_backend === 'stub' ? '桩：本地 clone + 占位 PR' : '真 Gitea：真 fork/PR'}
              </span>
            </dd>
          </dl>
          <p className="muted">部署指引见 <code>docs/deployment.md</code>。</p>
        </section>
      )}
    </div>
  )
}

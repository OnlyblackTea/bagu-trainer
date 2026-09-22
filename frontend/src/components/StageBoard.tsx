import { KIND_LABEL, stateLabel } from '../labels'
import type { InstanceOut, StageEntry, TrackDetail } from '../types'

interface Props {
  track: TrackDetail
  stateOf: (stage: StageEntry) => InstanceOut | undefined
  selectedStage: string | null
  onSelect: (stage: StageEntry) => void
}

/**
 * 左侧栏：Track 总览与阶段看板（spec §C.6）。
 *
 * 这一栏是**导航**，不是内容：所以它是窄栏、紧凑行、一屏能看完整条解锁链，读者的眼睛可以一直
 * 停在右边真正要读的东西上。当前选中的阶段高亮；**第一个还没通过的已出卷阶段**标"下一步"——
 * 学员最常问的就是"我现在该做哪个"。
 *
 * `kind` 二分的意义在这里落地：`board_only` 阶段是"只上榜、不出卷"（真 Track 的
 * 环境入职阶段就如此），它**没有** `ticket_id`，所以既不可点进发卷、也不该被拿去取卷——
 * 取卷必须用 `identity.id`，拿 stage 目录名去取会 404。
 */
export function StageBoard({ track, stateOf, selectedStage, onSelect }: Props) {
  const assessed = track.stages.filter((s) => s.kind !== 'board_only')
  const passed = assessed.filter((s) => stateOf(s)?.state === 'passed').length
  const enrolled = track.stages.some((s) => stateOf(s) !== undefined)
  // "下一步"= 第一个既没通过、也没作废的已出卷阶段；它右边的状态决定了该做什么。
  const nextUp = assessed.find((s) => {
    const state = stateOf(s)?.state
    return state !== 'passed' && state !== 'stale'
  })

  return (
    <section className="card board-card" data-testid="board">
      <header className="board-card__head">
        <h2 className="board-card__title">{track.name}</h2>
        <div className="board-card__meta">
          <span className="badge">{track.id}</span>
          <span className="badge">信任级别 {track.trust_level}</span>
        </div>
      </header>

      <div className="board-progress" aria-label="通关进度">
        <div className="board-progress__bar">
          <span style={{ width: `${assessed.length ? (passed / assessed.length) * 100 : 0}%` }} />
        </div>
        <span className="muted">
          已通过 {passed} / {assessed.length} 阶段
        </span>
      </div>

      {!enrolled && (
        <p className="muted board-card__enroll" data-testid="board-enroll-hint">
          还没入学——到<strong>主页</strong>点「入学」，平台会为每个已出卷阶段铸造一个实例。
        </p>
      )}

      <nav className="board">
        {track.stages.map((stage) => {
          const instance = stateOf(stage)
          const boardOnly = stage.kind === 'board_only'
          const selected = selectedStage === stage.stage
          const isNext = nextUp?.stage === stage.stage

          const body = (
            <>
              <div className="stage__line">
                <span className="stage__name">
                  {stage.title ?? <span className="muted">（未出卷）</span>}
                </span>
                {boardOnly && (
                  <span className="badge badge--board_only">{KIND_LABEL.board_only}</span>
                )}
                {instance && (
                  <span className={`badge badge--${instance.state}`}>
                    {stateLabel(instance.state)}
                  </span>
                )}
                {!boardOnly && !instance && <span className="badge">{KIND_LABEL.assessed}</span>}
                {isNext && enrolled && <span className="badge badge--next">下一步</span>}
              </div>
              <div className="stage__dir">{stage.stage}</div>
              {stage.prerequisites.length > 0 && (
                <div className="stage__prereq">前置：{stage.prerequisites.join('、')}</div>
              )}
            </>
          )

          if (boardOnly) {
            return (
              <div
                key={stage.stage}
                className="stage stage--board_only"
                data-testid={`stage-${stage.stage}`}
              >
                {body}
              </div>
            )
          }

          return (
            <button
              key={stage.stage}
              type="button"
              className={`stage${selected ? ' stage--selected' : ''}`}
              data-testid={`stage-${stage.stage}`}
              aria-current={selected ? 'true' : undefined}
              onClick={() => onSelect(stage)}
            >
              {body}
            </button>
          )
        })}
      </nav>
    </section>
  )
}

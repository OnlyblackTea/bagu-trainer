import { KIND_LABEL, stateLabel } from '../labels'
import type { InstanceOut, StageEntry, TrackDetail } from '../types'

interface Props {
  track: TrackDetail
  stateOf: (stage: StageEntry) => InstanceOut | undefined
  selectedStage: string | null
  onSelect: (stage: StageEntry) => void
  onEnroll: () => void
  busy: boolean
}

/**
 * 屏幕 1：Track 总览（spec §C.6）。
 *
 * `kind` 二分的意义在这里落地：`board_only` 阶段是"只上榜、不出卷"（真 Track 的
 * 环境入职阶段就如此），它**没有** `ticket_id`，所以既不可点进发卷、也不该被拿去取卷——
 * 取卷必须用 `identity.id`，拿 stage 目录名去取会 404。
 */
export function StageBoard({
  track,
  stateOf,
  selectedStage,
  onSelect,
  onEnroll,
  busy,
}: Props) {
  const enrolled = track.stages.some((s) => stateOf(s) !== undefined)

  return (
    <section className="card">
      <h2 className="card__title">
        {track.name}
        <span className="badge">{track.id}</span>
        <span className="badge">信任级别 {track.trust_level}</span>
      </h2>

      {!enrolled && (
        <div className="row" style={{ marginBottom: 12 }}>
          <button className="primary" onClick={onEnroll} disabled={busy}>
            入学（deployer）
          </button>
          <span className="muted">入学后会为每个已出卷阶段铸造一个实例。</span>
        </div>
      )}

      <div className="board">
        {track.stages.map((stage) => {
          const instance = stateOf(stage)
          const boardOnly = stage.kind === 'board_only'
          const selected = selectedStage === stage.stage

          const body = (
            <>
              <div>
                <div className="stage__name">
                  {stage.title ?? <span className="muted">（未出卷）</span>}
                </div>
                <div className="stage__dir">{stage.stage}</div>
              </div>
              <div className="row">
                {boardOnly && <span className="badge badge--board_only">{KIND_LABEL.board_only}</span>}
                {instance && (
                  <span className={`badge badge--${instance.state}`}>
                    {stateLabel(instance.state)}
                  </span>
                )}
                {!boardOnly && !instance && <span className="badge">{KIND_LABEL.assessed}</span>}
              </div>
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
              onClick={() => onSelect(stage)}
            >
              {body}
            </button>
          )
        })}
      </div>
    </section>
  )
}

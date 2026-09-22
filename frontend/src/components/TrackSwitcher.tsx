import type { TrackSummary } from '../types'

interface Props {
  tracks: TrackSummary[]
  currentId: string
  onSelect: (trackId: string) => void
  busy: boolean
}

/**
 * 选方向（spec §C.3 步骤 1）。
 *
 * 只在部署了**多个** Track 时出现——单 Track 时一个"只有一项的下拉"只是噪音。
 *
 * 切换会清掉当前选中的阶段：两个 Track 可以有同名 stage 与同名 ticket id，留着选中项会让
 * 看板显示 A 的进度、题目却是 B 的（这正是多 Track 最容易骗过眼睛的那种错）。
 */
export function TrackSwitcher({ tracks, currentId, onSelect, busy }: Props) {
  if (tracks.length <= 1) return null

  return (
    <nav className="track-switcher" data-testid="track-switcher" aria-label="选择 Track">
      {tracks.map((t) => (
        <button
          key={t.id}
          type="button"
          className={`chip${t.id === currentId ? ' chip--on' : ''}`}
          aria-pressed={t.id === currentId}
          disabled={busy}
          onClick={() => onSelect(t.id)}
        >
          {t.name}
          <span className="badge">{t.assessed_stages} 卷</span>
          {t.board_only_stages > 0 && <span className="badge">{t.board_only_stages} 上榜</span>}
        </button>
      ))}
    </nav>
  )
}

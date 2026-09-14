import type { InstanceState, StageKind } from './types'

/** 状态 → 中文标签。短状态词是给机器的，标签是给人看的。 */
export const STATE_LABEL: Record<InstanceState, string> = {
  locked: '未解锁',
  available: '可开始',
  in_progress: '开发中',
  submitted: '已提交',
  grading: '评审中',
  passed: '已通过',
  failed: '未通过',
  stale: '已作废',
}

/** 看板条目类型标签。 */
export const KIND_LABEL: Record<StageKind, string> = {
  assessed: '已出卷',
  board_only: '未出卷',
}

export function stateLabel(state: InstanceState): string {
  return STATE_LABEL[state] ?? state
}

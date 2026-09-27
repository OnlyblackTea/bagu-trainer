import type { InstanceState, StageKind } from './types'

/**
 * 状态 → 中文标签。短状态词是给机器的，标签是给人看的。
 *
 * **这里是全站唯一的一套任务状态词**（设计 §1.3 的 `InstanceState` 状态机）：
 * 左栏看板、工作区、决策结果、右栏「当前实例」都从 `stateLabel` 取词。
 * 不要在别处再引入同义的说法（「进行中 / 处理中 / 待测试 / 待提交」）去表达这里已有的状态——
 * 同一个阶段在两个地方叫两个名字，学员就得自己猜它们是不是一回事。
 *
 * 自动测试那一步（`reset_infra` → `run_tests` → `parse`）的状态词在 `GradingView` 里，
 * 那是**步骤**轴，不是**任务**轴，所以刻意不混进这里。
 */
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

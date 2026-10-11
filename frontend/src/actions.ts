import type {
  DecisionOut,
  GradingReportOut,
  InstanceOut,
  ReviewOut,
  SubmissionOut,
} from './types'

/**
 * 「这一步能做什么」的**唯一**一处分身（右栏操作面板与文档区都读它）。
 *
 * 为什么把它抽出来：这些可用性条件原先散在 `ReviewView` / `DecisionView` 各自的行内按钮上。
 * 按界面分工把按钮挪到右栏之后，条件再写第二遍就会出现"文档说能交、面板按钮是灰的"这种
 * 自相矛盾——按钮换位置不该让规则跟着分身。
 */
export interface StageActions {
  /** 阶段还没开工（`available`）⇒ 只该看到「开始本阶段」。 */
  canStart: boolean
  /** 只有「开发中」能提交。 */
  canSubmit: boolean
  /** 已经交过一次、还没出结论 ⇒ 可以发起评审。 */
  canReview: boolean
  /** 测试已经红了（结论必然 fail）⇒ 主按钮改成「改完再交」。 */
  red: boolean
  canRectify: boolean
  /** 门只认"测试报告 + 评审意见"同时在场的 `grading` 态。 */
  canDecide: boolean
  /** `failed` ⇒ 可以返工重来（不清历史）。 */
  canRetry: boolean
  canGoNext: boolean
  /** 取确定性结论还缺什么（给学员看的自然语言，不是短码）。 */
  missing: string[]
}

export interface ActionInput {
  instance: InstanceOut | null
  submission: SubmissionOut | null
  report: GradingReportOut | null
  review: ReviewOut | null
  decision: DecisionOut | null
  nextStage: { stage: string; title?: string | null } | null
}

export function deriveActions({
  instance,
  submission,
  report,
  review,
  decision,
  nextStage,
}: ActionInput): StageActions {
  const state = instance?.state ?? null
  const red = report !== null && !report.correctness.passed
  const missing: string[] = []
  if (instance && !submission) missing.push('提交（跑自动测试）')
  if (instance && !review) missing.push('发起 AI 评审')

  return {
    canStart: state === 'available',
    canSubmit: state === 'in_progress',
    canReview: submission !== null && state === 'submitted',
    red,
    // 红的报告判定必然是 fail（门的第一路输入不成立），所以这两步合成一个动作。
    canRectify: red && submission !== null && state === 'submitted',
    canDecide: state === 'grading' && missing.length === 0 && decision === null,
    canRetry: state === 'failed',
    canGoNext: decision?.decision.verdict === 'pass' && nextStage !== null,
    missing,
  }
}

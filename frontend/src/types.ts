/**
 * 冻结 REST 契约的 TS 投影（Plan 1 产出，Plan 2 的 `2026-09-10-b-learner-spa.md` §2 表）。
 * 手写、不引 codegen：字段少、形状稳定，手写比多一条构建链更划算。
 */

export type InstanceState =
  | 'locked'
  | 'available'
  | 'in_progress'
  | 'submitted'
  | 'grading'
  | 'passed'
  | 'failed'
  | 'stale'

/** `assessed` = 有工单的出卷阶段；`board_only` = 只上榜、不出卷（如环境入职阶段）。 */
export type StageKind = 'assessed' | 'board_only'

export type TrustLevel = 'self' | 'verified' | 'community'
export type Recommendation = 'approve' | 'request_changes'
export type Verdict = 'pass' | 'fail'

export interface TrackSummary {
  id: string
  name: string
  language: string | null
  trust_level: TrustLevel
  stages: string[]
}

export interface StageEntry {
  /** stage 的**目录名**（= 分支名），不是工单身份。 */
  stage: string
  kind: StageKind
  /** 工单的 `identity.id`；`board_only` 阶段为 null。取卷必须用它，不能用上面那个。 */
  ticket_id: string | null
  stage_index: number | null
  title: string | null
  prerequisites: string[]
}

export interface TrackDetail {
  id: string
  name: string
  language: string | null
  trust_level: TrustLevel
  stages: StageEntry[]
}

export interface Hint {
  id: string
  text: string
}

export interface GraderView {
  test_group: string
  canonical_test: string
  expect: string | null
}

export interface Ticket {
  id: string
  stage: number
  title: string
  ticket_version: number
  problem: string
  concepts: string[]
  files_to_change: string[]
  hints: Hint[]
  grader: GraderView
}

export interface InstanceOut {
  id: string
  learner: string
  ticket_id: string
  ticket_version: number
  engine_version: string
  test_version: number
  state: InstanceState
  created_at: string
  /** 平台给出的"下一步该做什么 git 动作"指示（§C.3）。 */
  gate: string
}

export interface EnrollmentOut {
  enrollment: { id: string; learner: string; track: string; created_at: string }
  instances: InstanceOut[]
}

export interface WorkspaceOut {
  path: string
  branch: string
  base_ref: string
  head_ref: string
  simulated: boolean
}

export interface DiffOut {
  base_ref: string
  head_ref: string
  diff: string
  total_chars: number
  truncated_files: string[]
  simulated: boolean
}

export interface PullRequestOut {
  pr_url: string
  simulated: boolean
}

export interface SubmissionOut {
  id: string
  instance_id: string
  seq: number
  pr_url: string | null
  diff_ref: string | null
  defense_text: string | null
  created_at: string
}

export interface ReviewCommentOut {
  dimension: string
  /** 平台按锚定三元组构造的展示串，如 `src/Foo.java#L354`；无锚定时为 null。 */
  anchor: string | null
  text: string
  path: string | null
  line: number | null
  side: 'left' | 'right' | null
  quote: string | null
}

export interface DimensionFindingOut {
  dimension: string
  commentary: string
}

export interface ReviewOut {
  id: string
  submission_id: string
  recommendation: Recommendation
  comments: ReviewCommentOut[]
  summary: string
  dimension_findings: DimensionFindingOut[]
  truncated_files: string[]
  created_at: string
}

export interface DecisionOut {
  decision: {
    id: string
    submission_id: string
    inputs: {
      correctness_gate: boolean
      review_recommendation: Recommendation
      evidence_trust: TrustLevel
      /** 学员改动的**判定面**文件（§11.3）：非空即 fail，与红绿无关。 */
      tampering: string[]
      config: 'auto'
    }
    verdict: Verdict
    decided_by: 'auto'
    created_at: string
  }
  /** 决策后的**全量**实例状态；看板据此刷新，不必再发一轮请求。 */
  instances: InstanceOut[]
}

export type GradingScenario = 'pass' | 'fail'
export type ReviewScenario = 'approve' | 'request_changes'

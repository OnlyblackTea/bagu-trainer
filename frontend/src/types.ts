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
  /** 有工单的阶段数；`board_only` 另计（看板清单允许"只上榜、不出卷"）。 */
  assessed_stages: number
  board_only_stages: number
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
  /** 取卷时用的 Track；多 Track 部署下 ticket id 会撞名，故回带归属。 */
  track?: string
}

export interface InstanceOut {
  id: string
  learner: string
  /** 实例**属于哪个 Track**。ticket id 只在 Track 内唯一，看板/取卷都必须先认这个。 */
  track: string
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

/** 阅卷阶段（后端 `GradingStep`）。稳定短标识；人话在 UI 层。 */
export type GradingStep = 'queued' | 'reset_infra' | 'run_tests' | 'parse' | 'done' | 'failed'

export type GradingJobStatus =
  | 'queued'
  | 'running'
  | 'done'
  | 'failed'
  | 'interrupted'
  /** 有活跃记录但**不在本进程手里**（重启过，或另一个实例在跑）——措辞不下结论。 */
  | 'stalled'
  | 'unknown'

export interface GradingStepOut {
  step: GradingStep | string
  status: 'pending' | 'running' | 'done' | 'failed'
  detail: string
  started_at: string | null
  ended_at: string | null
  elapsed_ms: number | null
}

/** `GET /submissions/{id}/progress`（§C.3 步骤 6）。 */
export interface GradingProgressOut {
  submission_id: string | null
  instance_id: string | null
  track: string | null
  ticket_id?: string
  owner: string | null
  status: GradingJobStatus
  step: string
  detail: string
  started_at: string | null
  ended_at: string | null
  elapsed_ms: number | null
  report_ready: boolean
  error: { detail: string; reason: string } | null
  steps: GradingStepOut[]
}

/** 一次尝试与**上一次**的差别（§11：历史查询与比对）。 */
export interface TestStatusChangeOut {
  name: string
  /** 上次的状态；null = 上次报告里没有这条测试（新增/改名）。 */
  frm: string | null
  to: string | null
}

export interface AttemptDeltaOut {
  against: number
  elapsed_ms_before: number | null
  elapsed_ms_after: number | null
  passed_before: boolean | null
  passed_after: boolean | null
  tests: TestStatusChangeOut[]
}

export interface AttemptOut {
  submission: SubmissionOut
  grading: GradingProgressOut | null
  report: GradingReportOut | null
  review: ReviewOut | null
  decision: DecisionOut['decision'] | null
  verdict: 'pass' | 'fail' | null
  delta: AttemptDeltaOut | null
}

/** `GET /instances/{id}/history`：该实例全部尝试，按 seq 从旧到新。 */
export interface InstanceHistoryOut {
  instance_id: string
  ticket_id: string
  track: string
  learner: string
  attempts: AttemptOut[]
}

/** `POST /instances/{id}/submit` 的 202 响应：提交已受理 + 当前进度。 */
export interface SubmitAcceptedOut {
  submission: SubmissionOut
  progress: GradingProgressOut
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

export interface GradingTestResultOut {
  name: string
  status: string
  detail?: string
}

/** `GET /submissions/{id}/report`（§7.4）：**不可信自报告**，带 provenance。 */
export interface GradingReportOut {
  submission_id: string
  ticket: string
  ticket_version: number
  test_version: number
  engine_version: string
  learner: string
  provenance: {
    generated_at: string
    generated_by: string
    /** 真阅卷里有 java/maven/退出码/耗时/`infra`（这次有没有重置环境）。 */
    host_env: Record<string, unknown>
    runner_trust: 'self'
  }
  correctness: {
    test_group: string
    passed: boolean
    tests: GradingTestResultOut[]
  }
  performance: Record<string, unknown> | null
  lint: Record<string, unknown>
  files_changed: string[]
  out_of_scope_edits: string[]
}

export type GradingScenario = 'pass' | 'fail'
export type ReviewScenario = 'approve' | 'request_changes'

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
  /** 预览/评审拿什么和 base 比：`null` = 工作区（含未提交改动）。 */
  diff_head_ref: string | null
  /** 学员要把改动推上去时跑的 git 命令（真 Forge 才有；不含任何凭据）。 */
  push_hint: string | null
  /** 学员想在自己的目录里拉取时照抄的命令（指引式 provision）。 */
  clone_hint: string | null
  /**
   * `path` 在**部署者机器上**的路径（容器化部署才会给；绑定的同一目录）。
   *
   * 界面必须显示它：容器里的 `/var/lib/bagu/ws/...` 对在自己电脑上写代码的人毫无意义。
   * `null` = 没配（非容器部署，`path` 本身就是真的）。
   */
  host_path?: string | null
  simulated: boolean
}

/**
 * 工作区与**远端 dev** 的对账结果（后端 `WorkspaceSync`）。
 *
 * 它存在的理由是"代码写在别的机器上也可以"：学员在笔记本上 push 之后，后端那份 checkout 是旧的，
 * 直接拿去阅卷会给出一个看起来像"你代码没过"的结论。`null`（桩工作区）表示压根没有远端。
 */
export interface WorkspaceSyncOut {
  /** fetch 成不成功。失败 ⇒ **不知道**远端状态，界面不许说"已是最新"。 */
  fetched: boolean
  /** 工作树有未提交改动 ⇒ 学员是在**这台机器**上写代码。 */
  dirty: boolean
  local_ahead: number
  remote_ahead: number
  fast_forwarded: boolean
  /** 对账后 `HEAD` 的 SHA——"这次对着哪一版"，可审计。 */
  revision: string | null
  remote_branch: boolean
  /** 远端有新东西而这次没同步 ⇒ 即将被预览/阅卷的是**旧**代码。 */
  remote_only_changes: boolean
  /** 后端给学员的一句话说明（同步做了什么 / 为什么没做）。 */
  note: string
}

export interface DiffOut {
  base_ref: string
  head_ref: string
  diff_head_ref: string | null
  /** `true` = 比的是工作区（含未提交改动），而不是某条 ref。 */
  worktree: boolean
  diff: string
  total_chars: number
  truncated_files: string[]
  simulated: boolean
  /** 只读对账：远端比你新时如实报出来（否则"没有改动"会被误解成"我没写"）。 */
  sync: WorkspaceSyncOut | null
}

/** `POST /instances/{id}/reissue`：重做这一题——旧实例作废（留档）+ 新实例。 */
export interface ReissueOut {
  /** 已被作废的那个（`stale`）：它的提交/报告/判定都还在历史里。 */
  previous: InstanceOut
  /** 新铸的实例（回到 DAG 初始态，锚在该 ticket 当前版本上）。 */
  instance: InstanceOut
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
  /** 提交前与远端的对账结果——"这次阅卷看的是哪一版"必须让学员看见。 */
  sync: WorkspaceSyncOut | null
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

/** `GET /attempts`：一次尝试的一行摘要（首页"最近练习"用）。 */
export interface AttemptSummaryOut {
  instance_id: string
  ticket_id: string
  track: string
  seq: number
  submission_id: string
  created_at: string
  /** 实例**当前**状态（可能已经 retry/passed，与提交那一刻不同）。 */
  state: InstanceState
  verdict: Verdict | null
  correctness_passed: boolean | null
  elapsed_ms: number | null
  /** 判定面被改动的文件数（§11.3）：非 0 时绿也不算绿。 */
  tampering: number
}

export interface AttemptsOut {
  learner: string
  attempts: AttemptSummaryOut[]
}

/** `GET /meta`：各后端**逐项**如实上报（§15 风险 5：笼统的"模拟"徽标会把真的说成假的）。 */
export interface MetaOut {
  platform_version: string
  grading_backend: 'stub' | 'docker'
  grading_reset: boolean
  forge_backend: 'stub' | 'gitea'
  store_backend: 'sqlite' | 'postgres' | 'memory'
  /** 存储落点：sqlite 是数据库文件的绝对路径。 */
  store_target: string
  instance_id: string
}

export type GradingScenario = 'pass' | 'fail'
export type ReviewScenario = 'approve' | 'request_changes'

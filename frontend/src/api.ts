/**
 * 唯一碰 HTTP 的地方。
 *
 * 两个职责：
 * 1. 把冻结的 REST 契约封成有类型的方法；
 * 2. **把 `{"detail": "<短码>"}` 翻成人话**（Plan 2 §4 铁律）——短码是给机器判定的，
 *    直接丢给学习者等于没说。`workspace_ref_missing` 这类还会把后端附带的
 *    `ref` / `available` 拼进消息里，让内容侧的配置错误一眼可修。
 */

import type {
  DecisionOut,
  DiffOut,
  EnrollmentOut,
  GradingScenario,
  InstanceOut,
  PullRequestOut,
  ReviewOut,
  ReviewScenario,
  SubmissionOut,
  Ticket,
  TrackDetail,
  TrackSummary,
  WorkspaceOut,
} from './types'

/** 后端错误的短码（Plan 1 §C.8）。 */
export type ErrorCode =
  | 'not_found'
  | 'illegal_transition'
  | 'decide_needs_report_and_review'
  | 'diff_too_large'
  | 'git_command_failed'
  | 'workspace_ref_missing'
  | 'report_not_found'
  | 'grading_unavailable'
  | 'forge_unavailable'
  | 'remote_branch_missing'
  | 'internal_error'

export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  readonly ref?: string | null
  readonly available?: string[]

  constructor(opts: {
    status: number
    detail: string
    message: string
    ref?: string | null
    available?: string[]
  }) {
    super(opts.message)
    this.name = 'ApiError'
    this.status = opts.status
    this.detail = opts.detail
    this.ref = opts.ref ?? null
    this.available = opts.available
  }
}

/** 短码 → 人话。每条都要说清"发生了什么 + 我该做什么"。 */
export function humanize(
  detail: string,
  extra?: { ref?: string | null; available?: string[]; reason?: string; branch?: string },
): string {
  switch (detail) {
    case 'not_found':
      return '找不到这个资源：实例、工单或提交可能已失效或被重建。'
    case 'illegal_transition':
      return '当前状态不允许这个操作。请按提示的下一步走（例如先「开始」再「提交」）。'
    case 'decide_needs_report_and_review':
      return '还不能决策：需要先「提交」拿到阅卷报告，再「评审」拿到评审意见。'
    case 'diff_too_large':
      return '改动太大，单个文件就超过 12 万字符：请把改动拆小后重试。'
    case 'git_command_failed':
      return 'git 命令执行失败（仓库可能损坏）。请检查本地工作区。'
    case 'workspace_ref_missing': {
      const ref = extra?.ref ? `阶段分支 ${extra.ref}` : '所需的阶段分支'
      const avail = extra?.available?.length ? `；仓库里现有：${extra.available.join('、')}` : ''
      return `这个 Track 缺少${ref}。§C.5 要求一个阶段一个分支——请让 Track 作者补出该分支${avail}。`
    }
    case 'report_not_found':
      return '还没有阅卷报告：先「提交」才会产生报告，报告是随提交一起生成的。'
    case 'grading_unavailable': {
      const why = extra?.reason ? `（${extra.reason}）` : ''
      return `阅卷跑不起来${why}。这是**平台/环境**问题，不是你代码的问题：本机需要 JDK17 + Maven，以及 Track 声明的基础设施。`
    }
    case 'forge_unavailable': {
      const why = extra?.reason ? `（${extra.reason}）` : ''
      return `Forge（Gitea）连不上或拒绝了这个请求${why}。这是**基础设施**问题，不是你代码的问题：检查 Gitea 是否在跑、token 是否有效。`
    }
    case 'remote_branch_missing':
      return `远端还没有 ${extra?.branch ?? 'dev'} 分支，所以开不了 PR。先在工作区里把改动 push 到该分支（平台不代你跑 git 写操作），再回来开单。`
    case 'internal_error':
      return '后端出了个没预期的错（平台不知道原因，所以没编一个）。请把服务端日志里的堆栈拿出来看。'
    default:
      return `后端返回了未识别的错误：${detail}`
  }
}

interface ErrorBody {
  detail?: unknown
  ref?: string | null
  available?: string[]
  reason?: string
  repo?: string
  branch?: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    throw new ApiError({
      status: 0,
      detail: 'network_error',
      message: `连不上后端（${path}）。确认 platform_api 已启动。`,
    })
  }

  if (!res.ok) {
    let body: ErrorBody = {}
    try {
      body = (await res.json()) as ErrorBody
    } catch {
      // 非 JSON 错误体（例如代理返回的 HTML）
    }
    const detail = typeof body.detail === 'string' ? body.detail : `http_${res.status}`
    throw new ApiError({
      status: res.status,
      detail,
      message: humanize(detail, {
        ref: body.ref,
        available: body.available,
        reason: body.reason,
        branch: body.branch,
      }),
      ref: body.ref,
      available: body.available,
    })
  }

  if (res.status === 204) {
    return undefined as T
  }
  return (await res.json()) as T
}

function query(params: Record<string, string | undefined>): string {
  const usable = Object.entries(params).filter(([, v]) => v !== undefined) as [string, string][]
  return usable.length ? `?${new URLSearchParams(usable).toString()}` : ''
}

export const api = {
  listTracks: () => request<TrackSummary[]>('/tracks'),
  getTrack: (trackId: string) => request<TrackDetail>(`/tracks/${encodeURIComponent(trackId)}`),
  /** 必须用 `identity.id`；用 stage 目录名会 404（Plan 1 Task 0）。 */
  getTicket: (ticketId: string) => request<Ticket>(`/tickets/${encodeURIComponent(ticketId)}`),

  enroll: (learner: string) =>
    request<EnrollmentOut>('/enrollments', {
      method: 'POST',
      body: JSON.stringify({ learner }),
    }),
  listInstances: (learner: string) => request<InstanceOut[]>(`/instances${query({ learner })}`),
  getInstance: (instanceId: string) =>
    request<InstanceOut>(`/instances/${encodeURIComponent(instanceId)}`),

  start: (instanceId: string) =>
    request<InstanceOut>(`/instances/${encodeURIComponent(instanceId)}/start`, { method: 'POST' }),
  provision: (instanceId: string) =>
    request<WorkspaceOut>(`/instances/${encodeURIComponent(instanceId)}/provision`, {
      method: 'POST',
    }),
  diff: (instanceId: string) =>
    request<DiffOut>(`/instances/${encodeURIComponent(instanceId)}/diff`),
  createPr: (instanceId: string) =>
    request<PullRequestOut>(`/instances/${encodeURIComponent(instanceId)}/pr`, { method: 'POST' }),

  submit: (
    instanceId: string,
    opts: { scenario: GradingScenario; pr_url?: string; defense?: string },
  ) =>
    request<SubmissionOut>(
      `/instances/${encodeURIComponent(instanceId)}/submit${query({ scenario: opts.scenario })}`,
      {
        method: 'POST',
        body: JSON.stringify({ pr_url: opts.pr_url ?? null, defense: opts.defense ?? null }),
      },
    ),
  review: (submissionId: string, opts: { scenario: ReviewScenario; dimensions?: string[] }) =>
    request<ReviewOut>(
      `/submissions/${encodeURIComponent(submissionId)}/review${query({ scenario: opts.scenario })}`,
      {
        method: 'POST',
        body: JSON.stringify({ dimensions: opts.dimensions ?? null }),
      },
    ),
  decide: (submissionId: string) =>
    request<DecisionOut>(`/submissions/${encodeURIComponent(submissionId)}/decide`, {
      method: 'POST',
    }),
  retry: (instanceId: string) =>
    request<InstanceOut>(`/instances/${encodeURIComponent(instanceId)}/retry`, { method: 'POST' }),
}

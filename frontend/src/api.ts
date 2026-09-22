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
  AttemptsOut,
  DecisionOut,
  DiffOut,
  EnrollmentOut,
  GradingProgressOut,
  GradingReportOut,
  GradingScenario,
  InstanceHistoryOut,
  InstanceOut,
  MetaOut,
  PullRequestOut,
  ReviewOut,
  ReviewScenario,
  SubmitAcceptedOut,
  Ticket,
  TrackDetail,
  TrackSummary,
  WorkspaceOut,
  WorkspaceSyncOut,
} from './types'

/**
 * 后端错误的短码（Plan 1 §C.8）。
 *
 * **必须与后端实际会发的短码一一对齐**——漏一个，学员看到的就是"未识别的错误：instance_not_found"
 * 这种死胡同（本文件的 `humanize` 落到 default）。对齐由后端侧的
 * `tests/test_error_codes.py` 守着：它扫 `platform_api/**` 里所有 `detail="…"` 字面量，逐个要求
 * 出现在这个文件里。加短码时两边一起加。
 */
export type ErrorCode =
  | 'not_found'
  | 'illegal_transition'
  | 'decide_needs_report_and_review'
  | 'diff_too_large'
  | 'git_command_failed'
  | 'workspace_ref_missing'
  | 'report_not_found'
  | 'report_not_ready'
  | 'grading_unavailable'
  | 'forge_unavailable'
  | 'remote_branch_missing'
  | 'track_not_found'
  | 'track_not_deployed'
  | 'enroll_needs_track'
  | 'ticket_id_ambiguous'
  | 'workspace_path_invalid'
  | 'instance_not_found'
  | 'submission_not_found'
  | 'ticket_not_found'
  | 'internal_error'
  | 'network_error'
  | 'http_422'

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
  extra?: {
    ref?: string | null
    available?: string[]
    reason?: string
    branch?: string
    /** HTTP 状态码。给兜底分支用：**未识别的短码**下，状态码是唯一可靠的线索。 */
    status?: number
    /** 出错的接口路径。兜底分支把它带给学员，否则那句报错没法复现。 */
    path?: string
  },
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
    case 'report_not_ready':
      return '阅卷还在跑：报告没生成完。稍等片刻（进度条会动），跑完这里就会出报告。'
    case 'track_not_found':
      return '找不到这个 Track：它可能没部署，或者 track.yaml 的 id 与你请求的不一致。'
    case 'track_not_deployed':
      return '这个实例属于一个**已经不在部署里**的 Track（内容换过了）。它的历史还在，但没法再对它做操作——请让部署者把这个 Track 重新部署上，或改用新发的实例。'
    case 'enroll_needs_track':
      return '部署了多个 Track，报名时必须指定报哪一个。'
    case 'ticket_id_ambiguous':
      return '这个工单号在多个 Track 里都存在（工单号只在 Track 内唯一）。请指明 Track 后再取卷。'
    case 'workspace_path_invalid': {
      const why =
        extra?.reason === 'not_a_repo'
          ? '那个目录不是 git 仓库'
          : extra?.reason === 'not_a_directory'
            ? '那个目录不存在'
            : '那个路径用不了'
      return `${why}。请用上面给的 clone 命令拉一份，再把**那个**目录登记回来（路径要是后端这台机器上能看到的）。`
    }
    case 'instance_not_found':
      return '这个实例在后端不存在了——多半是后端重启过（`--store memory` 重启即失忆），或它来自一个已经被重置/重建的实例。刷新页面重新「入学」即可，历史记录不会因此丢。'
    case 'submission_not_found':
      return '这条提交在后端不存在了：可能它属于一个已被重置或重建的实例。刷新页面看当前实例的提交。'
    case 'ticket_not_found':
      return '这个工单号在当前 Track 里不存在。看板上的阶段若已撤下，取卷就会 404——刷新页面试试。'
    case 'http_422':
      return '请求参数没通过后端校验（422）。这多半是前端与后端的契约不一致，请把这一步的操作与短码记下来报给部署者。'
    case 'network_error':
      return '连不上后端。确认 `platform_api.serve` 还在跑（若用的是 5174 那个真后端，还要确认 SSH 隧道没断）。'
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
      return unrecognized(detail, extra)
  }
}

/**
 * 兜底：短码不在上面的表里时说什么。
 *
 * 原来的兜底是一句「后端返回了未识别的错误：X」——**死胡同**，学员和部署者都无从下手。而它真发生
 * 过两次：一次是后端多了个新短码而前端没跟上（已由 `tests/test_error_codes.py` 钉住），一次是
 * **后端比前端旧**——旧后端根本没有 `/attempts` 这个路由，Starlette 回它自己的
 * `{"detail":"Not Found"}`，那不是我们的短码表里的任何东西。
 *
 * 所以这里按**状态码**给出可执行的下一步。第一件要分清的事：这到底是"后端不认这个接口"（版本
 * 倾斜/代理配错）还是"后端认了但内部炸了"——两者的处置完全不同。
 */
function unrecognized(
  detail: string,
  extra?: { status?: number; path?: string },
): string {
  const where = extra?.path ? `（${extra.path}）` : ''
  const status = extra?.status
  if (status === 404) {
    return `后端没有这个接口${where}。多半是**后端比前端旧**（拉了新前端但没重启/重新部署后端），或反向代理没把这个前缀转给后端。核对主页「这台服务」里的版本号。`
  }
  if (status === 405) {
    return `后端不认这个请求方法${where}：前后端版本不一致（旧后端没有这个端点）。重新部署后端。`
  }
  if (status === 502 || status === 503 || status === 504) {
    return `后端（或它前面的代理）暂时不可用（HTTP ${status}）${where}：确认 platform_api 还在跑。`
  }
  if (status !== undefined && status >= 500) {
    return `后端内部错误（HTTP ${status}）${where}：「${detail}」不是平台已知的短码，去服务端日志里看堆栈。`
  }
  return `后端返回了未识别的错误：${detail}${where}。这多半是前后端版本不一致（重新部署后端），或契约漂了——把这句话和路径报给部署者。`
}

/**
 * "这个资源已经没了"一类短码。界面看到它们时不该只是报错——本地那份实例/提交已经过期
 * （最典型：后端重启过、或部署者重置过数据），**正确恢复是刷新看板**，而不是让学员对着一个
 * 已经不存在的东西反复点。
 */
export const STALE_RESOURCE_CODES: readonly string[] = [
  'instance_not_found',
  'submission_not_found',
  'ticket_not_found',
  'not_found',
]

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
        status: res.status,
        path,
      }),
      ref: body.ref,
      available: body.available,
    })
  }

  if (res.status === 204) {
    return undefined as T
  }
  try {
    return (await res.json()) as T
  } catch {
    // 2xx 但不是 JSON ⇒ 请求压根**没到后端**（最典型：开发代理少配了一个前缀，于是拿到
    // 前端自己的 index.html）。不把底层那句 `Unexpected token '<'` 抛给学员——它指向 Vite，
    // 而问题在部署配置。
    throw new ApiError({
      status: res.status,
      detail: 'unexpected_response',
      message: `后端返回的不是 JSON（${path}）。多半是这个前缀没配到反向代理/开发代理上，请求打到了前端静态资源——部署者查一下代理配置。`,
    })
  }
}

function query(params: Record<string, string | undefined>): string {
  const usable = Object.entries(params).filter(([, v]) => v !== undefined) as [string, string][]
  return usable.length ? `?${new URLSearchParams(usable).toString()}` : ''
}

export const api = {
  /** 后端逐项自述（阅卷/Forge/存储各是什么）。前端据此说清"进度存哪、重启会不会丢"。 */
  meta: () => request<MetaOut>('/meta'),
  listTracks: () => request<TrackSummary[]>('/tracks'),
  getTrack: (trackId: string) => request<TrackDetail>(`/tracks/${encodeURIComponent(trackId)}`),
  /** 必须用 `identity.id`；用 stage 目录名会 404（Plan 1 Task 0）。 */
  getTicket: (ticketId: string) => request<Ticket>(`/tickets/${encodeURIComponent(ticketId)}`),
  /** 多 Track 下的**权威**取卷地址：ticket id 只在 Track 内唯一，扁平路径会撞名 409。 */
  getTrackTicket: (trackId: string, ticketId: string) =>
    request<Ticket>(
      `/tracks/${encodeURIComponent(trackId)}/tickets/${encodeURIComponent(ticketId)}`,
    ),

  enroll: (learner: string, track?: string) =>
    request<EnrollmentOut>('/enrollments', {
      method: 'POST',
      body: JSON.stringify(track ? { learner, track } : { learner }),
    }),
  listInstances: (learner: string, track?: string) =>
    request<InstanceOut[]>(`/instances${query({ learner, track })}`),
  getInstance: (instanceId: string) =>
    request<InstanceOut>(`/instances/${encodeURIComponent(instanceId)}`),

  start: (instanceId: string) =>
    request<InstanceOut>(`/instances/${encodeURIComponent(instanceId)}/start`, { method: 'POST' }),
  provision: (instanceId: string) =>
    request<WorkspaceOut>(`/instances/${encodeURIComponent(instanceId)}/provision`, {
      method: 'POST',
    }),
  /** 登记（`path`）/ 撤销登记（`null`）学员自己的工作区目录——指引式 provision。 */
  attachWorkspace: (instanceId: string, path: string | null) =>
    request<WorkspaceOut & { workspace_path: string | null }>(
      `/instances/${encodeURIComponent(instanceId)}/workspace`,
      { method: 'POST', body: JSON.stringify({ path }) },
    ),
  diff: (instanceId: string) =>
    request<DiffOut>(`/instances/${encodeURIComponent(instanceId)}/diff`),
  /**
   * 把工作区与远端 `dev` 对一次账（fetch，且**只在无损时**快进）。
   *
   * 代码写在**另一台机器**上时用这个：那边 push 完，这边点一下就把最新代码拉进工作树。
   * 提交时平台会自动做同一件事，所以这是"我想先自己看一眼"的入口。
   */
  syncWorkspace: (instanceId: string) =>
    request<WorkspaceOut & { sync: WorkspaceSyncOut | null }>(
      `/instances/${encodeURIComponent(instanceId)}/sync`,
      { method: 'POST' },
    ),
  createPr: (instanceId: string) =>
    request<PullRequestOut>(`/instances/${encodeURIComponent(instanceId)}/pr`, { method: 'POST' }),

  submit: (
    instanceId: string,
    opts: { scenario: GradingScenario; pr_url?: string; defense?: string },
  ) =>
    request<SubmitAcceptedOut>(
      `/instances/${encodeURIComponent(instanceId)}/submit${query({ scenario: opts.scenario })}`,
      {
        method: 'POST',
        body: JSON.stringify({ pr_url: opts.pr_url ?? null, defense: opts.defense ?? null }),
      },
    ),
  /** 提交是异步的：轮询这个端点直到 `report_ready`（§C.3 步骤 6）。 */
  progress: (submissionId: string) =>
    request<GradingProgressOut>(`/submissions/${encodeURIComponent(submissionId)}/progress`),
  report: (submissionId: string) =>
    request<GradingReportOut>(`/submissions/${encodeURIComponent(submissionId)}/report`),
  /** 该实例的**全部**尝试 + 与上一次的比对（§11）。 */
  history: (instanceId: string) =>
    request<InstanceHistoryOut>(`/instances/${encodeURIComponent(instanceId)}/history`),
  /** 最近的几次提交（跨实例、跨 Track，最新在前）——首页"最近练习"。 */
  attempts: (learner: string, limit = 8) =>
    request<AttemptsOut>(`/attempts${query({ learner, limit: String(limit) })}`),
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

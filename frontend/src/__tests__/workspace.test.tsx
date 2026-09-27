import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { WorkspaceView } from '../components/WorkspaceView'
import type { DiffOut, WorkspaceOut, WorkspaceSyncOut } from '../types'
import { makeInstance } from './fixtures'

/**
 * 「工作区 / diff 不再只是模拟」在界面上的落点：**逐项**跟着后端真实的 `simulated` 走。
 *
 * 这条规则两头都会犯错：真仓库 / 真 diff 上盖「模拟」章，会把真结果说成假的（§15 风险 5）；
 * 桩产出上没有徽标，则是骗人。所以这里把两种后端各钉一遍。
 */

const REAL_WS: WorkspaceOut = {
  path: '/home/lqz/bagu-trial/ws/database-bagu/ti-1',
  branch: 'dev',
  base_ref: 'origin/stage-01-product-query-index',
  head_ref: 'dev',
  diff_head_ref: null,
  push_hint: 'git add -A && git commit -m "stage 完成" && git push -u origin dev',
  clone_hint:
    'git clone http://127.0.0.1:3000/deployer/seckill-lab.git ~/my-bagu-stage-01\ncd ~/my-bagu-stage-01\ngit checkout -b dev origin/stage-01-product-query-index',
  merge_hint: null,
  simulated: false,
}

const STUB_WS: WorkspaceOut = {
  ...REAL_WS,
  base_ref: 'origin/stage-00-onboarding',
  head_ref: 'origin/stage-00-onboarding-sol',
  diff_head_ref: 'origin/stage-00-onboarding-sol',
  push_hint: null,
  clone_hint: null,
  simulated: true,
}

function diffOut(over: Partial<DiffOut> = {}): DiffOut {
  return {
    base_ref: 'origin/stage-01-product-query-index',
    head_ref: 'dev',
    diff_head_ref: null,
    diff_base: 'abcdef0123456789',
    diff_base_kind: 'stage_start',
    worktree: true,
    diff: 'diff --git a/src/x.sql b/src/x.sql\n+CREATE INDEX idx_product_search\n',
    total_chars: 64,
    truncated_files: [],
    simulated: false,
    sync: null,
    ...over,
  }
}

/** 一次对账结果（`WorkspaceSyncOut`）。默认"与远端一致"，按需覆盖。 */
function syncOut(over: Partial<WorkspaceSyncOut> = {}): WorkspaceSyncOut {
  return {
    fetched: true,
    dirty: false,
    local_ahead: 0,
    remote_ahead: 0,
    fast_forwarded: false,
    revision: 'abcdef0123456789',
    remote_branch: true,
    remote_only_changes: false,
    note: '工作区与远端 dev 一致。',
    ...over,
  }
}

function stubFetch(routes: Record<string, unknown>) {
  const seen: Array<{ url: string; init?: RequestInit }> = []
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    seen.push({ url, init })
    const key = Object.keys(routes).find((k) => url.startsWith(k))
    if (!key) return { ok: false, status: 404, json: async () => ({ detail: 'not_found' }) }
    const body = routes[key] as { __status?: number }
    if (body?.__status) return { ok: false, status: body.__status, json: async () => body }
    return { ok: true, status: 200, json: async () => routes[key] }
  })
  vi.stubGlobal('fetch', fn)
  return { fn, calls: seen }
}

/**
 * 「拉取本地工作区」「从远端同步」是**低频/有副作用**的动作，收在「更多操作」折叠里
 * （不与「开始本阶段」「查看 diff」抢视觉权重）。所以点它们之前先展开。
 */
async function openMoreActions(user: ReturnType<typeof userEvent.setup>) {
  const summary = screen.getByText('更多操作')
  const details = summary.closest('details')
  if (details && !details.open) await user.click(summary)
  return user
}

async function openFlow(routes: Record<string, unknown>) {
  stubFetch(routes).fn
  const user = userEvent.setup()
  render(<WorkspaceView instance={makeInstance({ state: 'in_progress' })} busy={false} onStart={() => {}} />)
  await openMoreActions(user)
  await user.click(screen.getByRole('button', { name: '拉取本地工作区' }))
  await screen.findByTestId('workspace-info')
  await user.click(screen.getByRole('button', { name: '查看 diff' }))
  await screen.findByTestId('diff-info')
  return user
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('指引式 provision（在**你自己**的目录里做这一题）', () => {
  it('给出可照抄的 clone/checkout 命令，且不含任何凭据', async () => {
    await openFlow({
      '/instances/ti-1/provision': REAL_WS,
      '/instances/ti-1/diff': diffOut(),
    })
    const hint = screen.getByTestId('clone-hint')
    expect(hint).toHaveTextContent('git clone')
    expect(hint).toHaveTextContent('git checkout -b dev origin/')
    const cmd = hint.querySelector('pre')?.textContent ?? ''
    expect(cmd).not.toMatch(/token|oauth/)
  })

  it('把路径登记回来之后，界面说清"现在用的是你自己的目录"', async () => {
    const fetchMock = stubFetch({
        '/instances/ti-1/workspace': {
          ...REAL_WS,
          path: '/home/lqz/my-bagu-stage-01',
          workspace_path: '/home/lqz/my-bagu-stage-01',
        },
        '/instances/ti-1/provision': REAL_WS,
        '/instances/ti-1/diff': diffOut(),
    })

    const user = userEvent.setup()
    render(
      <WorkspaceView instance={makeInstance({ state: 'in_progress' })} busy={false} onStart={() => {}} />,
    )
    await openMoreActions(user)
    await user.click(screen.getByRole('button', { name: '拉取本地工作区' }))
    await screen.findByTestId('workspace-info')

    await user.type(screen.getByTestId('workspace-path'), '/home/lqz/my-bagu-stage-01')
    await user.click(screen.getByRole('button', { name: '用这个目录' }))

    await waitFor(() =>
      expect(screen.getByTestId('clone-hint')).toHaveTextContent('你自己的目录'),
    )
    expect(screen.getByTestId('clone-hint')).toHaveTextContent('/home/lqz/my-bagu-stage-01')
    const posted = fetchMock.calls.find((c) => c.url.endsWith('/instances/ti-1/workspace'))
    expect(JSON.parse(String(posted?.init?.body))).toEqual({ path: '/home/lqz/my-bagu-stage-01' })
  })

  it('路径不能用时说清是"目录不存在"还是"不是 git 仓库"', async () => {
    stubFetch({
      '/instances/ti-1/workspace': {
        __status: 422,
        detail: 'workspace_path_invalid',
        reason: 'not_a_repo',
      },
      '/instances/ti-1/provision': REAL_WS,
    })
    const user = userEvent.setup()
    render(
      <WorkspaceView instance={makeInstance({ state: 'in_progress' })} busy={false} onStart={() => {}} />,
    )
    await openMoreActions(user)
    await user.click(screen.getByRole('button', { name: '拉取本地工作区' }))
    await screen.findByTestId('workspace-info')
    await user.type(screen.getByTestId('workspace-path'), '/tmp/not-a-repo')
    await user.click(screen.getByRole('button', { name: '用这个目录' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('不是 git 仓库')
    expect(alert).not.toHaveTextContent('未识别')
  })

  it('桩没有可推的远端 ⇒ 不给 clone 指引（给了会把人骗去跑一条注定失败的命令）', async () => {
    await openFlow({
      '/instances/ti-1/provision': STUB_WS,
      '/instances/ti-1/diff': diffOut({ simulated: true, worktree: false }),
    })
    expect(screen.queryByTestId('clone-hint')).not.toBeInTheDocument()
  })
})

describe('真 Forge（gitea）', () => {
  it('不盖「模拟」章，并给出可照抄的 git 命令与「推完就能提交」的说明', async () => {
    await openFlow({
      '/instances/ti-1/provision': REAL_WS,
      '/instances/ti-1/diff': diffOut(),
    })

    // 真 Gitea ⇒ 一个「模拟」徽标都不该有
    expect(screen.queryByTestId('simulated-badge')).not.toBeInTheDocument()
    // 工作区面板一处（PR 面板已随 L3 废弃，不再有第二处）
    expect(screen.getAllByText('真 Gitea').length).toBeGreaterThanOrEqual(1)

    // PR 已废弃（L3）：真 Gitea 下给的是「推完就能提交」的说明，而不是一个开单入口
    expect(screen.getByTestId('push-hint')).toHaveTextContent('推完就能回平台')

    const push = screen.getByTestId('push-hint')
    expect(push).toHaveTextContent('git push -u origin dev')
    // 命令本身不许出现任何 token/凭据（说明文字里"不含 token"是另一回事）
    const command = push.querySelector('pre')?.textContent ?? ''
    expect(command).toContain('git push -u origin dev')
    expect(command).not.toMatch(/token|oauth|@|:\/\//)
  })

  it('还没合本阶段基线时，把「先 git merge origin/<stage>」这条命令递到眼前', async () => {
    await openFlow({
      '/instances/ti-1/provision': {
        ...REAL_WS,
        merge_hint: 'git merge origin/stage-02-order-transaction   # 本阶段的验收测试在这个分支上',
      },
      '/instances/ti-1/diff': diffOut({ base_ref: 'origin/stage-02-order-transaction' }),
    })

    const hint = screen.getByTestId('merge-hint')
    expect(hint).toHaveTextContent('本阶段基线')
    expect(hint.querySelector('pre')?.textContent).toContain('git merge origin/stage-02-order-transaction')
    // 说明里必须点出"验收测试在它上面"：这才是学员非做不可的理由
    expect(hint).toHaveTextContent('验收测试就在那个分支上')
  })

  it('diff 说清"比的是工作区（含未提交）"，而不是笼统 base ↔ head', async () => {
    await openFlow({
      '/instances/ti-1/provision': REAL_WS,
      '/instances/ti-1/diff': diffOut(),
    })

    const info = screen.getByTestId('diff-info')
    expect(info).toHaveTextContent('工作区（含未提交改动）')
    expect(info).toHaveTextContent('还没 commit')
    expect(info).toHaveTextContent('idx_product_search')
  })

  it('工作区还没改动时说清"还没有改动"，而不是给一个空的 diff 框', async () => {
    await openFlow({
      '/instances/ti-1/provision': REAL_WS,
      '/instances/ti-1/diff': diffOut({ diff: '', total_chars: 0 }),
    })
    expect(screen.getByTestId('diff-empty')).toHaveTextContent('还没有改动')
  })
})

describe('桩 Forge（stub）', () => {
  it('逐项标「模拟」：工作区 / diff 来源，且不给 push 命令', async () => {
    await openFlow({
      '/instances/ti-1/provision': STUB_WS,
      '/instances/ti-1/diff': diffOut({
        base_ref: 'origin/stage-00-onboarding',
        head_ref: 'origin/stage-00-onboarding-sol',
        diff_head_ref: 'origin/stage-00-onboarding-sol',
        worktree: false,
        simulated: true,
      }),
    })

    // 两处都有徽标（Forge + diff 来源；PR 面板已随 L3 废弃）
    expect(screen.getAllByTestId('simulated-badge').length).toBeGreaterThanOrEqual(2)
    // 桩没有可推的远端 ⇒ 不给一条注定失败的 push 命令
    expect(screen.queryByTestId('push-hint')).not.toBeInTheDocument()
    expect(screen.getByTestId('diff-info')).toHaveTextContent('origin/stage-00-onboarding-sol')
    expect(screen.getByTestId('diff-info')).not.toHaveTextContent('还没 commit')
  })
})

describe('代码写在**另一台机器**上（与远端对账）', () => {
  it('远端领先且工作区干净：diff 说"你多半在另一台机器上写的"，而不是"还没有改动"', async () => {
    await openFlow({
      '/instances/ti-1/provision': REAL_WS,
      '/instances/ti-1/diff': diffOut({
        diff: '',
        total_chars: 0,
        sync: syncOut({
          remote_ahead: 2,
          remote_only_changes: true,
          note: '远端 dev 领先这份工作区 2 个提交：你多半是在另一台机器上写的。',
        }),
      }),
    })

    const behind = screen.getByTestId('diff-behind')
    expect(behind).toHaveTextContent('另一台机器')
    expect(behind).toHaveTextContent('从远端同步')
    expect(screen.queryByTestId('diff-empty')).not.toBeInTheDocument()
  })

  it('「从远端同步」把快进结果说出来（对着哪一版也算清）', async () => {
    const fetchMock = stubFetch({
      '/instances/ti-1/provision': REAL_WS,
      '/instances/ti-1/sync': {
        ...REAL_WS,
        sync: syncOut({
          fast_forwarded: true,
          revision: 'deadbeefcafe1234',
          note: '已把工作区快进到远端的 dev——这次阅卷看的就是它。',
        }),
      },
    })
    const user = userEvent.setup()
    render(
      <WorkspaceView instance={makeInstance({ state: 'in_progress' })} busy={false} onStart={() => {}} />,
    )
    await openMoreActions(user)
    await user.click(screen.getByTestId('sync-remote'))

    const info = await screen.findByTestId('sync-info')
    expect(info).toHaveTextContent('快进')
    expect(info).toHaveTextContent('deadbeefcafe') // 短的 SHA：够对照，又不占版面
    const posted = fetchMock.calls.find((c) => c.url.endsWith('/instances/ti-1/sync'))
    expect(posted?.init?.method).toBe('POST')
  })

  it('桩工作区没有远端 ⇒ 一个字都不提同步（不显示"已是最新"这种假状态）', async () => {
    await openFlow({
      '/instances/ti-1/provision': STUB_WS,
      '/instances/ti-1/diff': diffOut({ simulated: true, worktree: false, sync: null }),
    })
    expect(screen.queryByTestId('sync-info')).not.toBeInTheDocument()
  })
})

describe('工作区就绪', () => {
  it('provision 之后展示路径/分支/骨架', async () => {
    await openFlow({
      '/instances/ti-1/provision': REAL_WS,
      '/instances/ti-1/diff': diffOut(),
    })
    const info = screen.getByTestId('workspace-info')
    expect(info).toHaveTextContent('/home/lqz/bagu-trial/ws/database-bagu/ti-1')
    expect(info).toHaveTextContent('dev')
    expect(info).toHaveTextContent('origin/stage-01-product-query-index')
  })

  it('gate 文案是"当前阶段该做什么"，且始终声明平台不代跑 git 写操作', async () => {
    stubFetch({})
    render(<WorkspaceView instance={makeInstance()} busy={false} onStart={() => {}} />)
    expect(screen.getByTestId('gate')).toHaveTextContent('可开始')
    expect(screen.getByTestId('gate')).toHaveTextContent('当前阶段')
    await waitFor(() =>
      expect(screen.getByTestId('workspace-view')).toHaveTextContent('不替你跑 git 写操作'),
    )
  })
})

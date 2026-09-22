import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { WorkspaceView } from '../components/WorkspaceView'
import type { DiffOut, PullRequestOut, WorkspaceOut } from '../types'
import { makeInstance } from './fixtures'

/**
 * 「PR / diff 不再只是模拟」在界面上的落点：**逐项**跟着后端真实的 `simulated` 走。
 *
 * 这条规则两头都会犯错：真 fork / 真 diff / 真 PR 上盖「模拟」章，会把真结果说成假的（§15 风险 5）；
 * 桩产出上没有徽标，则是骗人。所以这里把两种后端各钉一遍。
 */

const REAL_WS: WorkspaceOut = {
  path: '/home/lqz/bagu-trial/ws/database-bagu/ti-1',
  branch: 'dev',
  base_ref: 'origin/stage-01-product-query-index',
  head_ref: 'dev',
  diff_head_ref: null,
  push_hint: 'git add -A && git commit -m "stage 完成" && git push -u origin dev',
  simulated: false,
}

const STUB_WS: WorkspaceOut = {
  ...REAL_WS,
  base_ref: 'origin/stage-00-onboarding',
  head_ref: 'origin/stage-00-onboarding-sol',
  diff_head_ref: 'origin/stage-00-onboarding-sol',
  push_hint: null,
  simulated: true,
}

function diffOut(over: Partial<DiffOut> = {}): DiffOut {
  return {
    base_ref: 'origin/stage-01-product-query-index',
    head_ref: 'dev',
    diff_head_ref: null,
    worktree: true,
    diff: 'diff --git a/src/x.sql b/src/x.sql\n+CREATE INDEX idx_product_search\n',
    total_chars: 64,
    truncated_files: [],
    simulated: false,
    ...over,
  }
}

function prOut(simulated: boolean): PullRequestOut {
  return {
    pr_url: simulated
      ? 'https://gitea.local/deployer/simulated/pulls/ti-1'
      : 'http://127.0.0.1:3000/trackowner/seckill-lab/pulls/3',
    simulated,
  }
}

function stubFetch(routes: Record<string, unknown>) {
  const fn = vi.fn(async (url: string) => {
    const key = Object.keys(routes).find((k) => url.startsWith(k))
    if (!key) return { ok: false, status: 404, json: async () => ({ detail: 'not_found' }) }
    return { ok: true, status: 200, json: async () => routes[key] }
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

async function openFlow(routes: Record<string, unknown>) {
  stubFetch(routes)
  const user = userEvent.setup()
  render(<WorkspaceView instance={makeInstance({ state: 'in_progress' })} busy={false} onStart={() => {}} />)
  await user.click(screen.getByRole('button', { name: '拉取本地工作区' }))
  await screen.findByTestId('workspace-info')
  await user.click(screen.getByRole('button', { name: '查看 diff' }))
  await screen.findByTestId('diff-info')
  await user.click(screen.getByRole('button', { name: '开 PR' }))
  await screen.findByTestId('pr-info')
  return user
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('真 Forge（gitea）', () => {
  it('不盖「模拟」章，PR 是可点的真链接，并给出可照抄的 git 命令', async () => {
    await openFlow({
      '/instances/ti-1/provision': REAL_WS,
      '/instances/ti-1/diff': diffOut(),
      '/instances/ti-1/pr': prOut(false),
    })

    // 真 Gitea ⇒ 一个「模拟」徽标都不该有
    expect(screen.queryByTestId('simulated-badge')).not.toBeInTheDocument()
    expect(screen.getAllByText('真 Gitea').length).toBeGreaterThanOrEqual(2)

    const link = screen.getByTestId('pr-link')
    expect(link).toHaveAttribute('href', 'http://127.0.0.1:3000/trackowner/seckill-lab/pulls/3')

    const push = screen.getByTestId('push-hint')
    expect(push).toHaveTextContent('git push -u origin dev')
    // 命令本身不许出现任何 token/凭据（说明文字里"不含 token"是另一回事）
    const command = push.querySelector('pre')?.textContent ?? ''
    expect(command).toContain('git push -u origin dev')
    expect(command).not.toMatch(/token|oauth|@|:\/\//)
  })

  it('diff 说清"比的是工作区（含未提交）"，而不是笼统 base ↔ head', async () => {
    await openFlow({
      '/instances/ti-1/provision': REAL_WS,
      '/instances/ti-1/diff': diffOut(),
      '/instances/ti-1/pr': prOut(false),
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
      '/instances/ti-1/pr': prOut(false),
    })
    expect(screen.getByTestId('diff-empty')).toHaveTextContent('还没有改动')
  })
})

describe('桩 Forge（stub）', () => {
  it('逐项标「模拟」：工作区 / diff 来源 / PR，且 PR 不是链接、不给 push 命令', async () => {
    await openFlow({
      '/instances/ti-1/provision': STUB_WS,
      '/instances/ti-1/diff': diffOut({
        base_ref: 'origin/stage-00-onboarding',
        head_ref: 'origin/stage-00-onboarding-sol',
        diff_head_ref: 'origin/stage-00-onboarding-sol',
        worktree: false,
        simulated: true,
      }),
      '/instances/ti-1/pr': prOut(true),
    })

    // 三处都有徽标（Forge ×2 + diff 来源）
    expect(screen.getAllByTestId('simulated-badge').length).toBeGreaterThanOrEqual(3)
    // 桩没有可推的远端 ⇒ 不给一条注定失败的 push 命令
    expect(screen.queryByTestId('push-hint')).not.toBeInTheDocument()
    // 桩的 PR 是占位 URL，不做成链接（点开是死的）
    expect(screen.queryByTestId('pr-link')).not.toBeInTheDocument()
    expect(screen.getByTestId('diff-info')).toHaveTextContent('origin/stage-00-onboarding-sol')
    expect(screen.getByTestId('diff-info')).not.toHaveTextContent('还没 commit')
  })
})

describe('工作区就绪', () => {
  it('provision 之后展示路径/分支/骨架', async () => {
    await openFlow({
      '/instances/ti-1/provision': REAL_WS,
      '/instances/ti-1/diff': diffOut(),
      '/instances/ti-1/pr': prOut(false),
    })
    const info = screen.getByTestId('workspace-info')
    expect(info).toHaveTextContent('/home/lqz/bagu-trial/ws/database-bagu/ti-1')
    expect(info).toHaveTextContent('dev')
    expect(info).toHaveTextContent('origin/stage-01-product-query-index')
  })

  it('gate 文案是"下一步"，且始终声明平台不代跑 git 写操作', async () => {
    stubFetch({})
    render(<WorkspaceView instance={makeInstance()} busy={false} onStart={() => {}} />)
    expect(screen.getByTestId('gate')).toHaveTextContent('可开始')
    await waitFor(() =>
      expect(screen.getByTestId('workspace-view')).toHaveTextContent('不替你跑 git 写操作'),
    )
  })
})

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import { makeInstance, ticket, track } from './fixtures'

/**
 * 「资源已经不在了」这一类错误的**恢复动作**。
 *
 * 真实场景：后端重启过（`--store memory` 重启即失忆）或部署者重置了数据，浏览器里那份实例却还在。
 * 此时光报一句「找不到这个资源」没用——界面上那个实例是个幻影，点它只会再 404。正确的恢复是
 * **刷新看板并清掉选中项**，让学员重新挑一个阶段。
 *
 * 这个缺陷是真被撞出来的：短码 `instance_not_found` 当时不在前端的人话表里，学员看到的是
 * 「后端返回了未识别的错误：instance_not_found」。
 */

function backend(opts: { instancesAfterRefresh?: () => unknown }) {
  let enrolled = true
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? 'GET').toUpperCase()
    const json = (body: unknown, status = 200) => ({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    })

    if (url === '/tracks') {
      return json([
        {
          id: track.id,
          name: track.name,
          language: track.language,
          trust_level: track.trust_level,
          stages: track.stages.map((s) => s.stage),
          assessed_stages: 2,
          board_only_stages: 1,
        },
      ])
    }
    if (url === `/tracks/${track.id}`) return json(track)
    if (url.startsWith('/tracks/') && url.includes('/tickets/')) return json(ticket)
    if (url.startsWith('/instances?')) {
      if (!enrolled) return json([])
      return json(opts.instancesAfterRefresh ? opts.instancesAfterRefresh() : [makeInstance()])
    }
    // 这个实例已经不在后端了
    if (/^\/instances\/ti-1\/(start|provision|diff|pr)$/.test(url) && method === 'POST') {
      enrolled = false // 恢复动作刷新看板时会拿到空列表
      return json({ detail: 'instance_not_found' }, 404)
    }
    if (url.endsWith('/history')) return json({ detail: 'instance_not_found' }, 404)
    return json({ detail: 'not_found' }, 404)
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('实例已经不在后端了', () => {
  it('错误说的是"刷新重新入学"，而不是"未识别的错误：instance_not_found"', async () => {
    vi.stubGlobal('fetch', backend({}))
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByTestId('stage-stage-00-onboarding'))
    await user.click(await screen.findByRole('button', { name: '开始本阶段' }))

    const alert = await screen.findByRole('alert')
    expect(alert).not.toHaveTextContent('未识别')
    expect(alert).toHaveTextContent(/已经不在|不存在/)
  })

  it('顺手刷新看板并清掉选中项（那个实例是个幻影，留着只会再 404）', async () => {
    const fetchMock = backend({})
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByTestId('stage-stage-00-onboarding'))
    await user.click(await screen.findByRole('button', { name: '开始本阶段' }))

    // 选中项被清掉：题面卡片消失，回到"选一个阶段开始"
    await waitFor(() =>
      expect(screen.queryByTestId('ticket-view')).not.toBeInTheDocument(),
    )
    expect(screen.getByText(/选一个阶段开始/)).toBeInTheDocument()
    // 并且真的重新拉了实例列表
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter((c) => String(c[0]).startsWith('/instances?')).length,
      ).toBeGreaterThan(1),
    )
  })
})

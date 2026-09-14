import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TicketView } from '../components/TicketView'
import { ticket, track } from './fixtures'

function stubTicket(body: unknown, status = 200) {
  const fn = vi.fn(async (_url: string, _init?: RequestInit) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }))
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
})

const assessed = track.stages[0]
const boardOnly = track.stages[2]

describe('TicketView（屏幕 2 · 发卷）', () => {
  it('用 identity.id 取卷并渲染题面', async () => {
    const fetchMock = stubTicket(ticket)
    render(<TicketView stage={assessed} />)

    await waitFor(() => expect(screen.getByText(ticket.problem)).toBeInTheDocument())
    expect(fetchMock.mock.calls[0][0]).toBe('/tickets/db-stage-00')
    expect(screen.getByText('最左前缀')).toBeInTheDocument()
    expect(screen.getByText('src/db-stage-00/Solution.java')).toBeInTheDocument()
    expect(screen.getByText(/先看 EXPLAIN/)).toBeInTheDocument()
    expect(screen.getByText('tests/db-stage-00/AcceptanceTest.java')).toBeInTheDocument()
  })

  it('board-only 阶段不取卷，只解释为什么没有工单', () => {
    const fetchMock = stubTicket(ticket)
    render(<TicketView stage={boardOnly} />)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByText(/只上榜、不出卷/)).toBeInTheDocument()
  })

  it('取卷失败时把后端短码翻成人话，而不是裸短码', async () => {
    stubTicket({ detail: 'not_found' }, 404)
    render(<TicketView stage={assessed} />)
    await waitFor(() =>
      expect(screen.getByText(/找不到这个资源/)).toBeInTheDocument(),
    )
  })

  // --- D2 防剧透回归护栏 -------------------------------------------------------

  it('D2：题面里不得出现解法 ref / 分支 / 答案内容', async () => {
    stubTicket(ticket)
    const { container } = render(<TicketView stage={assessed} />)
    await waitFor(() => expect(screen.getByText(ticket.problem)).toBeInTheDocument())

    const text = container.textContent ?? ''
    // 注意：files_to_change 里的 `Solution.java` 是**学员要改的文件名**，不是剧透——
    // D2 要挡的是解法本身。所以这里断言的是真正的泄露向量：
    expect(text).not.toMatch(/-sol/) // 解法 ref 后缀
    expect(text).not.toContain('origin/') // 分支/ref 信息
    expect(text).not.toContain('模拟学员解法') // demo 仓库里解法分支的文件内容
    expect(text).not.toContain('public class Solution') // 解法实现
  })

  it('D2：board-only 占位也不泄露分支信息', () => {
    stubTicket(ticket)
    const { container } = render(<TicketView stage={boardOnly} />)
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/-sol/)
    expect(text).not.toContain('origin/')
  })
})

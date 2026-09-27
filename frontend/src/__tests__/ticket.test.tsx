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
    render(<TicketView stage={assessed} trackId={track.id} />)

    await waitFor(() => expect(screen.getByText(ticket.problem)).toBeInTheDocument())
    expect(fetchMock.mock.calls[0][0]).toBe('/tracks/database-bagu/tickets/db-stage-00')
    expect(screen.getByText('最左前缀')).toBeInTheDocument()
    expect(screen.getByText('src/db-stage-00/Solution.java')).toBeInTheDocument()
    expect(screen.getByText(/先看 EXPLAIN/)).toBeInTheDocument()
    expect(screen.getByText('tests/db-stage-00/AcceptanceTest.java')).toBeInTheDocument()
  })

  it('board-only 阶段不取卷，只解释为什么没有工单', () => {
    const fetchMock = stubTicket(ticket)
    render(<TicketView stage={boardOnly} trackId={track.id} />)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByText(/只上榜、不出卷/)).toBeInTheDocument()
  })

  // --- 信息层级（UX 评审 §7/§8）--------------------------------------------------

  it('正文只保留三层：一级是任务说明/改动面/验收标准/提示，标题不再造第三层', async () => {
    stubTicket(ticket)
    const { container } = render(<TicketView stage={assessed} trackId={track.id} />)
    await waitFor(() => expect(screen.getByText(ticket.problem)).toBeInTheDocument())

    for (const label of ['任务说明', '改动面', '验收标准', '提示']) {
      expect(screen.getByRole('heading', { level: 3, name: new RegExp(label) })).toBeInTheDocument()
    }
    // 每个语义区块只用一个 h3 标签；h2 只留给工单标题本身，不再有卡片套卡片
    expect(container.querySelectorAll('h1, h4')).toHaveLength(0)
    expect(container.querySelectorAll('h2')).toHaveLength(1)
  })

  it('提示默认全折叠，标题是「提示 N：标题」；没写 title 时退回正文首行', async () => {
    stubTicket({
      ...ticket,
      hints: [
        { id: 'H1', title: '从哪看起', text: '先看 EXPLAIN，再决定索引列序。' },
        { id: 'H2', text: '这是一条没有标题的提示，至少要让人看见它在讲什么。' },
      ],
    })
    const { container } = render(<TicketView stage={assessed} trackId={track.id} />)
    await waitFor(() => expect(screen.getByText('提示 1')).toBeInTheDocument())

    const hints = container.querySelectorAll('details.hint')
    expect(hints).toHaveLength(2)
    // 默认折叠：提示是"卡住了再看"的辅助信息，不该在第一次读题时抢注意力
    for (const h of hints) expect(h).not.toHaveAttribute('open')
    expect(hints[0]).toHaveTextContent('提示 1')
    expect(hints[0]).toHaveTextContent('从哪看起')
    expect(hints[1]).toHaveTextContent('提示 2')
    expect(hints[1]).toHaveTextContent('这是一条没有标题的提示')
  })

  it('取卷失败时把后端短码翻成人话，而不是裸短码', async () => {
    stubTicket({ detail: 'not_found' }, 404)
    render(<TicketView stage={assessed} trackId={track.id} />)
    await waitFor(() =>
      expect(screen.getByText(/找不到这个资源/)).toBeInTheDocument(),
    )
  })

  // --- D2 防剧透回归护栏 -------------------------------------------------------

  it('D2：题面里不得出现解法 ref / 分支 / 答案内容', async () => {
    stubTicket(ticket)
    const { container } = render(<TicketView stage={assessed} trackId={track.id} />)
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
    const { container } = render(<TicketView stage={boardOnly} trackId={track.id} />)
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/-sol/)
    expect(text).not.toContain('origin/')
  })
})

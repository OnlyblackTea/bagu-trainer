import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { SimulatedBadge } from '../components/SimulatedBadge'
import { StageBoard } from '../components/StageBoard'
import { makeInstance, track } from './fixtures'

describe('SimulatedBadge（诚实占位 UI）', () => {
  it('渲染「模拟」并说明这是占位后端', () => {
    render(<SimulatedBadge what="AI 评审" />)
    const badge = screen.getByTestId('simulated-badge')
    expect(badge).toHaveTextContent('模拟')
    expect(badge).toHaveTextContent('AI 评审')
    expect(badge.getAttribute('title')).toMatch(/占位后端/)
  })
})

describe('StageBoard（屏幕 1）', () => {
  it('按 identity.id 显示已出卷阶段，并显示状态徽章', () => {
    render(
      <StageBoard
        track={track}
        stateOf={() => makeInstance({ state: 'in_progress' })}
        selectedStage={null}
        onSelect={() => {}}
        onEnroll={() => {}}
        busy={false}
      />,
    )
    const stage = screen.getByTestId('stage-stage-00-onboarding')
    expect(within(stage).getByText('环境入职：把骨架跑起来')).toBeInTheDocument()
    // 目录名也显示出来，避免"名字"与"身份"被混为一谈
    expect(within(stage).getByText('stage-00-onboarding')).toBeInTheDocument()
    expect(within(stage).getByText('开发中')).toBeInTheDocument()
  })

  it('board-only 阶段显示为未出卷，且不可点进发卷', async () => {
    const onSelect = vi.fn()
    render(
      <StageBoard
        track={track}
        stateOf={() => undefined}
        selectedStage={null}
        onSelect={onSelect}
        onEnroll={() => {}}
        busy={false}
      />,
    )
    const boardOnly = screen.getByTestId('stage-stage-03-deadlock-optional')
    expect(within(boardOnly).getByText('未出卷')).toBeInTheDocument()
    expect(within(boardOnly).queryByRole('button')).not.toBeInTheDocument()

    await userEvent.click(boardOnly)
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('未入学时给出入学入口；已入学则不显示', () => {
    const { rerender } = render(
      <StageBoard
        track={track}
        stateOf={() => undefined}
        selectedStage={null}
        onSelect={() => {}}
        onEnroll={() => {}}
        busy={false}
      />,
    )
    expect(screen.getByRole('button', { name: /入学/ })).toBeInTheDocument()

    rerender(
      <StageBoard
        track={track}
        stateOf={() => makeInstance()}
        selectedStage={null}
        onSelect={() => {}}
        onEnroll={() => {}}
        busy={false}
      />,
    )
    expect(screen.queryByRole('button', { name: /入学/ })).not.toBeInTheDocument()
  })

  it('点已出卷阶段会把它交给上层', async () => {
    const onSelect = vi.fn()
    render(
      <StageBoard
        track={track}
        stateOf={() => makeInstance()}
        selectedStage={null}
        onSelect={onSelect}
        onEnroll={() => {}}
        busy={false}
      />,
    )
    await userEvent.click(screen.getByTestId('stage-stage-01-product-query-index'))
    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ ticket_id: 'db-stage-01', stage: 'stage-01-product-query-index' }),
    )
  })

  it('显示前置依赖，便于理解解锁链', () => {
    render(
      <StageBoard
        track={track}
        stateOf={() => makeInstance()}
        selectedStage={null}
        onSelect={() => {}}
        onEnroll={() => {}}
        busy={false}
      />,
    )
    expect(screen.getByText(/前置：db-stage-00/)).toBeInTheDocument()
  })
})

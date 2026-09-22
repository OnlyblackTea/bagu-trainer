import { render, screen, waitFor, within } from '@testing-library/react'
import type { ComponentProps } from 'react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HomeView } from '../components/HomeView'
import type { AttemptSummaryOut, StageEntry } from '../types'
import { makeInstance, track } from './fixtures'

/**
 * 屏幕 0：主页 / 总览。
 *
 * 用户的抱怨是"打开平台没有主页、不能选题目、看不到进度"。所以这里逐条钉住：
 * 进度看得见、下一题有显眼入口、题目列表**自己挑**、最近练习答得出"我上次干到哪了"。
 */

function attempt(over: Partial<AttemptSummaryOut> = {}): AttemptSummaryOut {
  return {
    instance_id: 'ti-1',
    ticket_id: 'db-stage-00',
    track: track.id,
    seq: 1,
    submission_id: 'sub-1',
    created_at: '2026-09-22T17:26:55Z',
    state: 'failed',
    verdict: 'fail',
    correctness_passed: false,
    elapsed_ms: 8200,
    tampering: 0,
    ...over,
  }
}

function stubFetch(attempts: AttemptSummaryOut[] = [], status = 200) {
  const fn = vi.fn(async (url: string) => {
    if (url.startsWith('/attempts')) {
      return { ok: status < 300, status, json: async () => ({ learner: 'deployer', attempts }) }
    }
    return { ok: false, status: 404, json: async () => ({ detail: 'not_found' }) }
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

function renderHome(over: Partial<ComponentProps<typeof HomeView>> = {}) {
  const props: ComponentProps<typeof HomeView> = {
    track,
    learner: 'deployer',
    stateOf: () => undefined,
    onSelect: vi.fn(),
    onEnroll: vi.fn(),
    busy: false,
    refreshKey: 0,
    ...over,
  }
  render(<HomeView {...props} />)
  return props
}

/** stage-00 已通过、stage-01 可开始——"下一题"就该是 stage-01。 */
const onlyStage00Passed = (stage: StageEntry) =>
  makeInstance({
    ticket_id: stage.ticket_id ?? '',
    state: stage.ticket_id === 'db-stage-00' ? 'passed' : 'available',
  })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('HomeView（屏幕 0 · 主页）', () => {
  it('没入学时给的是**入学入口**，而不是一句"选一个阶段开始"', async () => {
    stubFetch()
    const props = renderHome()
    const enroll = await screen.findByTestId('enroll')
    expect(enroll).toHaveTextContent('入学（deployer）')
    await userEvent.click(enroll)
    expect(props.onEnroll).toHaveBeenCalled()
  })

  it('进度看得见：已通过 x / y 阶段', async () => {
    stubFetch()
    renderHome({
      stateOf: () => makeInstance({ state: 'passed' }),
    })
    expect(screen.getByTestId('home-view')).toHaveTextContent('已通过 2 / 2 阶段')
  })

  it('"下一题"是第一个未通过的已出卷阶段，点它直接打开', async () => {
    stubFetch()
    const props = renderHome({ stateOf: onlyStage00Passed })
    const next = screen.getByTestId('home-next')
    // stage-00 已通过 ⇒ 下一题应当是 stage-01（fixtures 里只有 00/01 是已出卷）
    expect(next).toHaveTextContent('商品搜索查询慢：索引与执行计划')
    await userEvent.click(within(next).getByRole('button'))
    expect(props.onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ ticket_id: 'db-stage-01' }),
    )
  })

  it('题目列表可以自己挑（含只上榜的阶段，但它不可点）', async () => {
    stubFetch()
    const props = renderHome()
    const assessed = screen.getByTestId('home-problem-stage-01-product-query-index')
    expect(assessed).toBeEnabled()
    await userEvent.click(assessed)
    expect(props.onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ stage: 'stage-01-product-query-index' }),
    )

    const boardOnly = screen.getByTestId('home-problem-stage-03-deadlock-optional')
    expect(boardOnly).toBeDisabled()
    expect(boardOnly).toHaveTextContent('未出卷')
  })

  it('最近练习答得出"上次干到哪了"：第几次 / 哪题 / 结论 / 阅卷耗时 / 判定面改动', async () => {
    stubFetch([
      attempt({ seq: 2, ticket_id: 'db-stage-01', verdict: 'pass', correctness_passed: true, elapsed_ms: 103129 }),
      attempt({ seq: 1, tampering: 2 }),
    ])
    renderHome()

    const rows = await screen.findAllByTestId('home-attempt')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('第 2 次')
    expect(rows[0]).toHaveTextContent('通过')
    expect(rows[0]).toHaveTextContent('103.1 s')
    // 判定面被改动 ⇒ 首页就该提示"绿也不算绿"
    expect(rows[1]).toHaveTextContent('判定面改动 2 处')
    expect(rows[1]).toHaveTextContent('未通过')
    expect(rows[1]).toHaveTextContent('阅卷红')
  })

  it('还没有提交记录时说清"挑一道题开始"，不是一片空白', async () => {
    stubFetch([])
    renderHome()
    await waitFor(() =>
      expect(screen.getByTestId('home-recent')).toHaveTextContent('还没有提交记录'),
    )
  })

  it('refreshKey 变化会重拉最近练习（提交/判定之后首页要跟着变）', async () => {
    const fetchMock = stubFetch([attempt()])
    const props = {
      track,
      learner: 'deployer',
      stateOf: () => undefined,
      onSelect: vi.fn(),
      onEnroll: vi.fn(),
      busy: false,
      refreshKey: 0,
    }
    const { rerender } = render(<HomeView {...props} />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    rerender(<HomeView {...props} refreshKey={1} />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  })
})

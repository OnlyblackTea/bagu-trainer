import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import { humanize } from '../api'
import { TrackSwitcher } from '../components/TrackSwitcher'
import type { TrackDetail, TrackSummary } from '../types'
import { makeInstance, track, trackId } from './fixtures'

const OTHER = 'cache-bagu'

function summary(over: Partial<TrackSummary>): TrackSummary {
  return {
    id: trackId,
    name: track.name,
    language: track.language,
    trust_level: track.trust_level,
    stages: track.stages.map((s) => s.stage),
    assessed_stages: 2,
    board_only_stages: 1,
    ...over,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

/** 两个 Track 的后端：同名 stage / 同名 ticket id，正是多 Track 最容易串味的形状。 */
function twoTrackBackend() {
  const other: TrackDetail = { ...track, id: OTHER, name: '缓存八股实战' }
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? 'GET').toUpperCase()
    const json = (body: unknown, status = 200) => ({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    })

    if (url.startsWith('/attempts')) {
      return json({ learner: 'deployer', attempts: [] })
    }
    if (url === '/tracks') {
      return json([summary({ id: trackId }), summary({ id: OTHER, name: other.name })])
    }
    if (url === `/tracks/${trackId}`) return json(track)
    if (url === `/tracks/${OTHER}`) return json(other)
    if (url.startsWith('/tracks/') && url.includes('/tickets/')) {
      return json({ ...track.stages[0], id: 'db-stage-00' })
    }
    if (url.startsWith('/instances?')) {
      // 后端按 track 过滤；这里断言请求真的带了 track，并只给第一个 Track 回实例
      // （第二个 Track 还没入学 ⇒ 看板出现「入学」按钮）。
      const requested = new URL(url, 'http://x').searchParams.get('track') ?? ''
      return json(requested === trackId ? [makeInstance({ track: requested })] : [])
    }
    if (url === '/enrollments' && method === 'POST') {
      const body = JSON.parse(String(init?.body)) as { track?: string }
      return json(
        {
          enrollment: { id: 'en-1', learner: 'deployer', track: body.track, created_at: '' },
          instances: [makeInstance({ track: body.track ?? '', state: 'available' })],
        },
        201,
      )
    }
    return json({ detail: 'not_found' }, 404)
  })
}

describe('TrackSwitcher（选方向 · spec §C.3 步骤 1）', () => {
  it('只有一个 Track 时不渲染——一个只有一项的下拉只是噪音', () => {
    render(
      <TrackSwitcher tracks={[summary({})]} currentId={trackId} onSelect={vi.fn()} busy={false} />,
    )
    expect(screen.queryByTestId('track-switcher')).not.toBeInTheDocument()
  })

  it('多个 Track 时逐项列出，并标出卷数/上榜数', () => {
    render(
      <TrackSwitcher
        tracks={[summary({ id: trackId }), summary({ id: OTHER, name: '缓存八股实战' })]}
        currentId={OTHER}
        onSelect={vi.fn()}
        busy={false}
      />,
    )
    expect(screen.getByText('数据库八股实战')).toBeInTheDocument()
    const current = screen.getByRole('button', { name: /缓存八股实战/ })
    expect(current).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getAllByText('2 卷')).toHaveLength(2)
  })

  it('点击切换把 track id 交回去', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(
      <TrackSwitcher
        tracks={[summary({ id: trackId }), summary({ id: OTHER, name: '缓存八股实战' })]}
        currentId={trackId}
        onSelect={onSelect}
        busy={false}
      />,
    )
    await user.click(screen.getByRole('button', { name: /缓存八股实战/ }))
    expect(onSelect).toHaveBeenCalledWith(OTHER)
  })
})

describe('App · 多 Track 切换', () => {
  it('切换 Track 会重新拉该 Track 的看板与实例（实例按 track 过滤）', async () => {
    const fetchMock = twoTrackBackend()
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    const switcher = await screen.findByTestId('track-switcher')
    expect(switcher).toBeInTheDocument()
    // 初始只拉第一个 Track 的实例。
    await waitFor(() =>
      expect(fetchMock.mock.calls.map((c) => String(c[0]))).toContain(
        `/instances?learner=deployer&track=${trackId}`,
      ),
    )

    await user.click(screen.getByRole('button', { name: /缓存八股实战/ }))

    await waitFor(() =>
      expect(fetchMock.mock.calls.map((c) => String(c[0]))).toContain(
        `/instances?learner=deployer&track=${OTHER}`,
      ),
    )
    // 报名也必须报进**当前** Track，否则学员会被报进另一个 Track 的 DAG。
    await user.click(screen.getByTestId('enroll'))
    const enrollCall = fetchMock.mock.calls.find((c) => String(c[0]) === '/enrollments')
    expect(JSON.parse(String(enrollCall?.[1]?.body))).toEqual({
      learner: 'deployer',
      track: OTHER,
    })
  })
})

describe('多 Track 相关短码的人话', () => {
  it('每个新短码都说清"发生了什么 + 我该做什么"，而不是回显短码', () => {
    for (const code of [
      'track_not_found',
      'track_not_deployed',
      'enroll_needs_track',
      'ticket_id_ambiguous',
    ]) {
      const text = humanize(code)
      expect(text).not.toContain(code)
      expect(text.length).toBeGreaterThan(10)
    }
  })
})

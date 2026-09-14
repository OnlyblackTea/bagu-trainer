import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, humanize } from '../api'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

function stubFetch(impl: (url: string, init?: RequestInit) => Promise<Response>) {
  const fn = vi.fn(impl)
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('api: 契约方法', () => {
  it('getTicket 用 identity.id 取卷', async () => {
    const fetchMock = stubFetch(async () => jsonResponse({ id: 'db-stage-01' }))
    await api.getTicket('db-stage-01')
    expect(fetchMock.mock.calls[0][0]).toBe('/tickets/db-stage-01')
  })

  it('submit 把 scenario 放 query、把请求体放 JSON', async () => {
    const fetchMock = stubFetch(async () => jsonResponse({ id: 'sub-1', seq: 1 }))
    await api.submit('ti-1', { scenario: 'pass', defense: '改了索引列序' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/instances/ti-1/submit?scenario=pass')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ pr_url: null, defense: '改了索引列序' })
  })

  it('listInstances 带 learner 查询参数', async () => {
    const fetchMock = stubFetch(async () => jsonResponse([]))
    await api.listInstances('deployer')
    expect(fetchMock.mock.calls[0][0]).toBe('/instances?learner=deployer')
  })

  it('decide 同时回带 decision 与全量 instances', async () => {
    stubFetch(async () =>
      jsonResponse({
        decision: { verdict: 'pass' },
        instances: [{ ticket_id: 'db-stage-00', state: 'passed' }],
      }),
    )
    const out = await api.decide('sub-1')
    expect(out.decision.verdict).toBe('pass')
    expect(out.instances).toHaveLength(1)
  })
})

describe('api: 错误短码翻人话（Plan 2 §4 铁律）', () => {
  it('把 illegal_transition 翻成可读说明，且不把短码当消息', async () => {
    stubFetch(async () => jsonResponse({ detail: 'illegal_transition' }, 409))
    await expect(api.start('ti-1')).rejects.toThrowError(/当前状态不允许这个操作/)
    try {
      await api.start('ti-1')
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError)
      expect((e as ApiError).detail).toBe('illegal_transition')
      expect((e as ApiError).message).not.toBe('illegal_transition')
    }
  })

  it('workspace_ref_missing 把缺失的 ref 与现有 ref 拼进消息', async () => {
    stubFetch(async () =>
      jsonResponse(
        {
          detail: 'workspace_ref_missing',
          ref: 'origin/stage-01-product-query-index',
          available: ['main', 'origin/solutions'],
        },
        500,
      ),
    )
    try {
      await api.provision('ti-1')
      throw new Error('should have thrown')
    } catch (e) {
      const err = e as ApiError
      expect(err.detail).toBe('workspace_ref_missing')
      expect(err.message).toContain('origin/stage-01-product-query-index')
      expect(err.message).toContain('origin/solutions')
      expect(err.ref).toBe('origin/stage-01-product-query-index')
    }
  })

  it('未识别短码也要给出一句可读的话', async () => {
    stubFetch(async () => jsonResponse({ detail: 'brand_new_code' }, 500))
    await expect(api.listTracks()).rejects.toThrowError(/未识别的错误：brand_new_code/)
  })

  it('非 JSON 错误体退化成 http_<status>', async () => {
    stubFetch(async () => {
      return {
        ok: false,
        status: 502,
        json: async () => {
          throw new Error('not json')
        },
      } as unknown as Response
    })
    try {
      await api.listTracks()
      throw new Error('should have thrown')
    } catch (e) {
      expect((e as ApiError).detail).toBe('http_502')
    }
  })

  it('连不上后端时提示去启动 platform_api', async () => {
    stubFetch(async () => {
      throw new TypeError('failed to fetch')
    })
    await expect(api.listTracks()).rejects.toThrowError(/确认 platform_api 已启动/)
  })
})

describe('humanize', () => {
  it('每个已知短码都有人话，且都不是短码本身', () => {
    const codes = [
      'not_found',
      'illegal_transition',
      'decide_needs_report_and_review',
      'diff_too_large',
      'git_command_failed',
      'workspace_ref_missing',
    ]
    for (const code of codes) {
      expect(humanize(code)).not.toBe(code)
      expect(humanize(code).length).toBeGreaterThan(8)
    }
  })
})

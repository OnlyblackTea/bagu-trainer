import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HistoryView } from '../components/HistoryView'
import type { AttemptOut, InstanceHistoryOut } from '../types'
import { track } from './fixtures'

function report(passed: boolean, tests: [string, string][]) {
  return {
    submission_id: 'sub-1',
    ticket: 'db-stage-00',
    ticket_version: 1,
    test_version: 1,
    engine_version: 'a-prime-0.1.0',
    learner: 'deployer',
    provenance: {
      generated_at: '2026-09-10T00:00:00Z',
      generated_by: 'stub-grading/a-prime',
      host_env: { simulated: true as const },
      runner_trust: 'self' as const,
    },
    correctness: {
      test_group: 'db-stage-00',
      passed,
      tests: tests.map(([name, status]) => ({ name, status })),
    },
    performance: null,
    lint: {},
    files_changed: ['src/db-stage-00/Solution.java'],
    out_of_scope_edits: [],
  }
}

function attempt(over: Partial<AttemptOut> = {}): AttemptOut {
  return {
    submission: {
      id: 'sub-1',
      instance_id: 'ti-1',
      seq: 1,
      pr_url: null,
      diff_ref: null,
      defense_text: null,
      created_at: '2026-09-10T00:00:00Z',
    },
    grading: {
      submission_id: 'sub-1',
      instance_id: 'ti-1',
      track: track.id,
      owner: 'default',
      status: 'done',
      step: 'done',
      detail: '阅卷完成',
      started_at: '2026-09-10T00:00:00Z',
      ended_at: '2026-09-10T00:02:00Z',
      elapsed_ms: 120_000,
      report_ready: true,
      error: null,
      steps: [],
    },
    report: report(false, [['usesIndex', 'fail']]),
    review: null,
    decision: null,
    verdict: null,
    delta: null,
    ...over,
  }
}

function history(attempts: AttemptOut[]): InstanceHistoryOut {
  return {
    instance_id: 'ti-1',
    ticket_id: 'db-stage-00',
    track: track.id,
    learner: 'deployer',
    attempts,
  }
}

function stub(body: unknown, status = 200) {
  const fn = vi.fn(async () => ({ ok: status < 300, status, json: async () => body }))
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('HistoryView（历史尝试与比对）', () => {
  it('没提交过时说清"提交一次就会留下记录"，而不是一片空白', async () => {
    stub(history([]))
    render(<HistoryView instanceId="ti-1" refreshKey={0} />)
    await waitFor(() =>
      expect(screen.getByTestId('history-view')).toHaveTextContent('还没有提交过'),
    )
  })

  it('每次尝试都列出判定、阅卷状态与耗时', async () => {
    stub(
      history([
        attempt({ verdict: 'fail', submission: { ...attempt().submission, seq: 2 } }),
      ]),
    )
    render(<HistoryView instanceId="ti-1" refreshKey={0} />)

    const row = await screen.findByTestId('history-attempt')
    expect(within(row).getByText('第 2 次')).toBeInTheDocument()
    expect(within(row).getByText('未通过')).toBeInTheDocument()
    expect(row).toHaveTextContent('阅卷 done')
    // 耗时读的是**运行记录**（含重置环境那一段），不是报告里的 maven 时间
    expect(row).toHaveTextContent('耗时 120.0 s')
  })

  it('与上一次的比对只列**真正变了**的测试，并区分"修好了"和"弄坏了"', async () => {
    stub(
      history([
        attempt({ submission: { ...attempt().submission, seq: 1 } }),
        attempt({
          submission: { ...attempt().submission, id: 'sub-2', seq: 2 },
          delta: {
            against: 1,
            elapsed_ms_before: 120_000,
            elapsed_ms_after: 20_000,
            passed_before: false,
            passed_after: true,
            tests: [
              { name: 'usesIndex', frm: 'fail', to: 'pass' },
              { name: 'stable', frm: 'pass', to: 'fail' },
              { name: 'brandNew', frm: null, to: 'fail' },
            ],
          },
        }),
      ]),
    )
    render(<HistoryView instanceId="ti-1" refreshKey={0} />)

    const deltas = await screen.findAllByTestId('history-delta')
    expect(deltas).toHaveLength(1) // 第一次没有"上一次"，不该硬编一个比对
    const delta = deltas[0]
    expect(delta).toHaveTextContent('对比第 1 次')
    // 耗时是"变了多少"而不是两个绝对值糊在一起
    expect(delta).toHaveTextContent('120.0 s → 20.0 s')
    expect(delta).toHaveTextContent('少了 100.0 s')
    expect(delta).toHaveTextContent('修好了')
    expect(delta).toHaveTextContent('弄坏了')
    expect(delta).toHaveTextContent('新增')
    expect(delta).toHaveTextContent('usesIndex')
  })

  it('历史里的报告可以展开逐条看（不只有"过没过"）', async () => {
    stub(history([attempt()]))
    const user = userEvent.setup()
    render(<HistoryView instanceId="ti-1" refreshKey={0} />)

    await user.click(await screen.findByText(/看第 1 次的阅卷报告/))
    const report = await screen.findByTestId('grading-report')
    expect(report).toHaveTextContent('usesIndex')
    expect(report).toHaveTextContent('不可信自报告')
  })

  it('没跑起来的尝试也留在历史里，并说清原因', async () => {
    stub(
      history([
        attempt({
          report: null,
          grading: {
            ...attempt().grading!,
            status: 'failed',
            error: { detail: 'grading_unavailable', reason: 'maven not found on PATH' },
          },
        }),
      ]),
    )
    render(<HistoryView instanceId="ti-1" refreshKey={0} />)

    const row = await screen.findByTestId('history-attempt')
    expect(row).toHaveTextContent('maven not found on PATH')
    expect(row).toHaveTextContent('grading_unavailable')
    expect(row).toHaveTextContent('这次没有阅卷报告')
  })

  it('refreshKey 变化会重新拉（提交/判定之后历史要跟着变）', async () => {
    const fetchMock = stub(history([attempt()]))
    const { rerender } = render(<HistoryView instanceId="ti-1" refreshKey={0} />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    rerender(<HistoryView instanceId="ti-1" refreshKey={1} />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  })
})

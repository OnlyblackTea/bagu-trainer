import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import { GradingView } from '../components/GradingView'
import type { GradingProgressOut, GradingReportOut, SubmissionOut } from '../types'
import { makeInstance, ticket, track } from './fixtures'

const SUBMISSION: SubmissionOut = {
  id: 'sub-1',
  instance_id: 'ti-1',
  seq: 1,
  pr_url: null,
  diff_ref: null,
  defense_text: null,
  created_at: '2026-09-10T00:00:00Z',
}

const STEPS = [
  {
    step: 'reset_infra',
    status: 'done',
    detail: '重置 Track 基础设施',
    started_at: '2026-09-10T00:00:00Z',
    ended_at: '2026-09-10T00:01:49Z',
    elapsed_ms: 109_000,
  },
  {
    step: 'run_tests',
    status: 'running',
    detail: 'mvn -B -Dgroups=stage01 test',
    started_at: '2026-09-10T00:01:49Z',
    ended_at: null,
    elapsed_ms: 4_000,
  },
  {
    step: 'parse',
    status: 'pending',
    detail: '',
    started_at: null,
    ended_at: null,
    elapsed_ms: null,
  },
] as const

function progress(over: Partial<GradingProgressOut> = {}): GradingProgressOut {
  return {
    submission_id: 'sub-1',
    instance_id: 'ti-1',
    track: track.id,
    owner: 'default',
    status: 'running',
    step: 'run_tests',
    detail: 'mvn -B -Dgroups=stage01 test',
    started_at: '2026-09-10T00:00:00Z',
    ended_at: null,
    elapsed_ms: 4200,
    report_ready: false,
    error: null,
    steps: STEPS.map((s) => ({ ...s })) as GradingProgressOut['steps'],
    ...over,
  }
}

function report(over: Partial<GradingReportOut> = {}): GradingReportOut {
  return {
    submission_id: 'sub-1',
    ticket: 'db-stage-00',
    ticket_version: 1,
    test_version: 1,
    engine_version: 'a-prime-0.1.0',
    learner: 'deployer',
    provenance: {
      generated_at: '2026-09-10T00:00:00Z',
      generated_by: 'docker-grading/a-prime',
      host_env: { simulated: false, exit_code: 1, infra: { reset: true } },
      runner_trust: 'self',
    },
    correctness: {
      test_group: 'stage01',
      passed: false,
      tests: [
        {
          name: 'searchReturnsCorrectOrderedResults',
          status: 'error',
          detail: 'CommunicationsException: 连接被拒',
        },
      ],
    },
    performance: null,
    lint: {},
    files_changed: ['src/main/resources/db/migration/V1__stage01_index.sql'],
    out_of_scope_edits: ['./README.md'],
    ...over,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('GradingView（屏幕 4a · 提交后的阅卷进度与报告）', () => {
  it('还没提交时不渲染', () => {
    render(<GradingView submission={null} progress={null} report={null} />)
    expect(screen.queryByTestId('grading-view')).not.toBeInTheDocument()
  })

  it('跑着的时候给出**完整**阶段链（含待跑）与当前步的说明', () => {
    render(<GradingView submission={SUBMISSION} progress={progress()} report={null} />)

    const chain = screen.getByTestId('grading-progress')
    expect(chain).toHaveTextContent('重置环境')
    expect(chain).toHaveTextContent('跑验收测试')
    expect(chain).toHaveTextContent('解析结果')
    // 未到达的步骤也要在，否则进度条画不出来。
    expect(chain).toHaveTextContent('待跑')
    expect(chain).toHaveTextContent('mvn -B -Dgroups=stage01 test')
    expect(screen.getByTestId('grading-view')).toHaveTextContent('已用 4.2 s')
  })

  it('没有运行记录时如实说不知道，而不是编一个阶段', () => {
    render(
      <GradingView
        submission={SUBMISSION}
        progress={progress({
          status: 'unknown',
          step: 'unknown',
          steps: [],
          detail: '这条提交没有阅卷运行记录（它可能来自本特性之前）。',
        })}
        report={null}
      />,
    )
    expect(screen.getByTestId('grading-not-live')).toHaveTextContent('没有阅卷运行记录')
  })

  it('被重启打断时说清"不在本进程手里"，不替它下结论', () => {
    render(
      <GradingView
        submission={SUBMISSION}
        progress={progress({
          status: 'interrupted',
          step: 'run_tests',
          detail: '服务在阅卷过程中重启',
        })}
        report={null}
      />,
    )
    expect(screen.getByTestId('grading-not-live')).toHaveTextContent('服务在阅卷过程中重启')
  })

  it('阶段条给出**每阶段各自**的耗时（"慢在哪一段"就靠它）', () => {
    render(<GradingView submission={SUBMISSION} progress={progress()} report={null} />)
    const chain = screen.getByTestId('grading-progress')
    expect(chain).toHaveTextContent('109.0 s')
    expect(chain).toHaveTextContent('4.0 s')
  })

  it('阅卷跑不起来时说清"不是你的代码没过"，并给出短码', () => {
    render(
      <GradingView
        submission={SUBMISSION}
        progress={progress({
          status: 'failed',
          step: 'run_tests',
          error: { detail: 'grading_unavailable', reason: 'maven not found on PATH' },
        })}
        report={null}
      />,
    )
    const alert = screen.getByTestId('grading-error')
    expect(alert).toHaveTextContent('不是你的代码没过')
    expect(alert).toHaveTextContent('maven not found on PATH')
    expect(alert).toHaveTextContent('grading_unavailable')
  })

  it('报告逐条给测试、改动面与越界改动；真阅卷**不**标"模拟"', () => {
    render(<GradingView submission={SUBMISSION} progress={progress({ status: 'done', report_ready: true })} report={report()} />)

    const view = screen.getByTestId('grading-report')
    expect(view).toHaveTextContent('searchReturnsCorrectOrderedResults')
    expect(view).toHaveTextContent('连接被拒')
    expect(view).toHaveTextContent('V1__stage01_index.sql')
    expect(view).toHaveTextContent('越界改动')
    expect(view).toHaveTextContent('不可信自报告')
    // host_env.simulated=false ⇒ 真阅卷，不能盖"模拟"章（§15 风险 5）。
    expect(screen.queryByTestId('simulated-badge')).not.toBeInTheDocument()
  })

  it('桩产出的报告要标"模拟"', () => {
    render(
      <GradingView
        submission={SUBMISSION}
        progress={progress({ status: 'done', report_ready: true })}
        report={report({
          provenance: {
            generated_at: '2026-09-10T00:00:00Z',
            generated_by: 'stub-grading/a-prime',
            host_env: { simulated: true },
            runner_trust: 'self',
          },
        })}
      />,
    )
    expect(screen.getByTestId('simulated-badge')).toBeInTheDocument()
  })
})

describe('App · 提交后的轮询', () => {
  /** 第一次问进度说"在跑"，第二次才说"好了"——证明界面真的在**轮询**而不是只读一次。 */
  function pollingBackend() {
    let progressCalls = 0
    return vi.fn(async (url: string) => {
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
      if (url === '/instances?learner=deployer&track=' + track.id) {
        return json([makeInstance({ state: 'in_progress' })])
      }
      if (url.match(/submit\?scenario=/)) {
        return json(
          {
            submission: SUBMISSION,
            progress: progress({ status: 'queued', step: 'queued', steps: [] }),
          },
          202,
        )
      }
      if (url.endsWith('/progress')) {
        progressCalls += 1
        return json(
          progressCalls === 1
            ? progress()
            : progress({ status: 'done', step: 'done', report_ready: true }),
        )
      }
      if (url.endsWith('/history')) {
        return json({
          instance_id: 'ti-1',
          ticket_id: 'db-stage-00',
          track: track.id,
          learner: 'deployer',
          attempts: [],
        })
      }
      if (url.endsWith('/report')) return json(report())
      return json({ detail: 'not_found' }, 404)
    })
  }

  it('一直轮询到报告就绪，并把报告渲染出来', async () => {
    const fetchMock = pollingBackend()
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByTestId('stage-stage-00-onboarding'))
    await user.click(await screen.findByRole('button', { name: /提交（跑阅卷桩）/ }))

    // 第一轮进度：进行中，报告还没好。
    await waitFor(() =>
      expect(screen.getByTestId('grading-progress')).toHaveTextContent('进行中'),
    )
    expect(screen.queryByTestId('grading-report')).not.toBeInTheDocument()

    // 第二轮：报告就绪，逐条测试渲染出来。
    await waitFor(() =>
      expect(screen.getByTestId('grading-report')).toHaveTextContent(
        'searchReturnsCorrectOrderedResults',
      ),
    )
    expect(screen.getByTestId('grading-report')).toHaveTextContent('红')
  })
})

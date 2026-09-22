import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import type { GradingReportOut, InstanceOut, SubmissionOut } from '../types'
import { makeInstance, review as reviewFixture, ticket, track } from './fixtures'

/**
 * App 装配测试：用一个**有状态的假后端**把 5 个屏幕串起来跑一遍。
 *
 * 单元测试覆盖各屏自己的行为；这个用例专门盯装配——props 接错、忘了刷新实例、
 * 决策后没用响应里的 `instances` 刷新看板，这些只有串起来才暴露。
 * 真后端 + 真浏览器的验证在 `e2e/learner-flow.spec.ts`。
 */

const STAGES = [
  { ticket_id: 'db-stage-00', prereqs: [] as string[] },
  { ticket_id: 'db-stage-01', prereqs: ['db-stage-00'] },
]

function json(body: unknown, status = 200): Response {
  return {
    ok: status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

const REPORT: GradingReportOut = {
  submission_id: 'sub-1',
  ticket: 'db-stage-00',
  ticket_version: 1,
  test_version: 1,
  engine_version: 'a-prime-0.1.0',
  learner: 'deployer',
  provenance: {
    generated_at: '2026-09-10T00:00:00Z',
    generated_by: 'stub-grading/a-prime',
    host_env: { simulated: true },
    runner_trust: 'self',
  },
  correctness: {
    test_group: 'db-stage-00',
    passed: true,
    tests: [{ name: 'tests/db-stage-00/AcceptanceTest.java', status: 'pass' }],
  },
  performance: null,
  lint: {},
  files_changed: ['src/db-stage-00/Solution.java'],
  out_of_scope_edits: [],
}

function fakeBackend() {
  let instances: InstanceOut[] = []
  let submission: SubmissionOut | null = null
  let gradingPassed = true
  let reportReady = false

  const withInstance = (id: string, patch: Partial<InstanceOut>) =>
    (instances = instances.map((i) => (i.id === id ? { ...i, ...patch } : i)))

  return async function fetchImpl(input: string | URL, init?: RequestInit): Promise<Response> {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()

    if (url.startsWith('/attempts')) {
      return json({ learner: 'deployer', attempts: [] })
    }
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

    // 取卷走**带 Track** 的权威地址：ticket id 只在 Track 内唯一（多 Track 会撞名）。
    const scoped = url.match(/^\/tracks\/([^/]+)\/tickets\/([^/]+)$/)
    if (scoped) {
      return scoped[1] === track.id && scoped[2] === ticket.id
        ? json(ticket)
        : json({ detail: 'not_found' }, 404)
    }

    if (url === '/enrollments' && method === 'POST') {
      instances = STAGES.map((s, i) =>
        makeInstance({
          id: `ti-${i}`,
          ticket_id: s.ticket_id,
          state: s.prereqs.length === 0 ? 'available' : 'locked',
          gate: s.prereqs.length === 0 ? '可开始：start 本实例。' : '未解锁：先通过全部前置阶段。',
        }),
      )
      return json({
        enrollment: { id: 'en-1', learner: 'deployer', track: track.id, created_at: '' },
        instances,
      })
    }
    if (url.startsWith('/instances?')) return json(instances)

    const start = url.match(/^\/instances\/([^/]+)\/start$/)
    if (start && method === 'POST') {
      withInstance(start[1], { state: 'in_progress', gate: '开发中：在本地 dev 分支完成。' })
      return json(instances.find((i) => i.id === start[1]))
    }

    const submit = url.match(/^\/instances\/([^/]+)\/submit\?scenario=(pass|fail)$/)
    if (submit && method === 'POST') {
      gradingPassed = submit[2] === 'pass'
      submission = {
        id: 'sub-1',
        instance_id: submit[1],
        seq: 1,
        pr_url: null,
        diff_ref: null,
        defense_text: null,
        created_at: '',
      }
      reportReady = true
      withInstance(submit[1], { state: 'submitted' })
      // 提交是**异步**的：202 + 进度，报告靠轮询 progress 拿（§C.3 步骤 6）。
      return json(
        {
          submission,
          progress: {
            submission_id: submission.id,
            instance_id: submit[1],
            track: track.id,
            owner: 'default',
            status: 'done',
            step: 'done',
            detail: '阅卷完成',
            started_at: '2026-09-10T00:00:00Z',
            ended_at: '2026-09-10T00:00:07Z',
            elapsed_ms: 5,
            report_ready: true,
            error: null,
            steps: [
              { step: 'reset_infra', status: 'done', detail: '', started_at: null, ended_at: null, elapsed_ms: 1 },
              { step: 'run_tests', status: 'done', detail: '', started_at: null, ended_at: null, elapsed_ms: 3 },
              { step: 'parse', status: 'done', detail: '', started_at: null, ended_at: null, elapsed_ms: 1 },
            ],
          },
        },
        202,
      )
    }

    const progressCall = url.match(/^\/submissions\/([^/]+)\/progress$/)
    if (progressCall) {
      return json({
        submission_id: progressCall[1],
        instance_id: submission?.instance_id ?? null,
        track: track.id,
        owner: 'default',
        status: reportReady ? 'done' : 'running',
        step: reportReady ? 'done' : 'run_tests',
        detail: '',
        started_at: '2026-09-10T00:00:00Z',
        ended_at: reportReady ? '2026-09-10T00:00:07Z' : null,
        elapsed_ms: 7,
        report_ready: reportReady,
        error: null,
        steps: [
          { step: 'reset_infra', status: 'done', detail: '', started_at: null, ended_at: null, elapsed_ms: 3 },
          { step: 'run_tests', status: reportReady ? 'done' : 'running', detail: '', started_at: null, ended_at: null, elapsed_ms: 4 },
          { step: 'parse', status: reportReady ? 'done' : 'pending', detail: '', started_at: null, ended_at: null, elapsed_ms: null },
        ],
      })
    }

    if (url.match(/^\/instances\/([^/]+)\/history$/)) {
      // 历史与进度是**同一份落库数据**，所以这个假后端也从同一份状态里出。
      const attempts = submission
        ? [
            {
              submission,
              grading: {
                submission_id: submission.id,
                instance_id: submission.instance_id,
                track: track.id,
                owner: 'default',
                status: reportReady ? 'done' : 'running',
                step: reportReady ? 'done' : 'run_tests',
                detail: '',
                started_at: '2026-09-10T00:00:00Z',
                ended_at: null,
                elapsed_ms: 7,
                report_ready: reportReady,
                error: null,
                steps: [],
              },
              report: reportReady
                ? {
                    ...REPORT,
                    correctness: { ...REPORT.correctness, passed: gradingPassed },
                  }
                : null,
              review: null,
              decision: null,
              verdict: null,
              delta: null,
            },
          ]
        : []
      return json({
        instance_id: 'ti-0',
        ticket_id: 'db-stage-00',
        track: track.id,
        learner: 'deployer',
        attempts,
      })
    }

    if (url.match(/^\/submissions\/([^/]+)\/report$/)) {
      if (!reportReady) return json({ detail: 'report_not_ready' }, 404)
      return json({
        ...REPORT,
        correctness: { ...REPORT.correctness, passed: gradingPassed },
      })
    }

    const reviewCall = url.match(/^\/submissions\/([^/]+)\/review\?scenario=(approve|request_changes)$/)
    if (reviewCall && method === 'POST') {
      if (submission) withInstance(submission.instance_id, { state: 'grading' })
      return json({ ...reviewFixture, recommendation: reviewCall[2] })
    }

    const decide = url.match(/^\/submissions\/([^/]+)\/decide$/)
    if (decide && method === 'POST') {
      if (!submission) return json({ detail: 'decide_needs_report_and_review' }, 409)
      const verdict = gradingPassed ? 'pass' : 'fail'
      instances = instances.map((i) => {
        if (i.ticket_id === 'db-stage-00') {
          return { ...i, state: verdict === 'pass' ? 'passed' : 'failed' }
        }
        if (i.ticket_id === 'db-stage-01') {
          return { ...i, state: verdict === 'pass' ? 'available' : 'locked' }
        }
        return i
      })
      return json({
        decision: {
          id: 'ad-1',
          submission_id: submission.id,
          inputs: {
            correctness_gate: gradingPassed,
            review_recommendation: 'approve',
            evidence_trust: 'self',
            tampering: [],
            config: 'auto',
          },
          verdict,
          decided_by: 'auto',
          created_at: '',
        },
        instances,
      })
    }

    const retry = url.match(/^\/instances\/([^/]+)\/retry$/)
    if (retry && method === 'POST') {
      withInstance(retry[1], { state: 'in_progress' })
      return json(instances.find((i) => i.id === retry[1]))
    }

    return json({ detail: 'not_found' }, 404)
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

const board = () => screen.getByTestId('stage-stage-00-onboarding')
const nextStage = () => screen.getByTestId('stage-stage-01-product-query-index')

describe('App 装配：跑通整条自习闭环', () => {
  it('入学 → 开始 → 提交 → 评审 → 决策 pass：看板显示下一阶段已解锁', async () => {
    vi.stubGlobal('fetch', vi.fn(fakeBackend()))
    const user = userEvent.setup()
    render(<App />)

    // 入学前没有任何实例，看板只列阶段、不带状态徽章
    await user.click(await screen.findByTestId('enroll'))

    // 入学后：根阶段可开始，后继阶段未解锁（DAG 生效）
    await waitFor(() => expect(within(board()).getByText('可开始')).toBeInTheDocument())
    expect(within(nextStage()).getByText('未解锁')).toBeInTheDocument()

    await user.click(board())
    expect(await screen.findByTestId('ticket-view')).toHaveTextContent(ticket.problem)

    await user.click(screen.getByRole('button', { name: '开始本阶段' }))
    await waitFor(() => expect(within(board()).getByText('开发中')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /提交（跑阅卷桩）/ }))
    expect(await screen.findByTestId('submission-info')).toHaveTextContent('sub-1')

    await user.click(await screen.findByRole('button', { name: /发起 AI 评审/ }))
    expect(await screen.findByTestId('review-info')).toHaveTextContent('模拟')

    await user.click(await screen.findByRole('button', { name: /取确定性结论/ }))

    expect(await screen.findByTestId('verdict')).toHaveTextContent('通过：下一阶段已解锁')
    // 关键装配点：决策后用响应里的 instances 刷新看板，而不是再发一轮请求
    await waitFor(() => {
      expect(within(board()).getByText('已通过')).toBeInTheDocument()
      expect(within(nextStage()).getByText('可开始')).toBeInTheDocument()
    })
    // 历史：这次提交留在记录里（与进度端点同一份落库数据）
    await waitFor(() =>
      expect(screen.getByTestId('history-view')).toHaveTextContent('第 1 次'),
    )
  })

  it('决策前门不可用并说明缺什么；fail 分支可 retry 回到开发中且清空旧结论', async () => {
    vi.stubGlobal('fetch', vi.fn(fakeBackend()))
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByTestId('enroll'))
    await user.click(await screen.findByTestId('stage-stage-00-onboarding'))

    expect(screen.getByRole('button', { name: /取确定性结论/ })).toBeDisabled()
    expect(screen.getByText(/还缺：提交（跑阅卷）、发起 AI 评审/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '开始本阶段' }))
    await user.selectOptions(screen.getByTestId('grading-scenario'), 'fail')
    await user.click(screen.getByRole('button', { name: /提交（跑阅卷桩）/ }))
    await user.click(await screen.findByRole('button', { name: /发起 AI 评审/ }))
    await user.click(await screen.findByRole('button', { name: /取确定性结论/ }))

    expect(await screen.findByTestId('verdict')).toHaveTextContent('未通过：请按评审意见返工')
    await waitFor(() => expect(within(board()).getByText('未通过')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /返工重来/ }))
    await waitFor(() => expect(within(board()).getByText('开发中')).toBeInTheDocument())
    // retry 后清空旧结论，避免拿上一次的提交/结论糊弄人
    expect(screen.queryByTestId('verdict')).not.toBeInTheDocument()
    expect(screen.queryByTestId('submission-info')).not.toBeInTheDocument()
  })

  it('board-only 阶段不提供取卷（点了也不进发卷，且不取工单）', async () => {
    const fetchMock = vi.fn(fakeBackend())
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByTestId('enroll'))
    await user.click(screen.getByTestId('stage-stage-03-deadlock-optional'))

    expect(screen.queryByTestId('ticket-view')).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.map((c) => String(c[0]))).not.toContain(
      `/tracks/${track.id}/tickets/stage-03-deadlock-optional`,
    )
  })
})

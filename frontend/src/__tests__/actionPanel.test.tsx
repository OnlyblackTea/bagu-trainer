import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { deriveActions } from '../actions'
import { ActionPanel } from '../components/ActionPanel'
import type {
  DecisionOut,
  GradingReportOut,
  InstanceOut,
  SubmissionOut,
} from '../types'
import { gradingReport, makeInstance, review as reviewFixture, ticket } from './fixtures'

/**
 * 右栏操作面板：界面里**所有会改动状态的动作**都在这一栏。
 *
 * 两条东西要钉住：① `deriveActions` 这套"现在能做什么"的规则（唯一一份，文档区与面板都读它）；
 * ② 面板按规则渲染按钮，并把"为什么现在取不了结论"说清楚。
 */

afterEach(() => {
  vi.unstubAllGlobals()
})

const submission: SubmissionOut = {
  id: 'sub-1',
  instance_id: 'ti-1',
  seq: 1,
  pr_url: null,
  diff_ref: null,
  defense_text: null,
  created_at: '2026-09-10T00:00:00Z',
}

const redReport: GradingReportOut = {
  ...gradingReport,
  correctness: { ...gradingReport.correctness, passed: false },
}

function decision(verdict: 'pass' | 'fail'): DecisionOut {
  return {
    decision: {
      id: 'ad-1',
      submission_id: 'sub-1',
      inputs: {
        correctness_gate: verdict === 'pass',
        review_recommendation: 'approve',
        evidence_trust: 'self',
        tampering: [],
        config: 'auto',
      },
      verdict,
      decided_by: 'auto',
      created_at: '2026-09-10T00:00:00Z',
    },
    instances: [],
  }
}

const base = {
  instance: makeInstance(),
  submission: null,
  report: null,
  review: null,
  decision: null,
  nextStage: null,
}

describe('deriveActions（能做什么，唯一一份规则）', () => {
  it.each([
    ['可开始', { ...base, instance: makeInstance({ state: 'available' }) }, { canStart: true }],
    ['开发中', { ...base, instance: makeInstance({ state: 'in_progress' }) }, { canSubmit: true }],
    [
      '已提交、还没评审',
      { ...base, instance: makeInstance({ state: 'submitted' }), submission },
      { canReview: true },
    ],
    [
      '红报告：一个动作收尾（评审+判定+返工）',
      { ...base, instance: makeInstance({ state: 'submitted' }), submission, report: redReport },
      { canRectify: true, red: true },
    ],
    [
      '证据齐了才能取结论',
      {
        ...base,
        instance: makeInstance({ state: 'grading' }),
        submission,
        report: gradingReport,
        review: reviewFixture,
      },
      { canDecide: true, missing: [] },
    ],
    [
      '未通过可返工',
      {
        ...base,
        instance: makeInstance({ state: 'failed' }),
        submission,
        report: redReport,
        review: reviewFixture,
        decision: decision('fail'),
      },
      { canRetry: true },
    ],
    [
      '通过且下一关已解锁',
      {
        ...base,
        instance: makeInstance({ state: 'passed' }),
        submission,
        report: gradingReport,
        review: reviewFixture,
        decision: decision('pass'),
        nextStage: { stage: 'stage-02', title: '下单事务要原子' },
      },
      { canGoNext: true },
    ],
  ])('%s', (_label, input, expected) => {
    expect(deriveActions(input as Parameters<typeof deriveActions>[0])).toMatchObject(expected)
  })

  it('取结论前把"还缺什么"用自然语言说出来', () => {
    const actions = deriveActions({ ...base, instance: makeInstance({ state: 'grading' }) })

    expect(actions.canDecide).toBe(false)
    expect(actions.missing).toEqual(['提交（跑自动测试）', '发起 AI 评审'])
  })
})

describe('ActionPanel 渲染', () => {
  const handlers = {
    onStart: vi.fn(),
    onSubmit: vi.fn(),
    onReview: vi.fn(),
    onFixAndResubmit: vi.fn(),
    onDecide: vi.fn(),
    onRetry: vi.fn(),
    onGoNext: vi.fn(),
  }

  function panel(instance: InstanceOut, extra: Record<string, unknown> = {}) {
    return render(
      <ActionPanel
        instance={instance}
        stage={null}
        submission={null}
        report={null}
        review={null}
        decision={null}
        busy={false}
        nextStage={null}
        {...handlers}
        {...extra}
      />,
    )
  }

  it('可开始的阶段主操作就是「开始本阶段」', async () => {
    const user = userEvent.setup()
    panel(makeInstance({ state: 'available' }))

    await user.click(screen.getByRole('button', { name: '开始本阶段' }))

    expect(handlers.onStart).toHaveBeenCalledTimes(1)
  })

  it('红报告时主按钮是「改完再交」，提交按钮不再出现', async () => {
    const user = userEvent.setup()
    panel(makeInstance({ state: 'submitted' }), { submission, report: redReport })

    await user.click(screen.getByTestId('rectify-and-retry'))

    expect(handlers.onFixAndResubmit).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('submit-answer')).not.toBeInTheDocument()
  })

  it('证据没齐时"取结论"是灰的，并写清还缺什么', () => {
    panel(makeInstance({ state: 'grading' }))

    expect(screen.getByTestId('decide')).toBeDisabled()
    expect(screen.getByText(/还缺：提交（跑自动测试）、发起 AI 评审/)).toBeInTheDocument()
  })

  it('两个桩夹具选择器跟着动作一起住在右栏', () => {
    panel(makeInstance({ state: 'in_progress' }))

    expect(screen.getByTestId('advanced-debug')).toBeInTheDocument()
    expect(screen.getByTestId('grading-scenario')).toBeInTheDocument()
    expect(screen.getByTestId('review-scenario')).toBeInTheDocument()
  })

  it('没有实例时不编造动作，只说实话', () => {
    panel(null as unknown as InstanceOut)

    expect(screen.getByText(/还没有实例/)).toBeInTheDocument()
    expect(screen.queryByTestId('submit-answer')).not.toBeInTheDocument()
  })

  it('工单标题里的阶段（stage）与实例脱钩时也不崩', () => {
    panel(makeInstance({ state: 'in_progress' }), { ticket })

    expect(screen.getByTestId('submit-answer')).toBeEnabled()
  })
})

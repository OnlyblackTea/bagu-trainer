import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DecisionView } from '../components/DecisionView'
import { ReviewView } from '../components/ReviewView'
import { WorkspaceView } from '../components/WorkspaceView'
import type { DecisionOut, SubmissionOut } from '../types'
import { makeInstance, review } from './fixtures'

function stubJson(body: unknown, status = 200) {
  const fn = vi.fn(async () => ({
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

const submission: SubmissionOut = {
  id: 'sub-1',
  instance_id: 'ti-1',
  seq: 1,
  pr_url: null,
  diff_ref: null,
  defense_text: null,
  created_at: '2026-09-10T00:00:00Z',
}

// --- 屏幕 3：工作区 / 进度 ------------------------------------------------------

describe('WorkspaceView（屏幕 3）', () => {
  it('把 gate 文案当作"下一步"显示出来（平台只 gate + 指示）', () => {
    render(
      <WorkspaceView instance={makeInstance()} busy={false} onStart={() => {}} />,
    )
    expect(screen.getByTestId('gate')).toHaveTextContent('可开始：start 本实例')
    expect(screen.getByText(/不替你跑 git 写操作/)).toBeInTheDocument()
  })

  it('只有 available 能「开始」，只有 in_progress 能「拉取工作区」', () => {
    const { rerender } = render(
      <WorkspaceView instance={makeInstance()} busy={false} onStart={() => {}} />,
    )
    expect(screen.getByRole('button', { name: '开始本阶段' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '拉取本地工作区' })).toBeDisabled()

    rerender(
      <WorkspaceView
        instance={makeInstance({ state: 'in_progress' })}
        busy={false}
        onStart={() => {}}
      />,
    )
    expect(screen.getByRole('button', { name: '开始本阶段' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '拉取本地工作区' })).toBeEnabled()
  })

  it('拉取工作区后显示路径与 diff 两端，并标「模拟」', async () => {
    stubJson({
      path: 'F:/ws/ti-1',
      branch: 'dev',
      base_ref: 'origin/stage-00-onboarding',
      head_ref: 'origin/stage-00-onboarding-sol',
      simulated: true,
    })
    render(
      <WorkspaceView
        instance={makeInstance({ state: 'in_progress' })}
        busy={false}
        onStart={() => {}}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: '拉取本地工作区' }))

    const info = await screen.findByTestId('workspace-info')
    expect(info).toHaveTextContent('F:/ws/ti-1')
    expect(info).toHaveTextContent('origin/stage-00-onboarding')
    expect(info).toHaveTextContent('模拟')
  })

  it('查看 diff 显示字符数与内容，并标「模拟」', async () => {
    stubJson({
      base_ref: 'origin/stage-00-onboarding',
      head_ref: 'origin/stage-00-onboarding-sol',
      diff: 'diff --git a/f.txt b/f.txt',
      total_chars: 27,
      truncated_files: [],
      simulated: true,
    })
    render(
      <WorkspaceView
        instance={makeInstance({ state: 'in_progress' })}
        busy={false}
        onStart={() => {}}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: '查看 diff' }))

    const info = await screen.findByTestId('diff-info')
    expect(info).toHaveTextContent('27')
    expect(info).toHaveTextContent('diff --git')
    expect(info).toHaveTextContent('模拟')
  })

  it('缺阶段分支时把 ref 与现有 ref 说清楚（后端短码 → 人话）', async () => {
    stubJson(
      {
        detail: 'workspace_ref_missing',
        ref: 'origin/stage-01-product-query-index',
        available: ['main', 'origin/solutions'],
      },
      500,
    )
    render(
      <WorkspaceView
        instance={makeInstance({ state: 'in_progress' })}
        busy={false}
        onStart={() => {}}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: '查看 diff' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('origin/stage-01-product-query-index')
    expect(alert).toHaveTextContent('origin/solutions')
    expect(alert).not.toHaveTextContent('workspace_ref_missing')
  })
})

// --- 屏幕 4：提交 / 评审 --------------------------------------------------------

describe('ReviewView（屏幕 4）', () => {
  it('只有 in_progress 能提交；只有已提交且有 submission 能评审', () => {
    render(
      <ReviewView
        instance={makeInstance()}
        submission={null}
        review={null}
        busy={false}
        onSubmit={() => {}}
        onReview={() => {}}
      />,
    )
    expect(screen.getByRole('button', { name: /提交/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /发起 AI 评审/ })).toBeDisabled()
  })

  it('两个 scenario 选择器把所选 fixture 传给上层（双分支都要能演示）', async () => {
    const onSubmit = vi.fn()
    const onReview = vi.fn()
    render(
      <ReviewView
        instance={makeInstance({ state: 'submitted' })}
        submission={submission}
        review={null}
        busy={false}
        onSubmit={onSubmit}
        onReview={onReview}
      />,
    )
    await userEvent.selectOptions(screen.getByTestId('grading-scenario'), 'fail')
    await userEvent.selectOptions(screen.getByTestId('review-scenario'), 'request_changes')
    await userEvent.click(screen.getByRole('button', { name: /发起 AI 评审/ }))

    expect(onReview).toHaveBeenCalledWith('request_changes')
  })

  it('已提交时显示 submission 信息', () => {
    render(
      <ReviewView
        instance={makeInstance({ state: 'submitted' })}
        submission={submission}
        review={null}
        busy={false}
        onSubmit={() => {}}
        onReview={() => {}}
      />,
    )
    expect(screen.getByTestId('submission-info')).toHaveTextContent('sub-1')
  })

  it('评审结果带「模拟」、维度评语与行级锚点', () => {
    render(
      <ReviewView
        instance={makeInstance({ state: 'grading' })}
        submission={submission}
        review={review}
        busy={false}
        onSubmit={() => {}}
        onReview={() => {}}
      />,
    )
    const info = screen.getByTestId('review-info')
    expect(info).toHaveTextContent('模拟')
    expect(info).toHaveTextContent('模拟评审结论')
    expect(info).toHaveTextContent('correctness：模拟评语。')
    expect(info).toHaveTextContent('src/Foo.java#L354')
    expect(info).toHaveTextContent('用户输入被拼进了 SQL')
  })

  it('评审不完整时给出被丢弃的文件', () => {
    render(
      <ReviewView
        instance={makeInstance({ state: 'grading' })}
        submission={submission}
        review={{ ...review, truncated_files: ['Huge.java'] }}
        busy={false}
        onSubmit={() => {}}
        onReview={() => {}}
      />,
    )
    expect(screen.getByTestId('review-info')).toHaveTextContent('Huge.java')
  })
})

// --- 屏幕 5：决策结果 -----------------------------------------------------------

function decision(verdict: 'pass' | 'fail', tampering: string[] = []): DecisionOut {
  return {
    decision: {
      id: 'ad-1',
      submission_id: 'sub-1',
      inputs: {
        correctness_gate: verdict === 'pass',
        review_recommendation: 'approve',
        evidence_trust: 'self',
        tampering,
        config: 'auto',
      },
      verdict,
      decided_by: 'auto',
      created_at: '2026-09-10T00:00:00Z',
    },
    instances: [],
  }
}

describe('DecisionView（屏幕 5）', () => {
  it('缺阅卷或评审时门不可用，并说明缺什么', () => {
    render(
      <DecisionView
        instance={makeInstance({ state: 'in_progress' })}
        submission={null}
        review={null}
        decision={null}
        busy={false}
        onDecide={() => {}}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByRole('button', { name: /取确定性结论/ })).toBeDisabled()
    expect(screen.getByText(/还缺：提交（跑阅卷）、发起 AI 评审/)).toBeInTheDocument()
  })

  it('阅卷 + 评审齐了且处于 grading 时可以决策', async () => {
    const onDecide = vi.fn()
    render(
      <DecisionView
        instance={makeInstance({ state: 'grading' })}
        submission={submission}
        review={review}
        decision={null}
        busy={false}
        onDecide={onDecide}
        onRetry={() => {}}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /取确定性结论/ }))
    expect(onDecide).toHaveBeenCalled()
  })

  it('pass 显示解锁与推进指示，并说明这是确定性门', () => {
    render(
      <DecisionView
        instance={makeInstance({ state: 'passed' })}
        submission={submission}
        review={review}
        decision={decision('pass')}
        busy={false}
        onDecide={() => {}}
        onRetry={() => {}}
      />,
    )
    const verdict = screen.getByTestId('verdict')
    expect(verdict).toHaveTextContent('通过：下一阶段已解锁')
    expect(verdict).toHaveTextContent(/cherry-pick/)
    expect(verdict).toHaveTextContent(/非 LLM 打分/)
  })

  it('fail 给出 retry 指示，且只有 failed 能返工', async () => {
    const onRetry = vi.fn()
    const { rerender } = render(
      <DecisionView
        instance={makeInstance({ state: 'failed' })}
        submission={submission}
        review={review}
        decision={decision('fail')}
        busy={false}
        onDecide={() => {}}
        onRetry={onRetry}
      />,
    )
    expect(screen.getByTestId('verdict')).toHaveTextContent('未通过：请按评审意见返工')
    const retry = screen.getByRole('button', { name: /返工重来/ })
    expect(retry).toBeEnabled()
    await userEvent.click(retry)
    expect(onRetry).toHaveBeenCalled()

    rerender(
      <DecisionView
        instance={makeInstance({ state: 'passed' })}
        submission={submission}
        review={review}
        decision={decision('pass')}
        busy={false}
        onDecide={() => {}}
        onRetry={onRetry}
      />,
    )
    expect(screen.getByRole('button', { name: /返工重来/ })).toBeDisabled()
  })

  it('判定面被改动时点名文件，并解释为什么"绿也不算绿"', () => {
    render(
      <DecisionView
        instance={makeInstance({ state: 'failed' })}
        submission={submission}
        review={review}
        decision={decision('fail', [
          'src/test/java/com/devteam/seckill/stage01/ProductQueryIndexAcceptanceTest.java',
        ])}
        busy={false}
        onDecide={() => {}}
        onRetry={() => {}}
      />,
    )
    const verdict = screen.getByTestId('verdict')
    expect(verdict).toHaveTextContent('未通过：判定面被改动')
    expect(verdict).toHaveTextContent('判定面改动 = 1 处')
    // 不能只说"按评审意见返工"——那不是这次失败的原因
    expect(verdict).not.toHaveTextContent('请按评审意见返工')
    const box = screen.getByTestId('tampering')
    expect(box).toHaveTextContent('绿也不算绿')
    expect(box).toHaveTextContent('ProductQueryIndexAcceptanceTest.java')
    expect(box).toHaveTextContent(/git checkout/)
  })
})

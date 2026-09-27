import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DecisionView } from '../components/DecisionView'
import { ReviewView } from '../components/ReviewView'
import { WorkspaceView } from '../components/WorkspaceView'
import type { DecisionOut, SubmissionOut } from '../types'
import { makeInstance, gradingReport, review } from './fixtures'

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
  it('把 gate 文案当作"当前阶段该做什么"显示出来（平台只 gate + 指示）', () => {
    render(
      <WorkspaceView instance={makeInstance()} busy={false} onStart={() => {}} />,
    )
    const gate = screen.getByTestId('gate')
    // 状态词与左栏看板同源（labels.ts），并给出"这一步做完会发生什么"。
    expect(gate).toHaveTextContent('当前阶段')
    expect(gate).toHaveTextContent('可开始')
    expect(gate).toHaveTextContent('可开始：start 本实例')
    expect(gate).toHaveTextContent('提交答案')
    expect(screen.getByText(/不替你跑 git 写操作/)).toBeInTheDocument()
  })

  it('按钮按使用频率分主次：低频且有副作用的动作收进「更多操作」', async () => {
    render(
      <WorkspaceView
        instance={makeInstance({ state: 'in_progress' })}
        busy={false}
        onStart={() => {}}
      />,
    )
    // 每次都用的两个留在外面：开始 / 查看 diff
    expect(screen.getByRole('button', { name: '开始本阶段' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看 diff' })).toHaveClass('ghost')
    // 「从远端同步」会 fetch 并可能快进后端那份 checkout：不许和普通操作长一样重
    const more = screen.getByText('更多操作').closest('details')
    expect(more).not.toBeNull()
    expect(more!.querySelector('[data-testid="sync-remote"]')).not.toBeNull()
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
        report={null}
        busy={false}
        onSubmit={() => {}}
        onReview={() => {}}
      />,
    )
    expect(screen.getByRole('button', { name: /提交/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /发起 AI 评审/ })).toBeDisabled()
  })

  it('用界面**真正知道**的事实做提交前检查（不编进度）', () => {
    render(
      <ReviewView
        instance={makeInstance({ state: 'submitted' })}
        submission={submission}
        review={null}
        report={gradingReport}
        busy={false}
        onSubmit={() => {}}
        onReview={() => {}}
      />,
    )
    const list = screen.getByTestId('submit-checklist')
    const items = list.querySelectorAll('li')
    expect(items[0]).toHaveClass('check--done') // 阶段已开始
    expect(items[1]).toHaveClass('check--done') // 自动测试已出结果
    expect(items[2]).not.toHaveClass('check--done') // 评审还没结论
    expect(list).toHaveTextContent('AI 评审已出结论')
  })

  it('两个 fixture 选择器收进「高级调试选项」，且仍把所选值传给上层', async () => {
    const onSubmit = vi.fn()
    const onReview = vi.fn()
    render(
      <ReviewView
        instance={makeInstance({ state: 'submitted' })}
        submission={submission}
        review={null}
        report={null}
        busy={false}
        onSubmit={onSubmit}
        onReview={onReview}
      />,
    )
    const advanced = screen.getByTestId('advanced-debug')
    expect(advanced.tagName).toBe('DETAILS')
    expect(advanced).not.toHaveAttribute('open') // 默认折叠：夹具不该抢占注意力
    expect(advanced).toHaveTextContent('占位后端')

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
        report={null}
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
        report={null}
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
        report={null}
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
  it('缺自动测试或评审时门不可用，并说明缺什么', () => {
    render(
      <DecisionView
        instance={makeInstance({ state: 'in_progress' })}
        submission={null}
        review={null}
        report={null}
        decision={null}
        busy={false}
        onDecide={() => {}}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByRole('button', { name: /取确定性结论/ })).toBeDisabled()
    expect(screen.getByText(/还缺：提交（跑自动测试）、发起 AI 评审/)).toBeInTheDocument()
  })

  it('自动测试 + 评审齐了且处于 grading 时可以决策', async () => {
    const onDecide = vi.fn()
    render(
      <DecisionView
        instance={makeInstance({ state: 'grading' })}
        submission={submission}
        review={review}
        report={gradingReport}
        decision={null}
        busy={false}
        onDecide={onDecide}
        onRetry={() => {}}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /取确定性结论/ }))
    expect(onDecide).toHaveBeenCalled()
  })

  it('pass：给出结论、逐条自动测试、评审摘要与「进入下一题」出口', async () => {
    const onGoNext = vi.fn()
    render(
      <DecisionView
        instance={makeInstance({ state: 'passed' })}
        submission={submission}
        review={review}
        report={gradingReport}
        decision={decision('pass')}
        busy={false}
        onDecide={() => {}}
        onRetry={() => {}}
        nextStage={{ stage: 'stage-01-product-query-index', title: '商品搜索查询慢' }}
        onGoNext={onGoNext}
      />,
    )
    const verdict = screen.getByTestId('verdict')
    expect(verdict).toHaveTextContent('✓')
    expect(verdict).toHaveTextContent('通过：下一阶段已解锁')
    expect(verdict).toHaveTextContent(/cherry-pick/)
    // 自动测试逐条列出报告里真实存在的用例（不是在复述"应该绿"）
    expect(verdict).toHaveTextContent('自动测试')
    expect(verdict).toHaveTextContent('acceptanceTestCase')
    // 评审结论与摘要
    expect(verdict).toHaveTextContent('AI 评审')
    expect(verdict).toHaveTextContent('模拟评审结论')
    // 审计信息折叠，不占正文
    expect(verdict).toHaveTextContent(/非 LLM 打分/)
    await userEvent.click(screen.getByTestId('go-next'))
    expect(onGoNext).toHaveBeenCalled()
  })

  it('没有已解锁的下一题时不给「进入下一题」按钮（点了没反应的按钮最糟）', () => {
    render(
      <DecisionView
        instance={makeInstance({ state: 'passed' })}
        submission={submission}
        review={review}
        report={gradingReport}
        decision={decision('pass')}
        busy={false}
        onDecide={() => {}}
        onRetry={() => {}}
      />,
    )
    expect(screen.queryByTestId('go-next')).not.toBeInTheDocument()
  })

  it('fail：给出失败原因、建议检查的文件与「返回工作区修改」，且只有 failed 能返工', async () => {
    const onRetry = vi.fn()
    const { rerender } = render(
      <DecisionView
        instance={makeInstance({ state: 'failed' })}
        submission={submission}
        review={review}
        report={{ ...gradingReport, correctness: { ...gradingReport.correctness, passed: false } }}
        decision={decision('fail')}
        busy={false}
        onDecide={() => {}}
        onRetry={onRetry}
      />,
    )
    const verdict = screen.getByTestId('verdict')
    expect(verdict).toHaveTextContent('✕')
    expect(verdict).toHaveTextContent('未通过：请按评审意见返工')
    expect(verdict).toHaveTextContent('建议先检查这次改动过的文件')
    expect(verdict).toHaveTextContent('src/db-stage-00/Solution.java')
    const retry = screen.getByTestId('retry')
    expect(retry).toBeEnabled()
    await userEvent.click(retry)
    expect(onRetry).toHaveBeenCalled()

    rerender(
      <DecisionView
        instance={makeInstance({ state: 'passed' })}
        submission={submission}
        review={review}
        report={gradingReport}
        decision={decision('pass')}
        busy={false}
        onDecide={() => {}}
        onRetry={onRetry}
      />,
    )
    // 通过之后没有"返回工作区修改"（pass 是终点，下一步是下一题）
    expect(screen.queryByTestId('retry')).not.toBeInTheDocument()
  })

  it('判定面被改动时点名文件，并解释为什么"绿也不算绿"', () => {
    render(
      <DecisionView
        instance={makeInstance({ state: 'failed' })}
        submission={submission}
        review={review}
        report={null}
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

  it('桩阅卷给出的 pass 必须自曝：结论跟你的代码无关', () => {
    // 真发生过：学员"什么都没改"提交后看见 pass，以为自己写对了——那是桩按 scenario 给的预置绿，
    // 而状态机没有回退边，于是他既误解了结论又没法接着做。
    render(
      <DecisionView
        instance={makeInstance({ state: 'passed' })}
        submission={submission}
        review={review}
        report={{
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
          correctness: { test_group: 'stage00', passed: true, tests: [] },
          performance: null,
          lint: {},
          files_changed: [],
          out_of_scope_edits: [],
        }}
        decision={decision('pass')}
        busy={false}
        onDecide={() => {}}
        onRetry={() => {}}
      />,
    )
    const note = screen.getByTestId('stub-verdict')
    expect(note).toHaveTextContent('桩')
    expect(note).toHaveTextContent(/没有关系/)
    expect(note).toHaveTextContent(/--grading docker/)
  })

  it('真阅卷（simulated=false）不贴那句警告', () => {
    render(
      <DecisionView
        instance={makeInstance({ state: 'passed' })}
        submission={submission}
        review={review}
        report={null}
        decision={decision('pass')}
        busy={false}
        onDecide={() => {}}
        onRetry={() => {}}
      />,
    )
    expect(screen.queryByTestId('stub-verdict')).not.toBeInTheDocument()
  })
})

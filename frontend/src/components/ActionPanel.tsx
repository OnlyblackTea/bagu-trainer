import { useState } from 'react'

import { deriveActions } from '../actions'
import { stateLabel } from '../labels'
import type {
  DecisionOut,
  GradingReportOut,
  GradingScenario,
  InstanceOut,
  ReviewOut,
  ReviewScenario,
  StageEntry,
  SubmissionOut,
} from '../types'

interface Props {
  /** 这一栏操作的是哪张卡（`stale` 的实例不给动作，见下）。 */
  instance: InstanceOut | null
  stage: StageEntry | null
  submission: SubmissionOut | null
  report: GradingReportOut | null
  review: ReviewOut | null
  decision: DecisionOut | null
  busy: boolean
  nextStage: { stage: string; title?: string | null } | null
  onStart: () => void
  onSubmit: (scenario: GradingScenario) => void
  onReview: (scenario: ReviewScenario) => void
  onFixAndResubmit: () => void
  onDecide: () => void
  onRetry: () => void
  onGoNext: () => void
}

/**
 * 右栏操作面板：**界面里所有"会改动状态"的动作都集中在这一栏**。
 *
 * 为什么这么分：中间那一栏改成文档阅读区之后，它的职责只有"读"——题面、你的改动、阅卷报告、
 * 评审意见、判定结论。按钮留在文档里会让人在读的时候不断被"现在该点哪个"打断，而且文档越长越
 * 难找。挪到这一栏之后，视线只需要在两个地方切换：左栏选阶段、右栏点下一步。
 *
 * 面板按**你此刻真正能做的事**渲染（:func:`deriveActions`：唯一一份规则），并把"为什么现在不能
 * 取结论、还缺什么"直接写出来——门是确定性的，学员不该靠猜按钮为什么是灰的。
 */
export function ActionPanel({
  instance,
  stage,
  submission,
  report,
  review,
  decision,
  busy,
  nextStage,
  onStart,
  onSubmit,
  onReview,
  onFixAndResubmit,
  onDecide,
  onRetry,
  onGoNext,
}: Props) {
  const [grading, setGrading] = useState<GradingScenario>('pass')
  const [reviewScenario, setReviewScenario] = useState<ReviewScenario>('approve')
  const actions = deriveActions({ instance, submission, report, review, decision, nextStage })

  if (!instance) {
    return (
      <section className="card action-panel" data-testid="action-panel">
        <h2 className="card__title">下一步</h2>
        <p className="muted">这个阶段还没有实例——回到主页点「入学」。</p>
      </section>
    )
  }

  const stale = instance.state === 'stale'

  return (
    <section className="card action-panel" data-testid="action-panel">
      <h2 className="card__title">
        下一步
        <span className={`badge badge--${instance.state}`}>{stateLabel(instance.state)}</span>
      </h2>

      {stale ? (
        <p className="muted">这个实例已作废（重做过或内容漂移）——用下面的「重做这一题」领一张新的。</p>
      ) : (
        <div className="action-panel__buttons">
          {/*
            只渲染**此刻真能做**的动作（+ 取结论那一个例外，见下）。一列灰按钮不但没用，
            还会让人以为"我是不是漏点了什么"——真踩过：已通过的阶段上还挂着提交/评审/取结论三颗
            灰按钮。取结论例外：它带着"还缺什么"，那正是门的教学点，所以留成禁用可见。
          */}
          {actions.canStart && (
            <button className="primary" disabled={busy} onClick={onStart}>
              开始本阶段
            </button>
          )}

          {actions.canRectify && (
            <button
              className="primary"
              data-testid="rectify-and-retry"
              disabled={busy}
              onClick={onFixAndResubmit}
            >
              测试红了 · 改完再交
            </button>
          )}

          {actions.canSubmit && (
            <button
              className="primary"
              data-testid="submit-answer"
              disabled={busy}
              onClick={() => onSubmit(grading)}
            >
              提交答案 →
            </button>
          )}

          {(actions.canReview || actions.canRectify) && (
            <button disabled={busy} onClick={() => onReview(reviewScenario)}>
              发起 AI 评审
            </button>
          )}

          {/*
            取结论是**唯一**一个"暂时按不动也留着"的按钮：它带着"还缺什么"，而"门要证据齐了才开"
            正是这一关的教学点（学员不该靠猜按钮为什么是灰的）。有结论之后它就消失。
          */}
          {decision === null && (
            <button
              className="primary"
              data-testid="decide"
              disabled={busy || !actions.canDecide}
              onClick={onDecide}
            >
              取确定性结论
            </button>
          )}

          {actions.canRetry && (
            <button data-testid="retry" disabled={busy} onClick={onRetry}>
              返回工作区修改
            </button>
          )}

          {actions.canGoNext && nextStage && (
            <button className="primary" data-testid="go-next" disabled={busy} onClick={onGoNext}>
              进入下一题：{nextStage.title ?? nextStage.stage} →
            </button>
          )}

          {!actions.canStart &&
            !actions.canSubmit &&
            !actions.canReview &&
            !actions.canRectify &&
            !actions.canRetry &&
            !actions.canGoNext &&
            instance.state !== 'grading' && (
              <p className="muted" style={{ margin: 0 }}>
                这一步没有要你点的东西：{stateLabel(instance.state)}。
                {instance.state === 'passed' ? '去左栏选下一关吧。' : '照上面的说明做完再来。'}
              </p>
            )}
        </div>
      )}

      {!stale && actions.missing.length > 0 && !actions.canRectify && (
        <p className="muted action-panel__missing">
          还缺：{actions.missing.join('、')}。门需要自动测试结果与评审意见同时在场。
        </p>
      )}
      {actions.canRectify && (
        <p className="muted action-panel__missing">
          红的结论已经定了（门的第一路输入不成立）：这个按钮把<b>评审与判定一起落库</b>，
          然后放你回「开发中」改代码——不用重做这一题。
        </p>
      )}
      {stage && !actions.canSubmit && !actions.canReview && !actions.canRectify && !stale && (
        <p className="muted action-panel__missing">
          只有「开发中」的实例能提交；当前是 {stateLabel(instance.state)}。
        </p>
      )}

      {/*
        两个 fixture 选择器是**桩后端**的开关（真阅卷下不影响任何结果）。它们是内部夹具、对
        "我要交答案"毫无意义，所以收进折叠区；原先长在题面那一栏，按钮挪到右栏后跟着搬过来。
      */}
      <details className="advanced" data-testid="advanced-debug">
        <summary>高级调试选项</summary>
        <p className="muted">
          下面是占位后端（`--grading stub`）的两套夹具，用来演示"红/绿"与"放行/要求修改"两条分支；
          真阅卷（`--grading docker`）下它们不影响结果。
        </p>
        <div className="row">
          <label className="muted" htmlFor="grading-scenario">
            测试场景
          </label>
          <select
            id="grading-scenario"
            data-testid="grading-scenario"
            value={grading}
            onChange={(e) => setGrading(e.target.value as GradingScenario)}
          >
            <option value="pass">pass（绿）</option>
            <option value="fail">fail（红）</option>
          </select>
          <label className="muted" htmlFor="review-scenario">
            评审场景
          </label>
          <select
            id="review-scenario"
            data-testid="review-scenario"
            value={reviewScenario}
            onChange={(e) => setReviewScenario(e.target.value as ReviewScenario)}
          >
            <option value="approve">approve（放行）</option>
            <option value="request_changes">request_changes（要求修改）</option>
          </select>
        </div>
      </details>
    </section>
  )
}

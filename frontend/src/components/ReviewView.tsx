import { useEffect, useState } from 'react'

import { stateLabel } from '../labels'
import type {
  GradingReportOut,
  GradingScenario,
  InstanceOut,
  ReviewOut,
  ReviewScenario,
  SubmissionOut,
} from '../types'
import { SimulatedBadge } from './SimulatedBadge'

interface Props {
  instance: InstanceOut
  submission: SubmissionOut | null
  review: ReviewOut | null
  /** 这次提交的自动测试报告（有它才谈得上"检查过了"）。 */
  report: GradingReportOut | null
  busy: boolean
  onSubmit: (scenario: GradingScenario) => void
  onReview: (scenario: ReviewScenario) => void
  /**
   * 测试已经红了时的**一个动作收尾**：落评审 + 取判定 + 回到「开发中」。
   *
   * 为什么要有它：红的答案交上来之后，判定**必然是 fail**（门的第一路输入就不成立，跟评审怎么
   * 说都不相干），可学员还得点两次走过场才能接着改；不点就停在「已提交」，唯一出路看着只剩
   * 「重做这一题」——真被抱怨过"每次改完都要重做一遍，太麻烦"。这里把两步合成一步，
   * 评审与判定**照旧落库**（审计链一条不少），只是不再要人陪着点。
   */
  onFixAndResubmit: () => void
}

/**
 * 屏幕 4：提交与评审（spec §C.6）。
 *
 * 这一屏要回答的是学员唯一的疑问：**「我提交以后，系统会检查什么？」** 所以它写成一条流程，
 * 而不是一堆按钮：
 *
 * 1. **提交前检查**——用界面**真正知道的事实**逐项打勾（阶段开没开、自动测试出没出结果、
 *    评审有没有结论）。不做假的进度条，也不假装检查过工作区。
 * 2. **提交**——一次明确的「提交答案 →」。
 * 3. **评审**——自动测试之后进入评审，结论是确定性门的一路输入。
 *
 * **红了走捷径、绿了走全程**：红的报告结论已定（`correctness_gate=false` ⇒ fail），所以主按钮
 * 直接是「测试红了 · 改完再交」；绿的报告不抄近路——`request_changes` 能把绿的翻成 fail，
 * 评审结论正是学员该看一眼的那路输入。
 *
 * 两个 `scenario` 选择器是**有意保留**的：A′ 的桩各备 pass/fail 与 approve/request_changes
 * 两套 fixture，只有让使用者能切换，decide 的两条分支才都能被真跑到（spec §C.4「双分支
 * fixture」，不能只测 happy path）。但它们是**内部夹具**，对"我要交答案"的学员毫无意义，
 * 所以收进「高级调试选项」里默认折叠，并用面板里的说明讲清它们只在桩后端下有效。
 */
export function ReviewView({
  instance,
  submission,
  review,
  report,
  busy,
  onSubmit,
  onReview,
  onFixAndResubmit,
}: Props) {
  const [grading, setGrading] = useState<GradingScenario>('pass')
  const [reviewScenario, setReviewScenario] = useState<ReviewScenario>('approve')

  // 换实例时把选择器复位，避免把上一阶段的意图带过来。
  useEffect(() => {
    setGrading('pass')
    setReviewScenario('approve')
  }, [instance.id])

  const canSubmit = instance.state === 'in_progress'
  const canReview = submission !== null && instance.state === 'submitted'
  /** 测试已经出结果、而且是红的 ⇒ 结论已定，主按钮改成"改完再交"。 */
  const red = report !== null && !report.correctness.passed
  const canRectify = red && canReview

  /** 三块检查项**全部**来自界面已知的事实，没有一项是编的。 */
  const checks: Array<{ label: string; done: boolean; note?: string }> = [
    {
      label: '本阶段已开始，可以在本地改代码',
      done: !['locked', 'available'].includes(instance.state),
      note: `当前：${stateLabel(instance.state)}`,
    },
    { label: '自动测试已出结果', done: report !== null },
    {
      label: 'AI 评审已出结论',
      done: review !== null,
      note: canReview ? '提交之后可以发起' : undefined,
    },
  ]

  return (
    <section className="card" data-testid="review-view">
      <h2 className="card__title">提交与评审</h2>

      <ul className="checklist" data-testid="submit-checklist">
        {checks.map((c) => (
          <li key={c.label} className={c.done ? 'check check--done' : 'check'}>
            <span aria-hidden="true">{c.done ? '✓' : '○'}</span>
            <span>{c.label}</span>
            {c.note && <span className="muted">（{c.note}）</span>}
          </li>
        ))}
      </ul>

      <p className="muted">
        提交后，平台会按本题的判据<b>重置基础设施并跑验收测试</b>（真阅卷跑 Track 的 canonical
        测试），然后把结果交给评审。两者同时在场，才能取确定性结论——门不是分数，是"证据齐了没有"。
      </p>

      <div className="row">
        {canRectify ? (
          <>
            <button
              className="primary"
              data-testid="rectify-and-retry"
              disabled={busy}
              onClick={onFixAndResubmit}
            >
              测试红了 · 改完再交
            </button>
            <button disabled={busy || !canReview} onClick={() => onReview(reviewScenario)}>
              发起 AI 评审
            </button>
            <span className="muted">
              红的结论已经定了（门的第一路输入不成立）——这个按钮把<b>评审与判定一起落库</b>，
              然后直接放你回「开发中」改代码，不用重做这一题。想先读评审意见再决定，就点右边的
              「发起 AI 评审」。
            </span>
          </>
        ) : (
          <>
            <button
              className="primary"
              data-testid="submit-answer"
              disabled={busy || !canSubmit}
              onClick={() => onSubmit(grading)}
            >
              提交答案 →
            </button>
            <button disabled={busy || !canReview} onClick={() => onReview(reviewScenario)}>
              发起 AI 评审
            </button>
            {!canSubmit && !canReview && (
              <span className="muted">
                只有「开发中」的实例能提交；当前是 {stateLabel(instance.state)}。
              </span>
            )}
          </>
        )}
      </div>

      {submission && (
        <p className="muted" style={{ marginTop: 10 }} data-testid="submission-info">
          已提交：<code>{submission.id}</code> · 第 {submission.seq} 次
        </p>
      )}

      {/*
        内部夹具收进折叠区：它们只是桩后端的开关（fixture），不是学员要理解的流程。
        真阅卷（--grading docker）下这两个选择器**不影响**结果——报告按 surefire 的真实红绿出。
      */}
      <details className="advanced" data-testid="advanced-debug">
        <summary>高级调试选项</summary>
        <p className="muted">
          下面是占位后端（`--grading stub`）的两套测试夹具，用来演示"红/绿"与
          "放行/要求修改"两条分支；换成真阅卷后它们不再影响任何结果。
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

      {review && (
        <div style={{ marginTop: 14 }} data-testid="review-info">
          <h3 className="card__title">
            AI 评审 <SimulatedBadge what="AI 评审" />
            <span
              className={`badge badge--${review.recommendation === 'approve' ? 'passed' : 'failed'}`}
            >
              {review.recommendation}
            </span>
          </h3>
          <p>{review.summary}</p>

          {review.dimension_findings.length > 0 && (
            <>
              <h4 className="card__title">按维度</h4>
              <ul className="tight">
                {review.dimension_findings.map((f) => (
                  <li key={f.dimension}>
                    <strong>{f.dimension}</strong>：{f.commentary}
                  </li>
                ))}
              </ul>
            </>
          )}

          {review.comments.length > 0 && (
            <>
              <h4 className="card__title">行级意见</h4>
              {review.comments.map((c, i) => (
                <div className="comment" key={`${c.dimension}-${i}`}>
                  <div>
                    <span className="badge">{c.dimension}</span>{' '}
                    {c.anchor && <span className="comment__anchor">{c.anchor}</span>}
                  </div>
                  {c.quote && (
                    <pre className="diff" style={{ maxHeight: 120 }}>
                      {c.quote}
                    </pre>
                  )}
                  <p>{c.text}</p>
                </div>
              ))}
            </>
          )}

          {review.truncated_files.length > 0 && (
            <div className="alert alert--info" style={{ marginTop: 10 }}>
              评审不完整：以下文件因超长被整份丢弃 —— {review.truncated_files.join('、')}
            </div>
          )}
        </div>
      )}
    </section>
  )
}

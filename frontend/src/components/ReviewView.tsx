import { useEffect, useState } from 'react'

import type {
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
  busy: boolean
  onSubmit: (scenario: GradingScenario) => void
  onReview: (scenario: ReviewScenario) => void
}

/**
 * 屏幕 4：提交 / 评审（spec §C.6）。
 *
 * 两个 `scenario` 选择器是**有意暴露**的：A′ 的桩各备 pass/fail 与
 * approve/request_changes 两套 fixture，只有让使用者能切换，decide 的两条分支才都能被
 * 真跑到（spec §C.4「双分支 fixture」，不能只测 happy path）。
 *
 * 阅卷报告与 AI 评审都标「模拟」——它们是占位后端产出的，不是真实结论。
 */
export function ReviewView({ instance, submission, review, busy, onSubmit, onReview }: Props) {
  const [grading, setGrading] = useState<GradingScenario>('pass')
  const [reviewScenario, setReviewScenario] = useState<ReviewScenario>('approve')

  // 换实例时把选择器复位，避免把上一阶段的意图带过来。
  useEffect(() => {
    setGrading('pass')
    setReviewScenario('approve')
  }, [instance.id])

  const canSubmit = instance.state === 'in_progress'
  const canReview = submission !== null && instance.state === 'submitted'

  return (
    <section className="card" data-testid="review-view">
      <h2 className="card__title">提交 / 评审</h2>

      <div className="row">
        <label className="muted" htmlFor="grading-scenario">
          阅卷 fixture
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
        <button className="primary" disabled={busy || !canSubmit} onClick={() => onSubmit(grading)}>
          提交（跑阅卷桩）
        </button>
        {!canSubmit && (
          <span className="muted">
            只有「开发中」的实例能提交；当前是 {instance.state}。
          </span>
        )}
      </div>

      {submission && (
        <p className="muted" style={{ marginTop: 10 }} data-testid="submission-info">
          已提交：<code>{submission.id}</code> · 第 {submission.seq} 次
        </p>
      )}

      <div className="row" style={{ marginTop: 12 }}>
        <label className="muted" htmlFor="review-scenario">
          评审 fixture
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
        <button disabled={busy || !canReview} onClick={() => onReview(reviewScenario)}>
          发起 AI 评审
        </button>
        {!canReview && <span className="muted">先提交，且实例处于「已提交」。</span>}
      </div>

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

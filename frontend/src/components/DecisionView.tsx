import type { DecisionOut, InstanceOut, ReviewOut, SubmissionOut } from '../types'

interface Props {
  instance: InstanceOut
  submission: SubmissionOut | null
  review: ReviewOut | null
  decision: DecisionOut | null
  busy: boolean
  onDecide: () => void
  onRetry: () => void
}

/**
 * 屏幕 5：决策结果（spec §C.6/§C.7）。
 *
 * 门是**确定性**的（`service.decide`，真代码）：阅卷绿 + 评审 approve 才 pass。pass 则
 * DAG 解锁后继阶段——所以这里要给出"下一阶段已解锁 + 你该做的 git 动作"，fail 则给 retry。
 * 决策响应里带**全量** instances，App 直接用它刷新看板，不再多发一轮请求。
 */
export function DecisionView({
  instance,
  submission,
  review,
  decision,
  busy,
  onDecide,
  onRetry,
}: Props) {
  const missing: string[] = []
  if (!submission) missing.push('提交（跑阅卷）')
  if (!review) missing.push('发起 AI 评审')

  return (
    <section className="card" data-testid="decision-view">
      <h2 className="card__title">决策结果</h2>

      <div className="row">
        <button
          className="primary"
          disabled={busy || missing.length > 0 || instance.state !== 'grading'}
          onClick={onDecide}
        >
          取确定性结论
        </button>
        <button
          disabled={busy || instance.state !== 'failed'}
          onClick={onRetry}
        >
          返工重来（retry）
        </button>
      </div>

      {missing.length > 0 && (
        <p className="muted" style={{ marginTop: 8 }}>
          还缺：{missing.join('、')}。门需要阅卷报告与评审意见同时在场。
        </p>
      )}

      {decision && (
        <div
          className={`alert ${decision.decision.verdict === 'pass' ? 'alert--ok' : 'alert--error'}`}
          style={{ marginTop: 12 }}
          data-testid="verdict"
        >
          <strong>
            {decision.decision.verdict === 'pass'
              ? '通过：下一阶段已解锁'
              : '未通过：请按评审意见返工'}
          </strong>
          <div className="muted" style={{ marginTop: 4 }}>
            判据：阅卷 correctness ={' '}
            {String(decision.decision.inputs.correctness_gate)} · 评审 ={' '}
            {decision.decision.inputs.review_recommendation} · 证据信任 ={' '}
            {decision.decision.inputs.evidence_trust}（决定性门，非 LLM 打分）
          </div>
          {decision.decision.verdict === 'pass' && (
            <p style={{ marginBottom: 0 }}>
              下一步：从<strong>下一阶段的分支</strong> cherry-pick / 提 PR 合入你的 dev，
              再「开始」下一阶段。平台不替你跑这些 git 写操作。
            </p>
          )}
          {decision.decision.verdict === 'fail' && (
            <p style={{ marginBottom: 0 }}>
              下一步：在本地 dev 上按评审意见修改，然后「返工重来」再走一遍
              提交 → 评审 → 决策。
            </p>
          )}
        </div>
      )}
    </section>
  )
}

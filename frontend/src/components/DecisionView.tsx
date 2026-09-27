import type {
  DecisionOut,
  GradingReportOut,
  InstanceOut,
  ReviewOut,
  SubmissionOut,
} from '../types'

interface Props {
  instance: InstanceOut
  submission: SubmissionOut | null
  review: ReviewOut | null
  decision: DecisionOut | null
  /** 这次判决所依据的阅卷报告——用来把"这条结论是不是桩给的"说清楚。 */
  report: GradingReportOut | null
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
  report,
  busy,
  onDecide,
  onRetry,
}: Props) {
  const missing: string[] = []
  if (!submission) missing.push('提交（跑阅卷）')
  if (!review) missing.push('发起 AI 评审')

  const inputs = decision?.decision.inputs
  const tampering = inputs?.tampering ?? []
  const tampered = tampering.length > 0
  const verdict = decision?.decision.verdict
  // 报告自己的 provenance 说了它是不是桩——判词旁边必须写清，否则"我什么都没动却 pass 了"会被
  // 读成"平台认为我写对了"（真发生过：桩按 scenario 给了预置绿）。
  const simulatedEvidence = report?.provenance.host_env.simulated === true
  const title =
    verdict === 'pass'
      ? '通过：下一阶段已解锁'
      : tampered
        ? '未通过：判定面被改动'
        : '未通过：请按评审意见返工'

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
          className={`alert ${verdict === 'pass' ? 'alert--ok' : 'alert--error'}`}
          style={{ marginTop: 12 }}
          data-testid="verdict"
        >
          <strong>{title}</strong>
          {simulatedEvidence && (
            <div className="muted" data-testid="stub-verdict" style={{ marginTop: 4 }}>
              注意：这条结论的阅卷证据来自<strong>桩</strong>（主机环境标着 <code>simulated</code>）
              ——它跟你的代码写没写、写得对不对<strong>没有关系</strong>，是预置的红绿。想要真结论，
              让后端用 <code>--grading docker</code> 跑 Track 的 canonical 测试；想接着做这一题，
              用右栏「重做这一题」（旧实例留档，重发一个）。
            </div>
          )}
          <div className="muted" style={{ marginTop: 4 }}>
            判据：阅卷 correctness = {String(inputs?.correctness_gate)} · 评审 ={' '}
            {inputs?.review_recommendation} · 证据信任 = {inputs?.evidence_trust} · 判定面改动 ={' '}
            {tampering.length} 处（决定性门，非 LLM 打分）
          </div>
          {tampered && (
            <div data-testid="tampering" style={{ marginTop: 4 }}>
              <p style={{ marginBottom: 4 }}>
                <strong>你改动了本阶段的判定面</strong>——阅卷测试、构建配置或 Track 的基础设施。
                这种情况下<strong>绿也不算绿</strong>：判定面就是判据本身，改了它就没有可判的东西了。
              </p>
              <ul style={{ margin: '0 0 8px 18px' }}>
                {tampering.map((path) => (
                  <li key={path}>
                    <code>{path}</code>
                  </li>
                ))}
              </ul>
              <p style={{ marginBottom: 0 }}>
                下一步：把它们还原成 Track 的原始版本（例如{' '}
                <code>git checkout origin/&lt;阶段分支&gt; -- &lt;文件&gt;</code>），只保留业务改动，
                再「返工重来」。
              </p>
            </div>
          )}
          {verdict === 'pass' && (
            <p style={{ marginBottom: 0 }}>
              下一步：把<strong>下一阶段的分支</strong>合入你的 dev（cherry-pick / merge 都行），
              再「开始」下一阶段。平台不替你跑这些 git 写操作。
            </p>
          )}
          {verdict === 'fail' && !tampered && (
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

import { stateLabel } from '../labels'
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
  /** 这次判决所依据的阅卷报告——既用来逐条列出自动测试，也用来把"这条结论是不是桩给的"说清楚。 */
  report: GradingReportOut | null
  busy: boolean
  onDecide: () => void
  onRetry: () => void
  /** 已解锁的下一题（没有就传 null）：把训练闭环的出口做成一个按钮，而不是让学员回左栏找。 */
  nextStage?: { stage: string; title: string | null } | null
  onGoNext?: () => void
}

/**
 * 屏幕 5：决策结果（spec §C.6/§C.7）——训练闭环的**终点**，不是后台状态面板。
 *
 * 门是**确定性**的（`service.decide`，真代码）：阅卷绿 + 评审 approve 才 pass。pass 则
 * DAG 解锁后继阶段；fail 则给返工路径。所以这一屏按"我得到什么反馈、下一步做什么"来组织：
 *
 * * 判决横幅（✓ / ✕ + 一句话结论）；
 * * **自动测试**逐条红绿（来自报告，不是复述）；
 * * **AI 评审**的结论与摘要；
 * * **下一步**：通过 → 「进入下一题 →」+ 该跑的 git 动作；未通过 → 失败原因 + 建议检查的文件 + 返回工作区；
 * * 判定依据（原始输入、证据信任级别）收进折叠区——它是审计信息，不是学员的判断依据。
 *
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
  nextStage = null,
  onGoNext,
}: Props) {
  const missing: string[] = []
  if (!submission) missing.push('提交（跑自动测试）')
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

  const tests = report?.correctness.tests ?? []

  return (
    <section className="card" data-testid="decision-view">
      <h2 className="card__title">
        决策结果
        <span className={`badge badge--${instance.state}`}>{stateLabel(instance.state)}</span>
      </h2>

      {!decision && (
        <>
          <p className="muted">
            证据齐了就可以取结论：<strong>自动测试</strong>与 <strong>AI 评审</strong>都在场时，
            这里给出一个确定性的判定（不是 LLM 打分）。
          </p>
          <div className="row">
            <button
              className="primary"
              disabled={busy || missing.length > 0 || instance.state !== 'grading'}
              onClick={onDecide}
            >
              取确定性结论
            </button>
            <button
              className="ghost"
              disabled={busy || instance.state !== 'failed'}
              onClick={onRetry}
            >
              返回工作区修改
            </button>
          </div>

          {missing.length > 0 && (
            <p className="muted" style={{ marginTop: 8 }}>
              还缺：{missing.join('、')}。门需要自动测试结果与评审意见同时在场。
            </p>
          )}
        </>
      )}

      {decision && (
        <>
          <div
            className={`verdict ${verdict === 'pass' ? 'verdict--pass' : 'verdict--fail'}`}
            data-testid="verdict"
          >
            <div className="verdict__head">
              <span className="verdict__mark" aria-hidden="true">
                {verdict === 'pass' ? '✓' : '✕'}
              </span>
              <strong>{title}</strong>
            </div>

            {simulatedEvidence && (
              <div className="muted" data-testid="stub-verdict" style={{ marginTop: 4 }}>
                注意：这条结论的阅卷证据来自<strong>桩</strong>（主机环境标着{' '}
                <code>simulated</code>）——它跟你的代码写没写、写得对不对
                <strong>没有关系</strong>，是预置的红绿。想要真结论，让后端用{' '}
                <code>--grading docker</code> 跑 Track 的 canonical 测试；想接着做这一题，
                用右栏「重做这一题」（旧实例留档，重发一个）。
              </div>
            )}

            {/* 自动测试：逐条列出**报告里真实存在的**测试结果，而不是复述"应该绿"。 */}
            <div className="verdict__block">
              <h3 className="verdict__label">自动测试</h3>
              {tests.length > 0 ? (
                <ul className="checklist">
                  {tests.map((t) => (
                    <li
                      key={t.name}
                      className={`check ${t.status === 'pass' ? 'check--done' : 'check--bad'}`}
                    >
                      <span aria-hidden="true">{t.status === 'pass' ? '✓' : '✕'}</span>
                      <code>{t.name}</code>
                      {t.detail && <span className="muted">{t.detail}</span>}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">
                  这一次的判定依据是阅卷结论（测试组{' '}
                  <code>{report?.correctness.test_group ?? '—'}</code>），报告里没有逐条用例。
                </p>
              )}
            </div>

            {/* AI 评审：结论与摘要。评审缺席时如实说缺席，而不是留空。 */}
            <div className="verdict__block">
              <h3 className="verdict__label">AI 评审</h3>
              {review ? (
                <p style={{ margin: 0 }}>
                  <span
                    className={`badge badge--${review.recommendation === 'approve' ? 'passed' : 'failed'}`}
                  >
                    {review.recommendation}
                  </span>{' '}
                  {review.summary}
                </p>
              ) : (
                <p className="muted" style={{ margin: 0 }}>
                  这次没有评审意见（桩或未启用评审挂件）。
                </p>
              )}
            </div>

            {/* 下一步：这才是学员真正要的东西 */}
            <div className="verdict__block verdict__next">
              <h3 className="verdict__label">下一步</h3>

              {verdict === 'pass' && (
                <>
                  <p style={{ marginTop: 0 }}>
                    把<strong>下一阶段的分支</strong>合入你的 <code>dev</code>（cherry-pick / merge
                    都行），再开始下一阶段。平台不替你跑这些 git 写操作。
                  </p>
                  {nextStage && (
                    <div className="row">
                      <button className="primary" data-testid="go-next" onClick={onGoNext}>
                        进入下一题：{nextStage.title ?? nextStage.stage} →
                      </button>
                    </div>
                  )}
                </>
              )}

              {verdict === 'fail' && !tampered && (
                <>
                  <p style={{ marginTop: 0 }}>
                    原因：{report && !report.correctness.passed
                      ? '验收测试没通过。'
                      : '评审要求修改。'}
                    {report?.files_changed.length
                      ? '建议先检查这次改动过的文件：'
                      : '在本地 dev 上按评审意见改完，再回来重走一遍。'}
                  </p>
                  {report && report.files_changed.length > 0 && (
                    <ul className="tight">
                      {report.files_changed.map((f) => (
                        <li key={f}>
                          <code>{f}</code>
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="row">
                    <button
                      data-testid="retry"
                      disabled={busy || instance.state !== 'failed'}
                      onClick={onRetry}
                    >
                      返回工作区修改
                    </button>
                  </div>
                </>
              )}

              {verdict === 'fail' && tampered && (
                <div data-testid="tampering">
                  <p style={{ marginTop: 0 }}>
                    <strong>你改动了本阶段的判定面</strong>——阅卷测试、构建配置或 Track
                    的基础设施。这种情况下<strong>绿也不算绿</strong>：判定面就是判据本身，
                    改了它就没有可判的东西了。
                  </p>
                  <ul className="tight">
                    {tampering.map((path) => (
                      <li key={path}>
                        <code>{path}</code>
                      </li>
                    ))}
                  </ul>
                  <p>
                    先把它们还原成 Track 的原始版本（例如{' '}
                    <code>git checkout origin/&lt;阶段分支&gt; -- &lt;文件&gt;</code>
                    ），只保留业务改动，再返回工作区。
                  </p>
                  <div className="row">
                    <button
                      data-testid="retry"
                      disabled={busy || instance.state !== 'failed'}
                      onClick={onRetry}
                    >
                      返回工作区修改
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* 审计信息折叠：它是"这条结论怎么来的"，不是学员的判断依据。 */}
            <details className="advanced">
              <summary>判定依据</summary>
              <p className="muted" style={{ marginBottom: 0 }}>
                自动测试 = {String(inputs?.correctness_gate)} · AI 评审 ={' '}
                {inputs?.review_recommendation} · 证据信任 = {inputs?.evidence_trust} · 判定面改动 ={' '}
                {tampering.length} 处（决定性门，非 LLM 打分）
              </p>
            </details>
          </div>
        </>
      )}
    </section>
  )
}

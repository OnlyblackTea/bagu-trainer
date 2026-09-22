import type { GradingReportOut } from '../types'
import { SimulatedBadge } from './SimulatedBadge'

/**
 * 阅卷报告（spec §7.4）——**不可信自报告**，但学员必须看得到。
 *
 * 单独抽成一个组件，是因为它现在有两个入口：刚跑完的那次（`GradingView`）与历史里的任意一次
 * （`HistoryView`）。报告的形状只有一处渲染逻辑，措辞才不会在两个地方各说一套。
 *
 * 真假以报告**自己的 provenance** 为准：真阅卷（`host_env.simulated === false`）不该被盖"模拟"章，
 * 那会把真结果说成假的（§15 风险 5）。
 */
export function ReportView({ report }: { report: GradingReportOut }) {
  const simulated = report.provenance.host_env.simulated === true
  return (
    <div data-testid="grading-report">
      <h3 className="card__title">
        阅卷报告
        {simulated && <SimulatedBadge what="阅卷" />}
        <span className={`badge badge--${report.correctness.passed ? 'passed' : 'failed'}`}>
          {report.correctness.passed ? '绿' : '红'}
        </span>
      </h3>
      <p className="muted">
        测试组 <code>{report.correctness.test_group}</code> · 生成于{' '}
        {report.provenance.generated_by} · <strong>不可信自报告</strong>（证据来源：
        {report.provenance.runner_trust}）
      </p>

      <ul className="tight">
        {report.correctness.tests.map((t) => (
          <li key={t.name}>
            <span className={`badge badge--${t.status === 'pass' ? 'passed' : 'failed'}`}>
              {t.status}
            </span>{' '}
            <code>{t.name}</code>
            {t.detail && <div className="muted">{t.detail}</div>}
          </li>
        ))}
      </ul>

      {report.files_changed.length > 0 && (
        <>
          <h4 className="card__title">你改了什么</h4>
          <ul className="tight">
            {report.files_changed.map((f) => (
              <li key={f}>
                <code>{f}</code>
              </li>
            ))}
          </ul>
        </>
      )}

      {report.out_of_scope_edits.length > 0 && (
        <div className="alert alert--info">
          越界改动（不影响绿红，但会被记下来）：{report.out_of_scope_edits.join('、')}
        </div>
      )}

      {report.provenance.host_env.infra != null && (
        <details>
          <summary className="muted">这次是在什么环境里跑的</summary>
          <pre className="diff" style={{ maxHeight: 200 }}>
            {JSON.stringify(report.provenance.host_env, null, 2)}
          </pre>
        </details>
      )}
    </div>
  )
}

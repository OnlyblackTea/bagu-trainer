import type { GradingProgressOut, GradingReportOut, SubmissionOut } from '../types'
import { SimulatedBadge } from './SimulatedBadge'

interface Props {
  submission: SubmissionOut | null
  progress: GradingProgressOut | null
  report: GradingReportOut | null
}

/** 阶段短标识 → 人话。后端只承诺"到了哪一步"，措辞留给这里。 */
const STEP_LABEL: Record<string, string> = {
  queued: '排队中',
  reset_infra: '重置环境',
  run_tests: '跑验收测试',
  parse: '解析结果',
  done: '完成',
  failed: '失败',
  unknown: '没有记录',
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待跑',
  running: '进行中',
  done: '完成',
  failed: '失败',
}

function seconds(ms: number | null): string {
  if (ms === null) return ''
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

/**
 * 屏幕 4a：提交后的阅卷进度与报告（spec §C.3 步骤 6）。
 *
 * 提交是**异步**的：真阅卷要 `docker compose down -v` 再 `mvn test`，几十秒起步。没有这一屏，
 * 学员点完提交只能对着一个不动的界面猜"在跑还是在死"。所以这里给出**完整阶段链**（含还没到的
 * 那几步，画得出进度条）、当前步的补充说明、已耗时。
 *
 * 报告跑完就顺手渲染（§7.4 的**不可信自报告**）：逐条测试 + 改了哪些文件 + 有没有越界。
 * 真阅卷时这是学员唯一能看出"哪条测试红了、为什么、在什么环境跑的"的地方。
 */
export function GradingView({ submission, progress, report }: Props) {
  if (!submission) return null

  const failed = progress?.status === 'failed'
  const unknown = progress?.status === 'unknown'

  return (
    <section className="card" data-testid="grading-view">
      <h2 className="card__title">
        阅卷
        <span className="badge">{submission.id}</span>
        {progress && <span className="badge">{STEP_LABEL[progress.step] ?? progress.step}</span>}
        {progress?.elapsed_ms != null && (
          <span className="badge">已用 {seconds(progress.elapsed_ms)}</span>
        )}
      </h2>

      {unknown && (
        <div className="alert alert--info" data-testid="grading-unknown">
          {progress?.detail}
        </div>
      )}

      {progress && progress.steps.length > 0 && (
        <ol className="steps" data-testid="grading-progress">
          {progress.steps.map((s) => (
            <li key={s.step} className={`step step--${s.status}`}>
              <span className="step__name">{STEP_LABEL[s.step] ?? s.step}</span>
              <span className={`badge badge--${s.status === 'done' ? 'passed' : 'stale'}`}>
                {STATUS_LABEL[s.status] ?? s.status}
              </span>
              {s.detail && <span className="muted"> {s.detail}</span>}
            </li>
          ))}
        </ol>
      )}

      {failed && progress?.error && (
        <div className="alert alert--error" data-testid="grading-error">
          阅卷<strong>没跑起来</strong>（不是你的代码没过）：{progress.error.reason}
          <br />
          <span className="muted">短码：{progress.error.detail}</span>
        </div>
      )}

      {report && (
        <div style={{ marginTop: 12 }} data-testid="grading-report">
          <h3 className="card__title">
            阅卷报告
            {/* 真假以**报告自己的 provenance** 为准：真阅卷（host_env.simulated=false）不该被标"模拟"。 */}
            {report.provenance.host_env.simulated === true && <SimulatedBadge what="阅卷" />}
            <span className={`badge badge--${report.correctness.passed ? 'passed' : 'failed'}`}>
              {report.correctness.passed ? '绿' : '红'}
            </span>
          </h3>
          <p className="muted">
            测试组 <code>{report.correctness.test_group}</code> · 生成于{' '}
            {report.provenance.generated_by} ·{' '}
            <strong>不可信自报告</strong>（证据来源：{report.provenance.runner_trust}）
          </p>

          <ul className="tight">
            {report.correctness.tests.map((t) => (
              <li key={t.name}>
                <span
                  className={`badge badge--${t.status === 'pass' ? 'passed' : 'failed'}`}
                >
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
      )}
    </section>
  )
}

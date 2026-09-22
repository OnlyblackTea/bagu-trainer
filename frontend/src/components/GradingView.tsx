import type { GradingProgressOut, GradingReportOut, SubmissionOut } from '../types'
import { ReportView } from './ReportView'

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
 * 提交是**异步**的：真阅卷要 `docker compose down -v` 再 `mvn test`，实测整次 120 秒（其中重置
 * 基础设施占 109 秒）。没有这一屏，学员点完提交只能对着一个不动的界面猜"在跑还是在死"。所以这里
 * 给出**完整阶段链**（含还没到的那几步，画得出进度条）、当前步的补充说明、已耗时，以及**每阶段**
 * 各自花了多久——"慢在哪一段"就是从这里读出来的。
 */
export function GradingView({ submission, progress, report }: Props) {
  if (!submission) return null

  const failed = progress?.status === 'failed'
  // 三种"不在正常跑"都要解释清楚：没有记录 / 不在本进程手里 / 被重启打断。
  // 缺了任何一种，学员看到的都是一个不动的界面，只能猜该等还是该重来。
  const explain = progress?.detail
  const notRunning = ['stalled', 'interrupted', 'unknown'].includes(progress?.status ?? '')

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

      {notRunning && (
        <div className="alert alert--info" data-testid="grading-not-live">
          {explain}
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
              {s.elapsed_ms != null && <span className="badge">{seconds(s.elapsed_ms)}</span>}
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
        <div style={{ marginTop: 12 }}>
          <ReportView report={report} />
        </div>
      )}
    </section>
  )
}

import { useEffect, useState } from 'react'

import { api } from '../api'
import { stateLabel } from '../labels'
import type { DiffOut, InstanceOut, PullRequestOut, WorkspaceOut } from '../types'
import { SimulatedBadge } from './SimulatedBadge'

interface Props {
  instance: InstanceOut
  busy: boolean
  onStart: () => void
}

/**
 * 屏幕 3：工作区 / 进度（spec §C.6 + §C.5）。
 *
 * 这里要讲清"平台只 gate + 指示、**不替学员跑 git 写操作**"：provision 是只读拉取，diff 是只读
 * `git diff`，而 commit / push / PR 都得学员自己驱动。所以：
 *
 * * gate 文案紧跟在状态后面，写明"下一步该做什么 git 动作"；
 * * 真 Forge 下给出**可以直接照抄**的 git 命令（凭据由工作区的 credential helper 提供，命令里
 *   不含 token）；
 * * **`模拟` 徽标逐项跟着端点的真实 `simulated` 走**——真 fork / 真 diff / 真 PR 上盖"模拟"章会
 *   把真结果说成假的（§15 风险 5），反过来桩产出没有徽标就是骗人。
 */
export function WorkspaceView({ instance, busy, onStart }: Props) {
  const [workspace, setWorkspace] = useState<WorkspaceOut | null>(null)
  const [diff, setDiff] = useState<DiffOut | null>(null)
  const [pr, setPr] = useState<PullRequestOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  // 换实例就清空本屏的资源（工作区/diff/PR 都只属于当前实例）。
  useEffect(() => {
    setWorkspace(null)
    setDiff(null)
    setPr(null)
    setError(null)
  }, [instance.id])

  async function run(fn: () => Promise<void>) {
    setWorking(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setWorking(false)
    }
  }

  const disabled = busy || working

  return (
    <section className="card" data-testid="workspace-view">
      <h2 className="card__title">
        工作区 / 进度
        <span className={`badge badge--${instance.state}`}>{stateLabel(instance.state)}</span>
      </h2>

      <div className="gate" data-testid="gate">
        <strong>下一步：</strong>
        {instance.gate}
      </div>
      <p className="muted">
        平台只 <strong>gate + 指示</strong>，不替你跑 git 写操作；commit / push / 开 PR 都由你驱动。
      </p>

      <div className="row">
        <button
          className="primary"
          onClick={onStart}
          disabled={disabled || instance.state !== 'available'}
        >
          开始本阶段
        </button>
        <button
          onClick={() => run(async () => setWorkspace(await api.provision(instance.id)))}
          disabled={disabled || instance.state !== 'in_progress'}
        >
          拉取本地工作区
        </button>
        <button
          onClick={() => run(async () => setDiff(await api.diff(instance.id)))}
          disabled={disabled}
        >
          查看 diff
        </button>
        <button
          onClick={() => run(async () => setPr(await api.createPr(instance.id)))}
          disabled={disabled}
        >
          开 PR
        </button>
      </div>

      {error && (
        <div className="alert alert--error" style={{ marginTop: 12 }} role="alert">
          {error}
        </div>
      )}

      {workspace && (
        <div style={{ marginTop: 14 }} data-testid="workspace-info">
          <h3 className="card__title">
            工作区
            {workspace.simulated ? <SimulatedBadge what="Forge" /> : <span className="badge">真 Gitea</span>}
          </h3>
          <dl className="kv">
            <dt>路径</dt>
            <dd>
              <code>{workspace.path}</code>
            </dd>
            <dt>你的分支</dt>
            <dd>
              <code>{workspace.branch}</code>
            </dd>
            <dt>骨架</dt>
            <dd>
              <code>{workspace.base_ref}</code>
            </dd>
          </dl>

          {workspace.push_hint && (
            <div className="hint-block" data-testid="push-hint">
              <p className="muted">
                在这个目录里自己提交并推送（凭据已经配好了，命令里不含 token）：
              </p>
              <pre className="cmd">{workspace.push_hint}</pre>
              <p className="muted">推完再点「开 PR」，平台会从你的分支向上游的阶段分支开单。</p>
            </div>
          )}
          {workspace.simulated && (
            <p className="muted">
              占位 Forge 用「本地 clone + 内容预置的解法 ref」冒充你的开发成果；
              换 `--forge gitea` 就是真 fork、真 push、真 PR。
            </p>
          )}
        </div>
      )}

      {pr && (
        <div style={{ marginTop: 14 }} data-testid="pr-info">
          <h3 className="card__title">
            PR
            {pr.simulated ? <SimulatedBadge what="Forge" /> : <span className="badge">真 Gitea</span>}
          </h3>
          {pr.simulated ? (
            <p>
              <code>{pr.pr_url}</code>
            </p>
          ) : (
            <p>
              <a href={pr.pr_url} target="_blank" rel="noreferrer" data-testid="pr-link">
                {pr.pr_url}
              </a>
            </p>
          )}
        </div>
      )}

      {diff && (
        <div style={{ marginTop: 14 }} data-testid="diff-info">
          <h3 className="card__title">
            改动预览
            {diff.simulated ? <SimulatedBadge what="diff 来源" /> : null}
            <span className="badge">{diff.total_chars} 字符</span>
          </h3>
          <p className="muted">
            {diff.base_ref} ↔ {diff.diff_head_ref ?? '工作区（含未提交改动）'}
            {diff.worktree && ' ⇐ 你现在看到的包含还没 commit 的改动'}
            {diff.truncated_files.length > 0 && ` · 被截断：${diff.truncated_files.join('、')}`}
          </p>
          {diff.diff.trim() === '' ? (
            <div className="alert alert--info" data-testid="diff-empty">
              工作区里还没有改动——去 <code>src/</code> 下把这一题写完，再回来看。
            </div>
          ) : (
            <pre className="diff">{diff.diff}</pre>
          )}
        </div>
      )}
    </section>
  )
}

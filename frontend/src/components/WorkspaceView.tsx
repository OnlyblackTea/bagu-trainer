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
 * 这里要讲清"平台只 gate + 指示、**不替学员跑 git 写操作**"：provision 是只读拉取（占位
 * 用本地 clone），diff 是只读 `git diff`，而 commit / merge / cherry-pick 都得学员在自己
 * 机器上做。所以 gate 文案紧跟在状态后面，且写明"下一步该做什么 git 动作"。
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
        平台只 <strong>gate + 指示</strong>，不替你跑 git 写操作；commit / merge /
        cherry-pick 都在你自己的机器上做。
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
        <button onClick={() => run(async () => setPr(await api.createPr(instance.id)))} disabled={disabled}>
          提 PR（占位）
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
            工作区 <SimulatedBadge what="Forge" />
          </h3>
          <dl className="kv">
            <dt>路径</dt>
            <dd>
              <code>{workspace.path}</code>
            </dd>
            <dt>分支</dt>
            <dd>
              <code>{workspace.branch}</code>
            </dd>
            <dt>diff 两端</dt>
            <dd>
              <code>
                {workspace.base_ref} ↔ {workspace.head_ref}
              </code>
            </dd>
          </dl>
          <p className="muted">
            占位 Forge 用「本地 clone + 内容预置的解法 ref」冒充你的开发成果；
            真实 Gitea fork 会在未来切片接入。
          </p>
        </div>
      )}

      {pr && (
        <div style={{ marginTop: 14 }} data-testid="pr-info">
          <h3 className="card__title">
            PR <SimulatedBadge what="Forge" />
          </h3>
          <p>
            <code>{pr.pr_url}</code>
          </p>
        </div>
      )}

      {diff && (
        <div style={{ marginTop: 14 }} data-testid="diff-info">
          <h3 className="card__title">
            改动预览 <SimulatedBadge what="diff 来源" />
          </h3>
          <p className="muted">
            {diff.base_ref} ↔ {diff.head_ref} · {diff.total_chars} 字符
            {diff.truncated_files.length > 0 && ` · 被截断：${diff.truncated_files.join('、')}`}
          </p>
          <pre className="diff">{diff.diff}</pre>
        </div>
      )}
    </section>
  )
}

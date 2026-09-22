import { useEffect, useState } from 'react'

import { api } from '../api'
import { stateLabel } from '../labels'
import type {
  DiffOut,
  InstanceOut,
  PullRequestOut,
  WorkspaceOut,
  WorkspaceSyncOut,
} from '../types'
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
  const [registered, setRegistered] = useState<
    (WorkspaceOut & { workspace_path: string | null }) | null
  >(null)
  const [path, setPath] = useState('')
  const [diff, setDiff] = useState<DiffOut | null>(null)
  const [pr, setPr] = useState<PullRequestOut | null>(null)
  /** 与远端 `dev` 的对账结果：来自显式同步，或来自 diff 的只读探针。 */
  const [sync, setSync] = useState<WorkspaceSyncOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  // 换实例就清空本屏的资源（工作区/diff/PR 都只属于当前实例）。
  useEffect(() => {
    setWorkspace(null)
    setRegistered(null)
    setPath('')
    setDiff(null)
    setPr(null)
    setSync(null)
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
          onClick={() =>
            run(async () => {
              const body = await api.diff(instance.id)
              setDiff(body)
              setSync(body.sync)
            })
          }
          disabled={disabled}
        >
          查看 diff
        </button>
        <button
          data-testid="sync-remote"
          title="代码写在别的机器上时用它：fetch 远端 dev，且只在无损时快进"
          onClick={() =>
            run(async () => {
              const body = await api.syncWorkspace(instance.id)
              setWorkspace(body)
              setSync(body.sync)
              setDiff(null) // 同步之后旧的 diff 不作数了
            })
          }
          disabled={disabled}
        >
          从远端同步
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

      {sync && (
        <div
          className={`alert ${sync.remote_only_changes ? 'alert--error' : 'alert--info'}`}
          style={{ marginTop: 12 }}
          data-testid="sync-info"
        >
          <div>
            <strong>与远端对账：</strong>
            {sync.note}
          </div>
          {sync.remote_only_changes && (
            <div className="muted">
              所以这次预览/阅卷看到的还是<strong>旧</strong>代码——点「从远端同步」，或直接提交
              （提交前平台会在无损时自动快进）。
            </div>
          )}
          {sync.revision && (
            <div className="muted">
              对着的版本：<code>{sync.revision.slice(0, 12)}</code>
              {sync.local_ahead > 0 && ` · 本地领先远端 ${sync.local_ahead} 个提交（还没 push）`}
            </div>
          )}
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

          {workspace.clone_hint && (
            <div className="hint-block" data-testid="clone-hint">
              <p className="muted">
                想在自己的目录里做这一题？把下面三行照抄（平台不替你 clone，也不碰你机器上的
                git 凭据）：
              </p>
              <pre className="cmd">{workspace.clone_hint}</pre>
              <div className="row">
                <input
                  className="input"
                  data-testid="workspace-path"
                  placeholder="拉好之后，把那个目录的路径登记回来（后端这台上能看到的绝对路径）"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  disabled={disabled}
                />
                <button
                  onClick={() =>
                    run(async () => {
                      setRegistered(await api.attachWorkspace(instance.id, path.trim()))
                      setDiff(null)
                    })
                  }
                  disabled={disabled || path.trim() === ''}
                >
                  用这个目录
                </button>
                {registered?.workspace_path && (
                  <button
                    onClick={() =>
                      run(async () => {
                        setRegistered(await api.attachWorkspace(instance.id, null))
                        setPath('')
                        setDiff(null)
                      })
                    }
                    disabled={disabled}
                  >
                    改回平台准备的目录
                  </button>
                )}
              </div>
              <p className="muted">
                现在用的是：
                {registered?.workspace_path ? (
                  <strong>你自己的目录</strong>
                ) : (
                  <strong>平台准备的工作区</strong>
                )}{' '}
                <code>{registered?.path ?? workspace.path}</code>
              </p>
            </div>
          )}

          {workspace.clone_hint && (
            <div className="hint-block" data-testid="remote-hint">
              <p className="muted">
                在<strong>另一台机器</strong>上写也完全可以：把改动 push 到你的 fork，再点
                「从远端同步」（或直接提交——提交前平台会自动做同一件事）。这台机器只要能从 Gitea
                读到那次 push 就行，你<strong>不需要</strong>把那个路径登记过来：登记只适用于
                “路径就在后端这台机器上”的情况，好处是 diff 能看见你还没 commit 的改动。
              </p>
            </div>
          )}

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
            diff.sync?.remote_only_changes ? (
              <div className="alert alert--error" data-testid="diff-behind">
                远端 {diff.head_ref} 有 {diff.sync.remote_ahead} 个提交不在这份工作区里：你多半是在
                <strong>另一台机器</strong>上写的（那边 push 了，后端这份 checkout 还是旧的）。
                点「从远端同步」，或直接提交——提交前平台会在无损时自动快进。
              </div>
            ) : (
              <div className="alert alert--info" data-testid="diff-empty">
                工作区里还没有改动——去 <code>src/</code> 下把这一题写完，再回来看。
              </div>
            )
          ) : (
            <pre className="diff">{diff.diff}</pre>
          )}
        </div>
      )}
    </section>
  )
}

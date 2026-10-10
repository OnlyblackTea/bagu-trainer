import { useEffect, useState } from 'react'

import { api } from '../api'
import { stateLabel } from '../labels'
import type {
  DiffOut,
  InstanceOut,
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
 * `git diff`，而 commit / push 都得学员自己驱动。所以：
 *
 * * gate 文案紧跟在状态后面，写明"下一步该做什么 git 动作"；
 * * 真 Forge 下给出**可以直接照抄**的 git 命令（凭据由工作区的 credential helper 提供，命令里
 *   不含 token）；
 * * **`模拟` 徽标逐项跟着端点的真实 `simulated` 走**——真仓库 / 真 diff 上盖"模拟"章会
 *   把真结果说成假的（§15 风险 5），反过来桩产出没有徽标就是骗人。
 */
export function WorkspaceView({ instance, busy, onStart }: Props) {
  const [workspace, setWorkspace] = useState<WorkspaceOut | null>(null)
  const [registered, setRegistered] = useState<
    (WorkspaceOut & { workspace_path: string | null }) | null
  >(null)
  const [path, setPath] = useState('')
  const [diff, setDiff] = useState<DiffOut | null>(null)
  /** 与远端 `dev` 的对账结果：来自显式同步，或来自 diff 的只读探针。 */
  const [sync, setSync] = useState<WorkspaceSyncOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  // 换实例就清空本屏的资源（工作区/diff 都只属于当前实例）。
  useEffect(() => {
    setWorkspace(null)
    setRegistered(null)
    setPath('')
    setDiff(null)
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
        工作区
        <span className={`badge badge--${instance.state}`}>{stateLabel(instance.state)}</span>
      </h2>

      {/*
        「下一步」不是一个状态描述，而是一条行动指导：所以这里把**当前阶段**（后端给的 gate 文案，
        它说的是"现在该做什么 git 动作"）与**完成后会发生什么**并排放在一起，读的人不用自己拼。
      */}
      <div className="stage-state" data-testid="gate">
        <div className="stage-state__now">
          <span className="muted">当前阶段</span>
          <p className="stage-state__gate">{instance.gate}</p>
        </div>
        <p className="muted stage-state__after">
          这一步做完：在本地跑验收测试 → 提交答案 → 等自动测试与评审的结论。
        </p>
      </div>
      <p className="muted">
        平台只 <strong>gate + 指示</strong>，不替你跑 git 写操作；commit / push 都由你驱动。
      </p>

      {/*
        按**使用频率**分主次，而不是把四个动作摊平成等权重的四个按钮：
        开始/查看 diff 是每次都要做的；「拉取本地工作区」「从远端同步」是特定处境下才用的高级动作
        ——尤其「从远端同步」会 fetch 并可能快进后端那份 checkout，不该和普通操作长一样重。
      */}
      <div className="row">
        <button
          className="primary"
          onClick={onStart}
          disabled={disabled || instance.state !== 'available'}
        >
          开始本阶段
        </button>
        <button
          className="ghost"
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

        <details className="more-actions">
          <summary>更多操作</summary>
          <div className="row">
            <button
              onClick={() => run(async () => setWorkspace(await api.provision(instance.id)))}
              disabled={disabled || instance.state !== 'in_progress'}
            >
              拉取本地工作区
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
          </div>
          <p className="muted">
            「拉取本地工作区」看的是后端那份 checkout（在哪台机器上、用什么分支）；
            「从远端同步」会 fetch 你 push 的 <code>dev</code>，并且<span>只在无损时</span>快进——
            工作树不干净或本地领先时它一个字都不动。
          </p>
        </details>
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
          {/*
            两条话术分开：**分叉**时「从远端同步」这个按钮按不动（不是无损，平台不会替你 merge），
            劝人点它等于把人指进死胡同——真发生过（他点了、提交了，阅的却是本地这份旧代码）。
          */}
          {sync.diverged ? (
            <div className="muted" data-testid="sync-diverged">
              分叉状态下这份 checkout 会一直当"本地那份"用：要么在你写代码那台机器上合好再 push，
              要么用右栏「重做这一题」——平台会给新实例<strong>重新 clone</strong> 一份远端 dev。
            </div>
          ) : (
            sync.remote_only_changes && (
              <div className="muted">
                所以这次预览/阅卷看到的还是<strong>旧</strong>代码——点「从远端同步」，或直接提交
                （提交前平台会在无损时自动快进）。
              </div>
            )
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
              {workspace.host_path && workspace.host_path !== workspace.path && (
                <span className="muted">
                  {' '}
                  —— 容器内的路径；在<strong>那台机器</strong>上是{' '}
                  <code>{workspace.host_path}</code>
                </span>
              )}
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

          {!workspace.simulated && (
            <p className="muted" data-testid="host-path-note">
              上面这个路径在<strong>后端那台机器</strong>上。你如果在**自己的电脑**上写代码，用下面的
              clone 命令拉一份、改完 push 上来就行——提交时平台会自动把这份 checkout 对齐到你 push
              的版本（也可以点「从远端同步」手动对齐）。容器里那份是**阅卷用的**，不必去改它。
            </p>
          )}

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
                在<strong>另一台机器</strong>上写也完全可以：把改动 push 到内容仓的 <code>dev</code>，再点
                「从远端同步」（或直接提交——提交前平台会自动做同一件事）。这台机器只要能从 Gitea
                读到那次 push 就行，你<strong>不需要</strong>把那个路径登记过来：登记只适用于
                “路径就在后端这台机器上”的情况，好处是 diff 能看见你还没 commit 的改动。
              </p>
            </div>
          )}

          {workspace.merge_hint && (
            <div className="hint-block" data-testid="merge-hint">
              <p className="muted">
                进入本阶段前先把<strong>本阶段基线</strong>合进 <code>dev</code>：验收测试就在那个分支上，
                不合进来工作区里没有判据（照抄下面这条就行，它只加不删、不会和你写的实现打架）：
              </p>
              <pre className="cmd">{workspace.merge_hint}</pre>
            </div>
          )}

          {workspace.push_hint && (
            <div className="hint-block" data-testid="push-hint">
              <p className="muted">
                在这个目录里自己提交并推送（凭据已经配好了，命令里不含 token）：
              </p>
              <pre className="cmd">{workspace.push_hint}</pre>
              <p className="muted">
                推完就能回平台「提交」——后端会 fetch 你的 <code>dev</code> 并按它阅卷。
              </p>
            </div>
          )}
          {workspace.simulated && (
            <p className="muted">
              占位 Forge 用「本地 clone + 内容预置的解法 ref」冒充你的开发成果；
              换 `--forge gitea` 就是真仓库、真 push、真 diff。
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
            {diff.diff_base_kind === 'stage_start' &&
              ' · 比的是「你开始这一阶段时的那份 dev」，所以这里只有本阶段的改动'}
            {diff.diff_base_kind === 'stage_branch' && (
              <>
                {' · 平台没能在你开工前记下那份 dev（你在登记这次尝试之前就把代码写好推上来了），'}
                <span data-testid="diff-base-degraded">
                  所以这里比的是<b>相对本阶段基线的全部改动</b>——会多带你之前几关的成果
                </span>
              </>
            )}
            {diff.truncated_files.length > 0 && ` · 被截断：${diff.truncated_files.join('、')}`}
          </p>
          {diff.diff.trim() === '' ? (
            diff.sync?.diverged ? (
              <div className="alert alert--error" data-testid="diff-diverged">
                这份工作区与远端 {diff.head_ref} <strong>分叉</strong>了：本地领先{' '}
                {diff.sync.local_ahead}、远端领先 {diff.sync.remote_ahead}，平台不替你 merge。
                所以 diff 是空的（两边相减的结果都落在对方那边）。先把两边合好，或用「重做这一题」
                让平台重新 clone 一份远端 dev。
              </div>
            ) : diff.sync?.remote_only_changes ? (
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

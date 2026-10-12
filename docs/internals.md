# 内部机制（端点 · 存储 · Forge）

面向"要读代码 / 要排障"的人。**怎么装、怎么跑**在 [`deployment.md`](deployment.md)，
**为什么这么设计**在 [`paper-platform-design.md`](paper-platform-design.md)。本文只讲"实际是什么样"。

## 提交是异步的（进度反馈）

`POST /instances/{id}/submit` 立刻回 **202**，阅卷在后台跑；学员轮询
`GET /submissions/{id}/progress` 看"到哪一步了"。真阅卷要 `docker compose down -v` 再
`mvn test`，几十到上百秒——同步做的话这一个请求就干挂着，中间没有任何反馈。

| 端点 | 说什么 |
|---|---|
| `POST /instances/{id}/submit` → 202 | `{submission, progress}`：提交已受理 + 当前进度。**不等阅卷** |
| `GET /submissions/{id}/progress` | `status`（queued/running/done/failed/**interrupted**/**stalled**/unknown）、`step`、`elapsed_ms`、`report_ready`、**完整阶段链** `steps[]`（每阶段自带起止时间与耗时） |
| `GET /submissions/{id}/report` | 跑完前 404 `report_not_ready`；跑挂了按失败短码报 503/500；从来没有过 404 `report_not_found` |
| `GET /instances/{id}/history` | 该实例**全部尝试**（submission + 运行记录 + 报告 + 评审 + 判定）与**与上一次的比对** |

阶段（后端只承诺短标识，措辞在前端）：`reset_infra` 重置环境 → `run_tests` 跑验收测试 →
`parse` 解析结果。

三条刻意的取舍：

- **单线程**（`max_workers=1`）：环境重置会 `down -v` 掉 Track 的**共享**基础设施，maven 还要抢
  端口与 `~/.m2`。两个阅卷并行不是更快，是互相砸场子——一个把另一个的数据库删了。
- **运行记录落库**：阅卷是分钟级的事，进程中途重启不能只剩一句"我不知道"。
- **跑不起来 ≠ 没过**：异步之后异常处理器够不着后台线程，所以失败在登记处就地分类成短码
  （`grading_unavailable` / `workspace_ref_missing` / …），`report`、`progress`、历史都原样报出来。

> `--grading stub` 下提交是**瞬时**的（桩不 sleep、不假装在干活），所以进度条一闪而过——
> 想看真的进度得换 `--grading docker`。实测（VM，真 MySQL + maven）：`submit` 80 ms 返回 202，
> 而整次阅卷 120 s，轮询依次看到 `reset_infra`(0.1 s) → `run_tests`(109 s) → `done`(120 s)，
> 报告带 `simulated:false` / `exit_code:1` / `infra.reset:true` 与逐条测试结果。

### 重启后的三种口径

都不编：`interrupted` = 本进程启动时清扫了自己的孤儿记录（确定被打断）；`stalled` = 有活跃记录但
不在本进程手里（重启过，或另一个平台实例在跑——不替它下结论）；`unknown` = 压根没有运行记录
（这条提交来自本特性之前）。多实例共用一个库时给每个实例不同的 `--instance-id` /
`BAGU_INSTANCE_ID`，清扫只动自己名下的。

## 历史查询与比对

刷同一道题会提交很多次，而**"这次和上次差在哪"正是练习的价值**。所以每次尝试都连同**运行记录**
一起落库，`GET /instances/{id}/history` 按 `seq` 给出全部尝试：

```jsonc
{ "attempts": [
  { "submission": {"seq": 1}, "grading": {"status": "done", "elapsed_ms": 103129,
      "steps": [{"step": "reset_infra", "elapsed_ms": 93559},
                {"step": "run_tests",   "elapsed_ms": 9239},
                {"step": "parse",       "elapsed_ms": 323}]},
    "report": { /* 不可信自报告，逐条测试都在 */ },
    "verdict": "fail", "delta": null },        // 第一次没有"上一次"
  { "submission": {"seq": 2}, "verdict": "pass",
    "delta": { "against": 1, "elapsed_ms_before": 103129, "elapsed_ms_after": 12088,
               "tests": [{"name": "explainUsesIndex", "frm": "fail", "to": "pass"}] } }
]}
```

- **比对只列真正变了的测试**：真 Track 的 canonical 测试常有十几条，把没动的也列出来等于没说。
  红→绿（`fixed`）、绿→红（`broke`）、新增、消失**分开说**——它们是相反的消息。
- **耗时优先取运行记录**：它含"重置基础设施"那一段，而那正是"阅卷为什么这么慢"的答案
  （实测 103 秒里 93.6 秒在重置容器）。报告里的 `duration_s` 只是 maven 那一段，故只作兜底。
- 前端的历史面板列出每次尝试，可展开看那次的报告，并显示与上一次的差异。

## 进度存在哪（持久化）

```bash
python -m platform_api.serve --content-dir ../seckill-lab \
    --store postgres --database-url 'postgresql://bagu:pw@127.0.0.1:5432/bagu'
```

| | `memory` | `postgres` |
|---|---|---|
| 重启后 | 一切归零 | 实例状态 / 提交 / 报告 / 判定 / 已解锁的后继都还在 |
| 取号 | 进程内计数器 | `bagu_id_seq`，落库（所以重启后接着发新号，**不会覆盖**上一轮的实例） |
| 表 | — | `bagu_documents`（`kind, id` 主键 + `payload jsonb`，按 learner/ticket/instance 建索引）+ `bagu_id_seq` |
| 依赖 | — | `pip install 'bagu-trainer[postgres]'`（psycopg3） |
| `GET /meta` | `"store_backend": "memory"` | `"store_backend": "postgres"` |

`/meta` 会**如实**报当前用的是哪个——学员有权知道攒的进度是"重启就没"还是落了库。

PostgreSQL 腿的一致性测试与内存腿**同一套**（`tests/test_store.py` 参数化），另有
`tests/test_persistence.py` 用两个独立的 app 实例模拟重启。本地没库时这些用例 **skip**，不静默通过
（内存腿证明不了 SQL）；怎么真跑见 [`development.md`](development.md)。

> [!WARNING]
> 测试用的那个 DSN 指向的库会被 **`TRUNCATE`**。指一个专门用来跑测试的库，别指向真实进度库。

## 真 Forge（Gitea）：真仓库 / 真 push / 真 diff

默认 `--forge stub`：`provision` 只是本地 clone，而且 **diff 比对的是内容预置的解法 ref**（桩"假装
学员已经做对了"）。要真仓库、真 push，换一个后端——学员**直接推 `dev` 到内容仓**，不需要 fork、
也不需要开 PR（理由见设计 §8.3）：

```bash
export BAGU_GITEA_URL=http://<gitea-host>:3000
export BAGU_GITEA_TOKEN=<access token>        # 需要 write:repository
export BAGU_GITEA_UPSTREAM=<owner>/<repo>     # Track 内容所在的仓（= 学员 push 的目标）
python -m platform_api.serve --content-dir ../seckill-lab --forge gitea
```

| | `stub` | `gitea` |
|---|---|---|
| `provision` | 本地 clone 源仓，**不建 `dev`** | clone **上游仓**（`BAGU_GITEA_UPSTREAM`），建好 `dev`，配好 push 凭据与 git 身份 |
| `dev` 的起点 | —— | **本地已有 > 远端已有 > 从阶段分支新建**（学员那条 `dev` 是跨阶段累积的，不该被平台清掉——重建会让下一次 `git push` 被拒） |
| 进入下一阶段 | —— | 阶段分支是**一条链**（`stage-0N` 的父亲 = `stage-0(N-1)`，每级只多本阶段测试）⇒ 一次**只加不删**的 `git merge origin/<stage>`。没合时 `provision` 会给出 `merge_hint`，界面直接显示这条命令 |
| diff 比什么 | `origin/<stage>` ↔ `origin/<stage>-sol`（预置解法，`simulated=true`） | **你开始这一阶段时的那份 `dev`** ↔ **工作区**（含未提交改动，`simulated=false`）；与阶段基线逐字节相同的文件会被剔掉（那是基线自带的判据，不是你写的） |
| 判定面比什么 | —— | **内容作者的版本**（`merge-base(origin/<stage>, HEAD)`）：判据有没有被动，跟学员自己的历史无关——"开题前先改测试"躲不掉 |
| 报错 | — | Gitea 连不上/认证失败 → **503 `forge_unavailable`**；你还没 push `dev` → 工作区对账会给 `behind`/`missing`，前端提示你先 push；**没合阶段基线** → 阅卷报告直接说"判据不在工作区里"并给出该跑的命令 |

上游仓的写权限由 `deploy/` 里的一次性 `track-loader` 授予（`add_collaborator`，
`BAGU_LEARNER_WRITE` 默认开）；它同时给**内容分支**加保护（只允许内容所有者推、`dev` 禁止 force）。
平台自己**不会**替你 push：它只 clone 与只读 diff。

### push 凭据怎么给的（不违反"平台不跑 git 写操作"）

`provision` 把 token 写进**仓库外**的 `<workspace-root>/.git-credentials`（权限 `0600`），仓库里只留
一条指向它的 `credential.helper = store --file=…`。于是 `.git/config` 里**没有秘密**；
`git add -A` **永远不可能**把它提交进去（它根本不在仓库目录里）；学员在自己的工作区里一句
`git push -u origin dev` 就能推（命令里不含 token）。

> 工作区解析不到 git 身份（`user.email`）时，`provision` 会补一个仓库本地的
> `<learner>@localhost`——否则第一次 `git commit` 必然失败在 "Please tell me who you are"，
> 而那跟这道题毫无关系。
>
> 顺带：宿主开着 `commit.gpgsign` 时，`provision` 会在这份 checkout 的**仓库本地**配置里关掉它——
> 学员按 `merge_hint` 跑的 `git merge` 要造一个合并提交，无头环境里弹不出 pinentry 就是
> `fatal: failed to write commit object`（真踩过）。学员自己的全局设置不受影响。
>
> 眼下手里的 token 是**用户级**的（Gitea 的 `write:repository` 起）。收窄成 repo 级/更小 scope 的
> 令牌是后续该做的事：那个文件里躺着的东西能推你名下所有仓库。

### 代码写在别的机器上也可以

只要 push 到内容仓的 `dev`、后端读得到。后端手里那份 checkout 与远端的对账是显式的：

| | 什么时候 | 做什么 |
|---|---|---|
| 提交前（自动） | `POST …/submit` | `fetch` 你的 `dev`，**在无损时**快进后端那份工作区，然后才阅卷 |
| 「从远端同步」 | `POST …/sync` | 同一个动作的手动入口（想先自己看一眼时用） |
| 看 diff 时 | `GET …/diff` | **只读**对账：报出"远端比你新 N 个提交"，绝不动分支 |

"无损"= 工作树干净 + 本地不领先远端 + 当前就在 `dev` 上；任何一条不成立，平台**一个字都不动**，
而是如实报出 `dirty` / `local_ahead` / `remote_ahead`（平台不替学员合并、不 stash、不 reset）。
两边都有对方没有的提交时，界面会明说"**分叉**了"并给两条真出路（在你自己那台机器上合好再 push，
或用「重做这一题」重新 clone 一份远端 `dev`）——**不会**劝你去点一个按不动的同步按钮。
哪一版被阅了写进报告的 `provenance.host_env.revision`——"跑了但跑的是旧代码"是最难
自查的一种结果，所以它必须可审计。

## 多 Track

`--content-dir` 可以重复，`GET /tracks` 列出全部，前端在部署了多个时出现「选方向」切换条。
三条硬规矩：

| 规矩 | 为什么 |
|---|---|
| 报名必须指定 Track（`POST /enrollments {"learner":…,"track":…}`）；多 Track 下省略 ⇒ 400 `enroll_needs_track` | 猜错就把学员报进了**另一个** Track 的 DAG，而 DAG 决定谁 locked——报错了人自己看不出来 |
| 取卷用 `GET /tracks/{track_id}/tickets/{ticket_id}` | ticket id 只在 Track 内唯一：两个 Track 都可以有 `db-stage-01`。扁平的 `GET /tickets/{id}` 保留给单 Track 部署与旧客户端，一旦撞名回 409 `ticket_id_ambiguous`，**不猜** |
| 实例自带 `track` 戳（`TicketInstance.track`），每个 Track 一个 Forge + 一层工作区目录 | 解锁、看板、provision 都必须按 Track 走；否则 A 通过会把 B 的同名后继阶段一起解锁，两个 Track 的 clone 也会互相顶掉 |

## 设计支柱（已锁定，不可逆）

- **D1 —— 角色反转，结构性保证。** 平台里**没有 Executor 这个座位**：不是"禁用了写代码的工具"，
  而是**根本没有会写业务代码的组件**。因此 D1 不需要 guardrail 去守，它是状态机的形状。
- **D2 —— 荣誉制防剧透。** 只做轻摩擦：**一阶段一 ref**（未解锁阶段不在工作区里）、过关前不 surfaced
  参考解。唯一的例外是**判定面硬门**：Track 声明 `runtime.protected_paths`（canonical 测试 / 构建配置 /
  基础设施），学员改动其中任何一个，**即使阅卷是绿的也判 fail**（设计 §11.3）。
- **D3 —— 阅卷跑在学员自己的算力上。** 平台中心 **零** 阅卷算力、**零** 强制 LLM 成本。
- **D4 —— 引擎无关。** 依赖面只有 **Track 内容 + git + 本地工具链**；AI 只作为**可选挂件**接在
  `ReviewEngine` 缝上，**缺席时闭环照走**（接上再强的模型也越不过测试硬门）。

完整论证、边界与代价见 [`paper-platform-design.md`](paper-platform-design.md)。

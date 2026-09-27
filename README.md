# 发卷平台 · bagu-trainer

> 开源、可自托管的 **八股实战演练所** —— 供部署者（也是唯一用户）自习、刷题。
> 它是 [`seckill-lab`](https://github.com/OnlyblackTea/seckill-lab) 的延伸，接受社区贡献（提交新 Track / 扩展八股面）。
> **不含班级 / 排名 / 得分**，不是多租户 SaaS。
> **由人类学习者亲手写代码**：平台发工单、收提交、跑确定性验收门、解锁下一关；AI 评审是**可选挂件**。
> 流程：`开题 → 在 dev 分支开发 → push → 提交 → 通过 → 解锁下一关`。

**引擎无关**：平台的依赖面只有 **Track 内容 + git + 本地工具链**（设计支柱 D4）。
首个内容 Track = [`seckill-lab`](https://github.com/OnlyblackTea/seckill-lab)（数据库八股）。
权威设计文档：[`docs/paper-platform-design.md`](docs/paper-platform-design.md)（**v0.6**）。

> **v0.5 变更**：平台原先设计为 prar-agent 引擎驱动（AI 经理发卷 / 导师陪练 / AI 评审），该设计**已退休**。
> 退休的理由、代价与决策记录见设计文档 §6；完整决策（含尚未执行的代码删除清单）见
> [`docs/superpowers/specs/2026-09-10-learner-webui-and-prar-agent-retirement-design.md`](docs/superpowers/specs/2026-09-10-learner-webui-and-prar-agent-retirement-design.md)。
>
> **v0.6 变更（L3）**：真 Gitea 的形态改为**一个内容仓 + 学员直接 push `dev`**——fork 与 PR 一并废弃
> （`ForgeClient` 只剩 `workspace_path`/`provision`，`POST /instances/{id}/pr` 与
> `remote_branch_missing` 短码已删，`BAGU_FORK_RESET`/`BAGU_LEARNER_CAN_MERGE` → `BAGU_LEARNER_WRITE`）。
> 理由与代价见设计文档 §8.3。

---

## 本地跑起来

> **部署到别的机器上？先读 [`docs/deployment.md`](docs/deployment.md)。**
> 它会先讲清两件事实：平台**没有认证**（怎么对外暴露是部署者的事），以及**只有阅卷工具链与数据库
> 必须在后端那台机器上**——工作区（你写代码的那份 clone）可以在任何地方，只要你能把 `dev` push 到
> 内容仓、后端能从那个 Forge 读回来（提交时平台会自动把它的 checkout 快进到你的 push）。
> 想**一条命令起全**（Gitea + 后端 + 前端，启动时自动把 Track 载入 Gitea）：`cd deploy && cp .env.example .env && docker compose up -d`，
> 细节见 [`deploy/README.md`](deploy/README.md)。裸机起步则用 `bash scripts/quickstart.sh --content-dir ../seckill-lab`。

A′ 引擎无关切片已把「发卷 → 工作区 → 提交 → 本地阅卷 → 评审 → 确定性决策（解锁 / 返工）」做成
**纯 REST 后端 + 学习者 SPA**，全程**不依赖任何 AI / 引擎**。开两个终端：

```bash
# 1) 后端（默认用内置 demo Track；进度默认存 ./bagu.sqlite3，重启不失忆）
.venv/Scripts/python.exe -m platform_api.serve            # http://127.0.0.1:8000

# 2) 前端（Vite 开发服务器把 REST 前缀代理到后端）
cd frontend && npm install && npm run dev                 # http://127.0.0.1:5173
```

> 注意是 `python -m platform_api.serve`，**不是** `uvicorn platform_api.app:app`——
> `create_app` 是工厂，需要 `content_dir`，模块级没有 `app`。

也可以用真 Track 起后端：

```bash
.venv/Scripts/python.exe -m platform_api.serve --content-dir ../seckill-lab
```

**`--content-dir` 可以重复**，一次部署多个 Track：

```bash
.venv/Scripts/python.exe -m platform_api.serve \
    --content-dir ../seckill-lab --content-dir ../cache-bagu
```

`GET /tracks` 列出全部，前端在部署了多个时出现「选方向」切换条。多 Track 的三条硬规矩：

| 规矩 | 为什么 |
|---|---|
| 报名必须指定 Track（`POST /enrollments {"learner":…,"track":…}`）；多 Track 下省略 ⇒ 400 `enroll_needs_track` | 猜错就把学员报进了**另一个** Track 的 DAG，而 DAG 决定谁 locked——报错了人自己看不出来 |
| 取卷用 `GET /tracks/{track_id}/tickets/{ticket_id}` | ticket id 只在 Track 内唯一：两个 Track 都可以有 `db-stage-01`。扁平的 `GET /tickets/{id}` 保留给单 Track 部署与旧客户端，一旦撞名回 409 `ticket_id_ambiguous`，**不猜** |
| 实例自带 `track` 戳（`TicketInstance.track`），每个 Track 一个 Forge + 一层工作区目录 | 解锁、看板、provision 都必须按 Track 走；否则 A 通过会把 B 的同名后继阶段一起解锁，两个 Track 的 clone 也会互相顶掉 |

> 但 seckill-lab **尚未**按「一阶段一 ref」改造（它现在只有 `main` + `solutions` 两条分支，见设计 §8.1）。
> 所以对着它点「起工作区」会如实返回短码 `workspace_ref_missing`，并列出仓库里实际可用的 ref——
> 这是**预期内**行为，不是 bug（对接现状见设计附录 B）。

验证（全绿才算完成）：

```bash
.venv/Scripts/python.exe -m pytest -q        # 320 passed, 14 skipped
.venv/Scripts/python.exe -m ruff check .

cd frontend
npm run build                                 # tsc --noEmit + vite build
npm test                                      # vitest + RTL，115 passed
npx playwright test                           # 真浏览器 + 真后端，3 passed
```

### 界面（学习者 SPA）

三栏，按**用途**分而不是一路往下堆：左栏是导航（阶段看板 + 通关进度，sticky 常驻），中栏是
**读题与干活**（题面 → 工作区 → 提交 → 阅卷 → 决策，按流程往下排），右栏是**当前实例与历史记录**。
窄屏（≤1279px）收成两栏，≤899px 收成一栏。

题面按 **Markdown** 渲染（`marked`）+ **TeX**（KaTeX）：Track 作者本来就是按 Markdown 写
`brief.problem` 的（`**加粗**`、`` `内联代码` ``、`1) 2)` 列表、缩进代码块），当纯文本直出时记号
原样露出、换行被折叠，整段糊成一坨。渲染前还做一次**CJK 排版规范化**：中文题面常用单换行做有意
义的分行（`①②`），又常为了在 YAML 里好看而在句中硬换行——纯 Markdown 只能二选一（全接起来丢
结构，或全断开把句子劈成两半），所以按标点判断该接的接、该断的断，并把缩进的类代码块明确围栏
起来（否则列表项里的 SQL 会散成普通段落）。内容可能来自**社区贡献的 Track**，所以渲染结果一律
经 DOMPurify 消毒（`marked` 也已关掉原始 HTML）。

> 代价：这三个依赖（marked / katex / dompurify）让前端 JS 产物从 ~250 KB 涨到 ~575 KB
> （gzip 183 KB）。自托管单用户场景下不值一提，介意的话可以按需拆包。

想看改版后的样子（两个栈各截几张，含 1:1 局部）：

```bash
cd frontend
npm run ui:shots      # 整页；REAL_URL=... REAL_SUBMIT=1 可连真后端跑一次真阅卷
npm run ui:clips      # 1:1 局部（题面 / 判据 / 提示 / 历史），判断排版质量用这个
```

### 真跑一次阅卷（不是桩）

默认是 `--grading stub`：阅卷返回预置红绿，离线就能跑通全流程。要**真**跑 Track 的 canonical
测试，换一个后端即可——需要 JDK17 + Maven，以及 Track 声明的基础设施（D3 说的"学员自己的机器"，
all-in-one 时就是跑平台这台）：

```bash
# 方式一：命令行
python -m platform_api.serve --content-dir ../seckill-lab --grading docker

# 方式二：环境变量（等价）
BAGU_GRADING=docker python -m platform_api.serve --content-dir ../seckill-lab
```

两个后端的差别都是**可观察**的：

| | `stub` | `docker` |
|---|---|---|
| `submit` 做什么 | 立刻返回预置报告 | 按 `runtime.compose` **先把基础设施重置**（`down -v` + `up -d --wait`），再在工作区里跑 `mvn -B -Dgroups=<test_group> test` |
| 报告里有什么 | 两套 fixture 的预置结果 | surefire XML：逐条测试真实的 `pass` / `fail` / `error` + 真实报错文本 |
| `provenance.host_env` | `{"simulated": true}` | java / maven 版本、退出码、耗时、**这次有没有重置环境**、`simulated: false` |
| `files_changed` / `out_of_scope_edits` | 取自 ticket 声明 | 真 `git diff`（改动与越界都会被抓出来） |
| 跑不起来时（缺 maven 等） | — | **503 `grading_unavailable`**，绝不伪装成 `passed=False` |

> [!WARNING]
> `docker` 后端默认**每次阅卷前重置 Track 的 compose 基础设施**——`docker compose down -v`
> 会**删掉数据卷**（Track 自己的 compose 头注释也把 `down -v` 记为重置仪式）。这是必须的：
> 验收测试跑在长活数据库上，"库里残留一个索引"就能让半道题白送，而工作区里看不出任何痕迹。
> 想保留库内状态就 `--grading-reset never` / `BAGU_GRADING_RESET=never`——**但那就等于把那条
> 绕过放回来**，且报告里会如实记 `infra.reset: false`。

报告随时可从 `GET /submissions/{id}/report` 读——桩时代看不出来，接上真后端才发现这个端点是必需的。

### 提交是异步的（进度反馈）

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
- **运行记录落库**（见下）：阅卷是分钟级的事，进程中途重启不能只剩一句"我不知道"。
- **跑不起来 ≠ 没过**：异步之后异常处理器够不着后台线程，所以失败在登记处就地分类成短码
  （`grading_unavailable` / `workspace_ref_missing` / …），`report`、`progress`、历史都原样报出来。

> [!NOTE]
> `--grading stub` 下提交是**瞬时**的（桩不 sleep、不假装在干活），所以进度条一闪而过——
> 想看真的进度得换 `--grading docker`。实测（VM，真 MySQL + maven）：`submit` 80 ms 返回 202，
> 而整次阅卷 120 s，轮询依次看到 `reset_infra`(0.1 s) → `run_tests`(109 s) → `done`(120 s)，
> 报告带 `simulated:false` / `exit_code:1` / `infra.reset:true` 与逐条测试结果。

### 历史查询与比对

刷同一道题会提交很多次，而**"这次和上次差在哪"正是练习的价值**。平台此前只留得住"当前状态"，
所以每次尝试都连同**运行记录**一起落库，`GET /instances/{id}/history` 按 `seq` 给出全部尝试：

```jsonc
{ "attempts": [
  { "submission": {"seq": 1}, "grading": {"status": "done", "elapsed_ms": 103129,
      "steps": [{"step": "reset_infra", "elapsed_ms": 93559},
                {"step": "run_tests",   "elapsed_ms": 9239},
                {"step": "parse",       "elapsed_ms": 323}]},
    "report": { /* §7.4 的不可信自报告，逐条测试都在 */ },
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
- **重做这一题**（`POST /instances/{id}/reissue`，"当前实例"卡片里的按钮）：交卷之后才发现想接着改、
  或者这一关的结论不算数（桩给的、误判）时用它。状态机里 `retry` 只从 `failed` 出发、`passed` 是
  终点、`submitted` 也只能往前走——`→ stale` 是那些状态**唯一**的合法边，所以这条不是便利功能，
  是出路。旧实例留档为 `stale`（提交/报告/判定仍在历史里），新实例按该 ticket 当前版本重锚、回到
  初始态；**你自己登记的 workspace 目录会被继承**（代码就在那儿），而**已经解锁的下一关不会被收回**
  （解锁是单调的，你可能已经在下一关干活了）。

> [!NOTE]
> **重启后的三种口径**，都不编：
> `interrupted` = 本进程启动时清扫了自己的孤儿记录（确定被打断）；`stalled` = 有活跃记录但不在
> 本进程手里（重启过，或另一个平台实例在跑——不替它下结论）；`unknown` = 压根没有运行记录
> （这条提交来自本特性之前）。多实例共用一个库时给每个实例不同的 `--instance-id` /
> `BAGU_INSTANCE_ID`，清扫只动自己名下的。

### 进度存在哪（持久化）

默认 `--store memory`：进度在进程内存里，**重启即失忆**（enrollment / 实例状态 / 提交 / 报告 /
判定全没）。单机自托管想让它活过重启，换 PostgreSQL：

```bash
python -m platform_api.serve --content-dir ../seckill-lab \
    --store postgres --database-url 'postgresql://bagu:pw@127.0.0.1:5432/bagu'
# 等价：BAGU_STORE=postgres BAGU_DATABASE_URL=... python -m platform_api.serve ...
```

| | `memory` | `postgres` |
|---|---|---|
| 重启后 | 一切归零 | 实例状态 / 提交 / 报告 / 判定 / 已解锁的后继都还在 |
| 取号 | 进程内计数器 | `bagu_id_seq`，落库（所以重启后接着发新号，**不会覆盖**上一轮的实例） |
| 表 | — | `bagu_documents`（`kind, id` 主键 + `payload jsonb`，按 learner/ticket/instance 建索引）+ `bagu_id_seq` |
| 依赖 | — | `pip install 'bagu-trainer[postgres]'`（psycopg3） |
| `GET /meta` | `"store_backend": "memory"` | `"store_backend": "postgres"` |

`/meta` 会**如实**报当前用的是哪个——学员有权知道攒的进度是"重启就没"还是落了库。

PostgreSQL 腿的一致性测试与内存腿**同一套**（`tests/test_store.py` 参数化），另有 `tests/test_persistence.py`
用两个独立的 app 实例模拟重启。本地没库时这些用例 **skip**，不静默通过（内存腿证明不了 SQL）；要真跑：

```bash
docker run -d --name bagu-pg -e POSTGRES_PASSWORD=bagu -e POSTGRES_DB=bagu_test -p 5432:5432 pgvector/pgvector:pg16
BAGU_TEST_POSTGRES_DSN='postgresql://postgres:bagu@127.0.0.1:5432/bagu_test' \
  .venv/Scripts/python.exe -m pytest -q tests/test_store.py tests/test_persistence.py
```

> [!WARNING]
> 那个 DSN 的库会被 **`TRUNCATE`**。指一个专门用来跑测试的库，别指向真实进度库。

### 真 Forge（Gitea）：真仓库 / 真 push / 真 diff

默认 `--forge stub`：`provision` 只是本地 clone，而且 **diff 比对的是内容预置的解法 ref**（桩"假装学员
已经做对了"）。要真仓库、真 push，换一个后端——学员**直接推 `dev` 到内容仓**，不需要 fork、也不需要
开 PR（理由见设计 §8.3）：

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
| diff 比什么 | `origin/<stage>` ↔ `origin/<stage>-sol`（预置解法，`simulated=true`） | `origin/<stage>` ↔ **你的工作区**（**含未提交改动**，`simulated=false`） |
| 报错 | — | Gitea 连不上/认证失败 → **503 `forge_unavailable`**；你还没 push `dev` → 工作区对账会给 `behind`/`missing`，前端提示你先 push（不再是 409） |

> 上游仓的写权限由 `deploy/` 里的一次性 `track-loader` 授予（`add_collaborator`，`BAGU_LEARNER_WRITE` 默认开）。
> 平台自己**不会**替你 push：它只 clone 与只读 diff。

**push 凭据怎么给的**（不违反"平台不跑 git 写操作"）：`provision` 把 token 写进**仓库外**的
`<workspace-root>/.git-credentials`（权限 `0600`），仓库里只留一条指向它的
`credential.helper = store --file=…`。于是

* `.git/config` 里**没有秘密**；
* `git add -A` **永远不可能**把它提交进去（它根本不在仓库目录里）；
* 学员在自己的工作区里一句 `git push -u origin dev` 就能推（命令里不含 token），
  界面会把这条命令直接给出来；**平台自己仍然不跑任何 git 写操作**。

> 工作区解析不到 git 身份（`user.email`）时，`provision` 会补一个仓库本地的
> `<learner>@localhost`——否则第一次 `git commit` 必然失败在 "Please tell me who you are"，
> 而那跟这道题毫无关系。

**代码可以写在任何机器上**（只要 push 到内容仓的 `dev`、后端读得到）。后端手里那份 checkout 与远端的
对账是显式的：

| | 什么时候 | 做什么 |
|---|---|---|
| 提交前（自动） | `POST …/submit` | `fetch` 你的 `dev`，**在无损时**快进后端那份工作区，然后才阅卷 |
| 「从远端同步」 | `POST …/sync` | 同一个动作的手动入口（想先自己看一眼时用） |
| 看 diff 时 | `GET …/diff` | **只读**对账：报出"远端比你新 N 个提交"，绝不动分支 |

"无损"= 工作树干净 + 本地不领先远端 + 当前就在 `dev` 上；任何一条不成立，平台**一个字都不动**，
而是如实报出 `dirty` / `local_ahead` / `remote_ahead`（平台不替学员合并、不 stash、不 reset）。
哪一版被阅了写进报告的 `provenance.host_env.revision`——"跑了但跑的是旧代码"是最难自查的一种结果，
所以它必须可审计。

> 眼下手里的 token 是**用户级**的（Gitea 的 `write:repository` 起）。收窄成 repo 级/更小 scope 的
> 令牌是后续该做的事：那个文件里躺着的东西能推你名下所有仓库。

---

## 设计支柱（已锁定，不可逆）

- **D1 —— 角色反转，结构性保证。** 平台里**没有 Executor 这个座位**：不是"禁用了写代码的工具"，
  而是**根本没有会写业务代码的组件**。因此 D1 不需要 guardrail 去守，它是状态机的形状。
- **D2 —— 荣誉制防剧透。** 只做轻摩擦：**一阶段一 ref**（未解锁阶段不在工作区里）、过关前不 surfaced 参考解。
  不设硬性高墙；开源的答案本来也藏不住。
  > 唯一的例外是**判定面硬门**：Track 声明 `runtime.protected_paths`（canonical 测试 / 构建配置 / 基础设施），
  > 学员改动其中任何一个，**即使阅卷是绿的也判 fail**（设计 §11.3）。这不是防剧透，而是因为
  > §7.3 的"测试运行时注入"还没实现 —— 测试此刻就躺在学员的工作区里，不拦的话"把测试改成永远绿"是免费通道。
- **D3 —— 阅卷跑在学员自己的算力上。** 平台中心 **零** 阅卷算力、**零** 强制 LLM 成本。
- **D4 —— 引擎无关。** 依赖面只有 **Track 内容 + git + 本地工具链**；AI 只作为**可选挂件**接在
  `ReviewEngine` 缝上，**缺席时闭环照走**。

> **D4 具体是什么意思**：AI 评审 / 口试不在闭环的必经路径上。确定性验收门把 AI 输出只当作**一个输入**，
> 自己独立裁决、独立解锁；反过来，接上再强的模型也越不过测试硬门。

---

## 仓库结构

```
bagu-trainer/
├── LICENSE                 Apache-2.0（本仓）
├── NOTICE                  署名
├── Makefile                test / lint / serve / web / e2e
├── pyproject.toml
├── platform_service/       纯领域层（无 HTTP / 无 DB / 无引擎）
│   ├── models.py           Pydantic 领域模型（extra="forbid"）
│   ├── lifecycle.py        状态机合法性（LEGAL_TRANSITIONS）
│   ├── unlock.py           DAG 解锁
│   ├── acceptance.py       确定性验收门
│   ├── admission.py        准入门 validate_track（schema / D1 / digest / trust_level）
│   ├── content_loader.py   读 track.yaml 与 stages/*/ticket.yaml
│   ├── service.py          用例编排      store.py  存储缝（sqlite / postgres / memory）
│   ├── grading.py          GradingRunner 缝     forge.py   ForgeClient 缝
│   ├── ai_review.py        ReviewEngine 缝      review_diff.py  diff 预处理
│   └── stubs.py            三个"诚实占位"桩
├── platform_api/           ★ 组合根：FastAPI 纯 REST 控制面
│   ├── app.py  deps.py  schemas.py  serve.py
│   ├── store_sqlite.py  store_postgres.py     持久化后端（同一套测试参数化跑）
│   ├── jobs.py             后台阅卷（单线程 + 落库的运行记录）  grading_docker.py  真阅卷
│   ├── forge_gitea.py  gitea.py               真 Forge（上游 clone / push 凭据 / diff）
│   ├── track_loader.py     启动时把 Track 内容载入 Gitea（compose 栈的启动步骤）
│   ├── demo_repo.py        内置 demo Track（会建一个真本地 git 仓库）
│   └── routes/             track / progress / workspace（含 /sync）
├── deploy/                 ★ docker compose 栈：Gitea + 后端 + 前端，Track 自动载入
│   ├── docker-compose.yml  docker-compose.grading.yml（真阅卷叠加）  .env.example
│   ├── Dockerfile          Dockerfile.web  nginx.conf  entrypoint.sh
│   └── bootstrap/admin.sh  一次性建 Gitea 管理员（官方镜像不认 GITEA_ADMIN_*）
├── frontend/               ★ 学习者 SPA（React + Vite + TS，从零写，Apache-2.0）
├── content/schema/         track.schema.json + ticket.schema.json —— 贡献契约（设计 §4.2/§4.3）
├── paper_cli/              脚手架 docstring（CLI 已降级为可选未来项，见设计 §7.2）
├── scripts/quickstart.sh   一条命令起拓扑 A（建 venv → 装依赖 → 构建前端 → 起后端）
├── docs/                   deployment.md（部署指引）+ paper-platform-design.md + superpowers/
└── tests/                  pytest（后端全量）
```

> **引擎时代的残留已清除**（spec Section B，已执行）：引擎适配层、git 子模块与
> GPL 边界守卫、以及两个引擎相关的测试文件均已删除；`pyproject.toml` / `Makefile` /
> `NOTICE` 同步去掉了子模块与边界措辞。上面这张图**就是**当前的文件树。

---

## 状态

| 块 | 状态 |
|----|------|
| 内容模型与准入门（`content/schema` + `admission.validate_track`） | ✅ 已实现 |
| 平台领域层 + REST 控制面（`platform_service` + `platform_api`） | ✅ 已实现 |
| 学习者 SPA（`frontend/`） | ✅ 已实现（单测 + 真浏览器 e2e 绿） |
| 内置 demo Track（可独立跑通全流程） | ✅ 已实现 |
| **真实阅卷**（JDK17 + Maven 真跑 canonical 测试） | ✅ 已实现，用 `--grading docker` 开（§7.1）。**跑测试前会先重置 Track 的 compose 基础设施**（默认；见下方警告）。开发机若没有 JDK17/Maven，请在装了工具链的机器上跑 |
| **真实 Forge**（Gitea 真仓库 / push 凭据 / 真 diff） | ✅ 已实现，`--forge gitea` 开；学员直接推 `dev` 到内容仓（无 fork / 无 PR，理由见设计 §8.3）；凭据写进仓库外的 0600 文件，平台仍不代跑 git 写操作。已在真 Gitea 上端到端验过（真 push、真 submit、真 diff） |
| **AI 评审 / 口试** | ⬜ 桩；设计为**可选挂件**（`ReviewEngine` 缝），见设计 §11.2 / §12.2 |
| 学习者画像 / 脚手架生成器 | ⬜ 后续切片，见设计 §14 路线图 |

> **"模拟"徽标**：桩实现下，UI 会明确标注哪些环节是占位（`SimulatedBadge`）。
> 注意 `Workspace.simulated` 说的是 **Forge**（clone 是真的，桩下 diff 来源是预置解 ref），而阅卷报告自带自己的
> `provenance.host_env.simulated`——**两件事要分开看**，别用一个徽标盖住。

分期路线图见设计文档 §14，未决问题见附录 C。

---

## 贡献

- **内容（新 Track / 新八股方向）：** fork 一个 Track 仓，按 `content/schema/` 写 `track.yaml` 与
  `stages/*/ticket.yaml`，补齐 canonical 测试（**判别性**：骨架上红 / 参考解上绿）与八股地图，
  再向目录开 PR。完整契约见设计 §4；准入门 `admission.validate_track` 会硬卡
  （schema、`files_to_change` 是否存在、八股锚点是否可解析、runtime 入口是否被 digest 锁定、
  `trust_level` 背书、ticket 目录是否覆盖 `manifest.stages`）。
  > 脚手架生成器 `paper new-track` 与 CLI 包装的 `paper validate-track` **尚未实现**（设计 §4.5 / §4.6）。
- **平台改动：** 在本仓走正常 PR —— Apache-2.0。
- **改 Track 内容：** 去对应的 Track 仓提 PR（如 seckill-lab）；本仓不内置 Track。

## 许可

Apache-2.0 —— 见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。

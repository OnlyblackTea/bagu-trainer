# 发卷平台 · bagu-trainer

> 开源、可自托管的 **八股实战演练所** —— 供部署者（也是唯一用户）自习、刷题。
> 它是 [`seckill-lab`](https://github.com/OnlyblackTea/seckill-lab) 的延伸，接受社区贡献（提交新 Track / 扩展八股面）。
> **不含班级 / 排名 / 得分**，不是多租户 SaaS。
> **由人类学习者亲手写代码**：平台发工单、收 PR、跑确定性验收门、解锁下一关；AI 评审是**可选挂件**。
> 流程仿 GitHub：`fork → 开发 → PR → 通过 → 解锁下一关`。

**引擎无关**：平台的依赖面只有 **Track 内容 + git + 本地工具链**（设计支柱 D4）。
首个内容 Track = [`seckill-lab`](https://github.com/OnlyblackTea/seckill-lab)（数据库八股）。
权威设计文档：[`docs/paper-platform-design.md`](docs/paper-platform-design.md)（**v0.5**）。

> **v0.5 变更**：平台原先设计为 prar-agent 引擎驱动（AI 经理发卷 / 导师陪练 / AI 评审），该设计**已退休**。
> 退休的理由、代价与决策记录见设计文档 §6；完整决策（含尚未执行的代码删除清单）见
> [`docs/superpowers/specs/2026-09-10-learner-webui-and-prar-agent-retirement-design.md`](docs/superpowers/specs/2026-09-10-learner-webui-and-prar-agent-retirement-design.md)。

---

## 本地跑起来

A′ 引擎无关切片已把「发卷 → 工作区 → 提交 → 本地阅卷 → 评审 → 确定性决策（解锁 / 返工）」做成
**纯 REST 后端 + 学习者 SPA**，全程**不依赖任何 AI / 引擎**。开两个终端：

```bash
# 1) 后端（默认用内置 demo Track）
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
.venv/Scripts/python.exe -m pytest -q        # 250 passed, 12 skipped
.venv/Scripts/python.exe -m ruff check .

cd frontend
npm run build                                 # tsc --noEmit + vite build
npm test                                      # vitest + RTL，46 passed
npx playwright test                           # 真浏览器 + 真后端，3 passed
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

### 真 Forge（Gitea）

默认 `--forge stub`：`provision` 就是本地 clone、`create_pr` 返回一个占位 URL。要真 fork、真开 PR：

```bash
export BAGU_GITEA_URL=http://<gitea-host>:3000
export BAGU_GITEA_TOKEN=<access token>        # 需要 write:repository
export BAGU_GITEA_UPSTREAM=<owner>/<repo>     # Track 内容在上游的仓
export BAGU_GITEA_OWNER=<你的账号>             # fork 落到谁名下
python -m platform_api.serve --content-dir ../seckill-lab --forge gitea
```

| | `stub` | `gitea` |
|---|---|---|
| `provision` | 本地 clone 源仓 | 先经 API 确保 **fork** 存在，再 clone 你的 fork，并建好 `dev` 分支 |
| 基线 | 不记 | 把阶段分支的 SHA 记进 `bagu.baseRef`（阅卷物证据此比出**已提交**的改动） |
| `create_pr` | 占位 URL | 真开 PR（`<owner>:dev` → 阶段分支），返回可点的 `html_url` |
| 报错 | — | Gitea 连不上/认证失败 → **503 `forge_unavailable`**；你还没 push `dev` → **409 `remote_branch_missing`**（带上分支名） |

> token 只用于那一次 clone，随后 remote 会被改回干净 URL——不会留在 `.git/config` 里。
> 私有 fork 若要长期免密 push，仍需在**你的**机器上配好 git 凭据（平台不代跑 git 写操作）。

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
│   ├── service.py          用例编排      store.py  存储（当前 InMemory）
│   ├── grading.py          GradingRunner 缝     forge.py   ForgeClient 缝
│   ├── ai_review.py        ReviewEngine 缝      review_diff.py  diff 预处理
│   └── stubs.py            三个"诚实占位"桩
├── platform_api/           ★ 组合根：FastAPI 纯 REST 控制面
│   ├── app.py  deps.py  schemas.py  serve.py
│   ├── demo_repo.py        内置 demo Track（会建一个真本地 git 仓库）
│   └── routes/             track / progress / workspace
├── frontend/               ★ 学习者 SPA（React + Vite + TS，从零写，Apache-2.0）
├── content/schema/         track.schema.json + ticket.schema.json —— 贡献契约（设计 §4.2/§4.3）
├── paper_cli/              脚手架 docstring（CLI 已降级为可选未来项，见设计 §7.2）
├── docs/                   paper-platform-design.md（权威设计稿，v0.5）+ superpowers/（spec 与计划）
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
| **真实 Forge**（Gitea fork / PR） | ⬜ 桩（真本地 clone + 真本地 git diff，PR 为占位） |
| **AI 评审 / 口试** | ⬜ 桩；设计为**可选挂件**（`ReviewEngine` 缝），见设计 §11.2 / §12.2 |
| PostgreSQL 存储 / 学习者画像 / 脚手架生成器 | ⬜ 后续切片，见设计 §14 路线图 |

> **"模拟"徽标**：桩实现下，UI 会明确标注哪些环节是占位（`SimulatedBadge`）。
> 注意 `Workspace.simulated` 说的是 **Forge**（clone/PR 仍是占位），而阅卷报告自带自己的
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

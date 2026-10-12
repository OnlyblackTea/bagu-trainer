# 开发与验证

面向"改这个仓的人"。部署给别人用见 [`deployment.md`](deployment.md)；
"内部机制长什么样"见 [`internals.md`](internals.md)。

## 起本地开发栈

```bash
# 1) 后端（进度默认存 ./bagu.sqlite3，重启不失忆）—— 必须给它一个内容 Track
.venv/Scripts/python.exe -m platform_api.serve --content-dir ../seckill-lab
#                                                        http://127.0.0.1:8000

# 2) 前端（Vite 开发服务器把 REST 前缀代理到后端）
cd frontend && npm install && npm run dev                 # http://127.0.0.1:5173
```

> 注意是 `python -m platform_api.serve`，**不是** `uvicorn platform_api.app:app`——
> `create_app` 是工厂，需要 `content_dir`，模块级没有 `app`。
>
> `--content-dir` 指向一个内容仓（Track）：没有内容的平台没有意义，所以它几乎是必填。

`Makefile` 里有同一批命令的快捷方式：`make serve` / `make web` / `make test` / `make lint` / `make e2e`。

## 验证（全绿才算完成）

```bash
.venv/Scripts/python.exe -m pytest -q        # 355 passed, 14 skipped
.venv/Scripts/python.exe -m ruff check .

cd frontend
npm run build                                 # tsc --noEmit + vite build
npm test                                      # vitest + RTL，141 passed
npx playwright test                           # 真浏览器 + 真后端（e2e/ 里那份 spec）
```

**UI 改动必须在浏览器里看过**（spec §C.9 的硬规矩）。仓库里有两支截图脚本，改版时先自己看一眼：

```bash
cd frontend
npm run ui:shots      # 整页；REAL_URL=... REAL_SUBMIT=1 可连真后端跑一次真阅卷
npm run ui:clips      # 1:1 局部（题面 / 判据 / 提示 / 历史），判断排版质量用这个
```

## 真阅卷（不是桩）

默认是 `--grading stub`：阅卷返回预置红绿，离线就能跑通全流程。要**真**跑 Track 的 canonical
测试，加一个开关即可——需要 JDK17 + Maven，以及 Track 声明的基础设施（D3 说的"学员自己的机器"，
all-in-one 时就是跑平台这台）：

```bash
.venv/Scripts/python.exe -m platform_api.serve \
    --content-dir ../seckill-lab --grading docker
```

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

## 跑 Postgres 腿的测试

一致性测试与内存腿**同一套**（`tests/test_store.py` 参数化），另有用两个独立 app 实例模拟重启的
`tests/test_persistence.py`。本地没库时这些用例 **skip**，不静默通过（内存腿证明不了 SQL）：

```bash
docker run -d --name bagu-pg -e POSTGRES_PASSWORD=bagu -e POSTGRES_DB=bagu_test \
  -p 5432:5432 pgvector/pgvector:pg16
BAGU_TEST_POSTGRES_DSN='postgresql://postgres:bagu@127.0.0.1:5432/bagu_test' \
  .venv/Scripts/python.exe -m pytest -q tests/test_store.py tests/test_persistence.py
```

> [!WARNING]
> 那个 DSN 的库会被 **`TRUNCATE`**。指一个专门用来跑测试的库，别指向真实进度库。

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
├── docs/                   deployment.md + internals.md + paper-platform-design.md + superpowers/
└── tests/                  pytest（后端全量）
```

## 状态

| 块 | 状态 |
|----|------|
| 内容模型与准入门（`content/schema` + `admission.validate_track`） | ✅ 已实现 |
| 平台领域层 + REST 控制面（`platform_service` + `platform_api`） | ✅ 已实现 |
| 学习者 SPA（`frontend/`） | ✅ 已实现（单测 + 真浏览器 e2e 绿） |
| 内置 demo Track（可独立跑通全流程） | ✅ 已实现 |
| **真实阅卷**（JDK17 + Maven 真跑 canonical 测试） | ✅ 已实现，用 `--grading docker` 开（§7.1）。**跑测试前会先重置 Track 的 compose 基础设施**（默认） |
| **真实 Forge**（Gitea 真仓库 / push 凭据 / 真 diff） | ✅ 已实现，`--forge gitea` 开；学员直接推 `dev` 到内容仓（无 fork / 无 PR，理由见设计 §8.3）。已在真 Gitea 上端到端验过（真 push、真 submit、真 diff） |
| **AI 评审 / 口试** | ⬜ 桩；设计为**可选挂件**（`ReviewEngine` 缝），见设计 §11.2 / §12.2 |
| 学习者画像 / 脚手架生成器 | ⬜ 后续切片，见设计 §14 路线图 |

> **"模拟"徽标**：桩实现下，UI 会明确标注哪些环节是占位（`SimulatedBadge`）。
> 注意 `Workspace.simulated` 说的是 **Forge**（clone 是真的，桩下 diff 来源是预置解 ref），而阅卷报告
> 自带自己的 `provenance.host_env.simulated`——**两件事要分开看**，别用一个徽标盖住。

分期路线图见设计文档 §14，未决问题见附录 C。

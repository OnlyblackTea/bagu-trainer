# 学习者 Web UI + prar-agent 退休 + A′ 引擎无关切片 — 设计 spec

> 日期：2026-09-10 ｜ 状态：设计已批准，待写实现计划（writing-plans）
> 范围：把"基于 prar-agent 引擎的发卷平台"收缩为"内容(Track) + git + 本地阅卷 + 确定性门 + 自有 UI"的自习演练所，并据此退休 prar-agent。
> 读者：实现者（含未来 session 的我自己）。本文是**设计契约**，不是实现步骤；步骤由 writing-plans 产出。

---

## 0. 背景：本次为什么转向

平台定位已在上一轮收缩为：**供部署者（也是唯一用户）自习、刷题的交互式演练所**，作为 seckill-lab 的延伸，接受社区贡献（新 Track / 扩展八股面）。不需要班级、排名、得分。

在此基础上，本轮 brainstorm 澄清出两个对原设计的**根本性修正**：

1. **阶段计划是 Track 固定内容，不是 AI 临时生成。** 学员把 track-repo `fork`/`pull` 到本地，在 `dev` 分支开发；各阶段（Stage00/01/…）的代码保存在**各自的分支**里。学员完成 Stage00 后，通过 PR 合并或 cherry-pick 从 Stage01 分支合入代码，再开发 Stage01。"发卷"= 渲染作者已写好的 `ticket.yaml` + stage 分支，**不经 LLM**。

2. **prar-agent 与修正后的模型硬冲突，应整体退休。** 经核对 `prar-agent/docs/CONTRACTS.md`（冻结 tag v0.2.0）确认两条不可调和的事实：
   - **没有 plan 导入端点。** 会话出生即 `planning`，只有 `WS /plan generate`（AI 生成）能推进到 `plan_review`（CONTRACTS §2.1）。连 AI 评审（§5，需 `action_review`）都要求上游有一份 AI 生成的 plan。Track 固定的计划**喂不进去**。
   - **§6.3 设想的"导师陪练 sub-loop"在 v0.2.0 不存在。** §4.3 让 human-actor 会话**结构性地不跑任何工具循环**（WS `/act` 直接以 `actor_not_executor` 拒绝，先于 phase/adapter/dispatcher）。"导师用只读工具陪练"无对应能力。

   修正后的模型（Track 固定计划 + 人类执行 + 可选轻量 AI）用不到 prar-agent 的任何独有机器（plan 生成、executor 状态机、sandbox、git checkpoint、pgvector 记忆）。继续背着它，只换来 GPL 边界的法务复杂度（§6.7 衍生作品风险）与一层适配维护成本，零收益。

**转向决策（已批准）**：退休 prar-agent。平台 = 内容 + git + 本地阅卷 + 确定性门 + 自有 UI，整体干净 Apache-2.0。AI 评审/带教降级为**未来可选**的轻量 harness（如 "DeepSeek harness"），届时插入既有 `ReviewEngine` Protocol 缝，不重建架构。

**关键红利：转向是外科手术式的。** 因为 `platform_service/ai_review.py` 当初就用结构化 `ReviewEngine` Protocol 注入引擎，**既不 import orchestrator 也不 import 引擎**，所以退休 prar-agent 时它的代码**一字不改**——只是后端实现从 `PrarBridge` 换成桩（现在）/轻量 harness（将来）。纯控制面 11 个任务（store/models/lifecycle/unlock/acceptance/admission/service/content_loader/review_diff/ai_review）全部原样存活。

---

## 1. 已批准决策摘要

| 维度 | 决策 |
|---|---|
| 引擎角色 | **退休 prar-agent**；AI 评审/带教为未来可选轻量 harness，经 `ReviewEngine` 缝插入 |
| 计划来源 | **Track 固定**（作者写好的 `ticket.yaml` + stage 分支），非 AI 生成 |
| git 进度模型 | **学员驱动本地 git**：fork/clone → `dev` 分支开发 → 阶段间 cherry-pick/PR 合并；平台只 **gate + 指示**，不代学员跑 git 写操作 |
| 后端命名 | `platform_api/`（新顶层包，FastAPI 组合根，区别于纯域层 `platform_service/`） |
| 前端命名 | `frontend/`（React + Vite + TS SPA；prar-agent submodule 退休后无命名冲突） |
| 传输 | **纯 REST**，无 SSE/WS（无 AI 流可推；传输问题随引擎退休而消解） |
| 占位策略 | **诚实占位 + 双分支 fixture**：阅卷/评审/Forge 三个桩，各备 pass 与 fail 两套 fixture，跑通 decide 两条分支；UI 明标"模拟" |
| A′ 三件占位 | provision = 本地 clone track-repo fixture；diff = 真本地 git（`review_diff.generate_diff`）；PR = 占位 URL |
| 执行排序 | **spec + 设计修订 → A′ 切片 → 退休删除（独立授权）** |

> **授权边界（必须遵守）**：本 spec 只**规划**退休（Section B 清单），**不执行删除**。删 submodule / `orchestrator/` / `mentor.py` / 契约测试是**单独授权**的步骤，在 A′ 切片落地、用户明确点头后才做。Section A 的设计文档修订属于第一阶段（与 spec 同期），但仍走 writing-plans → 执行流程，不在 brainstorm 阶段直接改。

---

## Section A — 设计文档修订清单（`docs/paper-platform-design.md`）

目标：把设计文档从"prar-agent 引擎驱动"改写为"Track 内容 + git + 本地阅卷 + 自有 UI + 未来可选轻量 AI"。修订后文档版本从 **v0.4 → v0.5**（代码 docstring 里的 "design v0.4 §x" 引用在退休阶段统一更新，见 Section B）。

处置图例：**DELETE** 删除 ｜ **RETIRE** 整节退休（保留一句"已退休，见 spec"指针）｜ **REWRITE** 改写 ｜ **REFINE** 保留并微调。

| 章节 | 现状 | 处置 | 修订要点 |
|---|---|---|---|
| §1.2 / §1.3 | "AI 经理生成工单" + 六阶段映射 | **REWRITE** | 工单/阶段是 Track 固定内容；删除"AI 生成 plan"叙事；六阶段映射改为平台自有状态机（`InstanceState`）说明 |
| §3 引擎层 | prar-agent 作为引擎 | **RETIRE** | 改为"无引擎；阅卷=本地工具链，AI 评审=未来可选轻量 harness" |
| §3.1 | `plan_engine.py → 发卷` | **DELETE** | 发卷 = `content_loader` 渲染 Track 内容，无 plan 引擎 |
| §6 整章 | submodule + 适配层 + 契约冻结 + GPL 边界 | **RETIRE** | 整章退休；保留一段"为何曾选 prar-agent、为何退休"的决策记录（指向本 spec §0） |
| §6.2 | PLANNING：AI 生成工单 plan | **REWRITE** | PLANNING 不存在 AI 生成；发卷即渲染 `ticket.yaml` + 暴露 stage 分支 |
| §6.3 | ACTING：导师 sub-loop（只读工具陪练） | **RETIRE** | v0.2.0 无此能力（§4.3）；ACTING = 学员本地开发，平台 gate+指示；导师陪练降为未来可选 harness |
| §6.4 | 导师 guardrail（D1 三层保险） | **REWRITE** | 工具白名单/角色不调用两层随引擎退休消失；**D1 概念存活**："人类亲手写被评分的代码"，由"平台根本不跑 AI 工具循环"天然保证 |
| §6.5 | AI 评审（评审者反转，锚定 PR diff） | **REFINE** | 概念存活为未来可选轻量 harness；锚定/维度/建议性形状由 `ReviewEngine` Protocol + `models.Review` 承载，与具体后端解耦 |
| §6.6 / §6.6.1 / §6.7 | submodule/契约/GPL 边界 | **RETIRE** | GPL 边界**消解**：无 GPL 代码进入仓库，平台干净 Apache-2.0，§6.7 衍生作品法务风险归零 |
| §7 / §7.2 | paper-cli / `paper grade` | **REFINE** | 形态从 CLI 改为 `platform_api/`(REST) + `frontend/`(SPA)；CLI 降级为可选未来项（原 Q2 已决，见附录 C） |
| §8 | fork + 防剧透 D2（未来阶段独立 ref，fork 只检出已解锁） | **REFINE** | **与用户的 git 模型一致**，保留并细化：stage 分支 = 独立 ref；平台 gate 只在阶段通过后指示下一分支；防剧透 = 未解锁分支不暴露给学员 UI |
| 附录 A 亮点 #1/#3/#6/#7 | prar-agent 为中心 | **REWRITE** | 改为内容驱动 / 本地阅卷 / 确定性门 / 自有 UI 四亮点 |
| 附录 B | prar-agent 对接 | **RETIRE** | 整附录退休 |
| 附录 C Q1（取码/交互轴） | submodule + 进程外 API | **标记 ✅已决** | 结论：退休 prar-agent，两轴均不再适用 |
| 附录 C Q2（CLI vs Web） | 未决 | **标记 ✅已决** | 结论：Web（`platform_api/` + `frontend/`），CLI 可选未来项 |
| D4 支柱 | （prar-agent 相关支柱） | **REWRITE** | 重述为"内容 + git + 本地阅卷 + 自有 UI"支柱 |

**修订验收**：全文 grep `prar-agent` / `prar_agent` / `submodule` / `GPL` / `契约冻结` 仅出现在"退休决策记录"段落（带过去时与 spec 指针）；无残留的现在时引擎依赖描述。

**执行结果（2026-09-10，已完成）**：

- 设计稿已由 **v0.4 修订为 v0.5**，逐章处置与本表一致。§6 整章退休为一段"决策记录"（6.1 曾经的选择 / 6.2 为什么放弃 / 6.3 代价诚实记账 / 6.4 边界纪律的去向），并按本表补了 §0 **D4**、§1.3 平台自有 `InstanceState`、§8.1「一阶段一 ref」契约。
- `README.md` 同步重写：去引擎叙事、换新结构图与状态表、补真 Track 跑法的坑（`workspace_ref_missing` 属预期）、并把"引擎时代残留文件尚未删除"写明。
- **验收审计**：全文 grep `prar|submodule|GPL|契约冻结|paper-cli|六阶段|engine_version|checkpoint|LTM|pgvector` 命中 37 处，逐一核对后**全部**落在三类允许位置：① 变更记录里标注为"当时决策"的 v0.2–v0.4 条目；② §1.1 / §1.2 / §3 / §6 / §10 / §11.4 / 附录 A / 附录 C 的**过去时 + §6 指针**段落；③ `engine_version` **字段名**本身（已标注为遗留命名债务，§3.1）。无残留的现在时引擎依赖描述。顺带修掉两处失效指针：§1.1 的 `§6.6 / §6.4`、§4.3 五个硬问题里的"引擎更新后旧 session 用哪个 engine"。
- **`NOTICE` / `Makefile` / `paper_cli/__init__.py` 刻意未改**：它们描述的是**仓内当前真实状态**（子模块仍在、`contracts/test_boundary.py` 仍在守），抢在 Section B 之前改会造成不实陈述——三者已补进 Section B.3 清单与 B.4 验收。
- 回归：`pytest -q` **220 passed**、`ruff check .` 全绿（本次为纯文档改动，代码未动）。

---

## Section B — prar-agent 退休执行清单【独立授权，勿现在执行】

> ⚠️ 本节是**待授权**的删除计划，不是 A′ 切片的一部分。执行前需用户明确点头。执行时机：A′ 切片落地并验证通过后。

### B.1 删除文件 / 目录

| 路径 | 说明 |
|---|---|
| `orchestrator/prar_bridge.py` | 唯一知道引擎线协议的文件 |
| `orchestrator/session_pool.py` | mentor 会话池 + workspace 自证 |
| `orchestrator/__init__.py` | 包初始化 |
| `platform_service/mentor.py` | 包装引擎会话生命周期（stream_plan_generation 等） |
| `contracts/test_prar_contract.py` | 引擎 API 契约测试 |
| `contracts/test_boundary.py` | GPL 边界守卫（边界消解后无意义） |
| `contracts/__init__.py` | 若 `contracts/` 清空则整目录删除 |
| `tests/test_session_pool.py` | session_pool 单测 |
| `tests/test_mentor.py` | mentor 单测 |
| `prar-agent/`（submodule） | 引擎源码 |

### B.2 git submodule 解除

```cmd
:: Windows / cmd.exe（在仓库根执行）
git submodule deinit -f prar-agent
git rm -f prar-agent
rd /s /q .git\modules\prar-agent
```
- 删除 `.gitmodules` 中 `[submodule "prar-agent"]` 段（若它是唯一条目，删整个文件）。
- 删除 `.git/config` 中对应段（`deinit` 通常已处理）。

### B.3 改造（非删除）

| 文件 | 改动 |
|---|---|
| `pyproject.toml` | `[tool.setuptools] packages`：移除 `"orchestrator"`、`"contracts"`（若清空），新增 `"platform_api"`；`dependencies` 移除 `websockets>=12`（无 WS）；**保留** `httpx`（未来 harness/Gitea 用）；`dev` 保留 `respx`（未来 harness/Gitea 的 httpx mock）；`[tool.pytest.ini_options] testpaths` 移除 `"contracts"` |
| `platform_service/ai_review.py` | **代码不改**；仅 docstring 去掉 `CONTRACTS §5` / `orchestrator.prar_bridge.PrarBridge` 字样，改述为"后端经 `ReviewEngine` 缝注入（桩 / 未来轻量 harness）"。`ReviewEngine` Protocol 的 `session_id` 形参为引擎味命名，A′ 阶段保留为可接受债务，待真实 harness 落地再议改名 |
| `platform_service/review_diff.py` | **代码不改**；docstring 去掉 `CONTRACTS §5.5/§5.6/§5.8a` / "契约三 铁律" 字样。`ENGINE_DIFF_CHAR_LIMIT` 常量名带 "ENGINE"，退休后可改名为 `REVIEW_DIFF_CHAR_LIMIT`（纯改名，可选） |
| 存活代码 docstring | 全局把 "design v0.4 §x" 引用更新为 "v0.5 §x"（若章节号变动） |
| `NOTICE` | 删掉「1. prar-agent —— 引擎层」整段（含"GPL 边界（承重 —— 切勿打破）"宣告）。⚠ **时序**：只有 B.1 的删除**真正执行之后**，该段才成为不实陈述——执行前它描述的是仓内**真实**状态（子模块仍在、`contracts/test_boundary.py` 仍在守），所以**不要抢在 B.1 之前改它**。（Section A 修订设计稿时已按此原则跳过 NOTICE。） |
| `Makefile` | 删 `SUBMODULE` / `PINNED_TAG` / `bootstrap` / `clone-hint` / `submodule-status` / `bump-submodule` / `test-boundary` / `test-contract` 及所有子模块措辞；`lint` / `fmt` 的路径列表去掉 `orchestrator`、`contracts`，补上 `platform_api`（`frontend/` 走 npm，不进 ruff） |
| `paper_cli/__init__.py` | docstring 去掉 `orchestrator.prar_bridge` / "GPL boundary" / `paper mentor` 措辞，改述为"形态已改为 REST 控制面 + 学习者 SPA（设计 §7.2）；CLI 是可选未来项" |

### B.4 退休验收

- `git submodule status` 无输出；`.gitmodules` 无 prar-agent 段。
- `grep -rn "prar" .`（排除本 spec 与 `docs/` 决策记录段）无命中。
- `grep -rn "prar\|GPL\|submodule" NOTICE Makefile paper_cli/` 无命中；`README.md` 只允许开头那段 v0.5 变更说明（决策记录）。

**执行结果（2026-09-10，已执行，获独立授权）**：

- **B.1 删除**：`orchestrator/`（3 文件）、`platform_service/mentor.py`、`contracts/`（3 文件）、`tests/test_session_pool.py`、`tests/test_mentor.py` —— 共 **9 个跟踪文件** `git rm`。
- **B.2 子模块**：`git submodule deinit -f prar-agent` → `git rm -f prar-agent` → 删 `.git/modules/prar-agent` → `.gitmodules`（唯一条目，整文件删）→ `.git/config` 已无 `submodule.*` 段。**验收**：`git submodule status` **空输出**。
- **B.3 改造**：`pyproject.toml`（packages 去 `orchestrator`/`contracts`、deps 去 `websockets`、`testpaths` 去 `contracts`、description 去引擎叙事、补 `paper_cli` 到 packages）；`Makefile` 重写（去 `bootstrap`/`submodule-status`/`bump-submodule`/`test-boundary`/`test-contract`，补 `serve`/`web`/`e2e`）；`NOTICE` 删引擎段并把内容仓改为第 1 节；`paper_cli/__init__.py` 改述为"REST + SPA 已定，CLI 可选"；`platform_service/__init__.py`、`ai_review.py`、`review_diff.py`、`models.py`、`admission.py`、`platform_api/__init__.py` 的 docstring 全面清理（含 20 个文件的 `design v0.4 §x` → `v0.5 §x`）。
- **清单外一并处理（同属"失效引用"一类，非新增范围）**：
  1. 8 处存活代码的 `(GPL boundary intact)` 尾注 → `(D4)`（边界已无对象）；
  2. `ENGINE_DIFF_CHAR_LIMIT` → `REVIEW_DIFF_CHAR_LIMIT`（B.3 标为可选，此处做了）；
  3. 指向**已删除文档**的 `CONTRACTS §5.x` / 裸 `§5.x` / `契约三` 引用（`models.py`、`review_diff.py`、`stubs.py` + 3 个测试文件）→ 改指设计稿 §11.2 或直接改述；
  4. `.gitignore` 的子模块段；本机一个陈旧的 `paper_platform.egg-info/`（已在 gitignore 内、含退休架构的旧 PKG-INFO）一并清除；
  5. README 里"残留尚未删除"的提示改为"已清除"，测试计数 220 → 173。
- **B.4 验收**：`git submodule status` 空；对 `docs/` 与 README 决策说明之外的全部文件 grep `prar` / `GPL` / `submodule` / `orchestrator` / `CONTRACTS` **零命中**；`pytest -q` **173 passed**（220 − 47 个随被删文件移除的用例）；`ruff check .` 全绿；`import platform_api, platform_service` 成功、`import orchestrator` 失败（`ModuleNotFoundError`）。
- 回归：`frontend/` **零改动**（本次不涉及前端）。
- `.venv\Scripts\python.exe -m pytest` 全绿（已删 `tests/test_session_pool.py`、`tests/test_mentor.py`、`contracts/`）。
- `.venv\Scripts\python.exe -m ruff check .` 全绿。
- `python -c "import platform_api, platform_service"` 成功；`import orchestrator` 失败（已删）。

---

## Section C — A′ 引擎无关纵向切片设计

### C.1 目标与非目标

**目标**：用一条**端到端可跑、可点、可测**的纵切片，证明退休引擎后的平台自洽——从发卷到解锁，全程无 prar-agent。

**在范围内**：
- Track 固定发卷（渲染 `ticket.yaml` / `track.yaml`）。
- 学员驱动本地 git 进度（平台 gate + 指示）：provision（本地 clone fixture）→ dev 开发 → diff（真本地 git）→ 阶段推进。
- 本地阅卷（桩 + pass/fail fixture）。
- AI 评审（桩 + approve/request_changes fixture，经既有 `ReviewEngine` 缝）。
- 确定性门 decide + DAG unlock（**真实**，复用存活代码）。
- 自有 UI：`platform_api/`（REST）+ `frontend/`（SPA）。

**非目标（未来切片，见 §2）**：真实 Docker/Maven 阅卷、真实 Gitea fork/PR、真实轻量 AI harness、PostgreSQL Store、LearnerProfile 个性化、多 Track 管理后台。

**A′ 不做的事**：不删任何 prar-agent 文件（那是 Section B，独立授权）；不改 `ai_review.py`/`review_diff.py` 代码（仅未来退休阶段动 docstring）；不引入 SSE/WS。

### C.2 架构总览

```
┌─────────────┐   REST/JSON    ┌──────────────────┐
│  frontend/  │ ─────────────▶ │   platform_api/  │  FastAPI 组合根（新）
│ React+Vite  │ ◀───────────── │  app/deps/routes │
│    +TS      │                └────────┬─────────┘
└─────────────┘                         │ 注入（Protocol 缝）
                          ┌─────────────┼─────────────────────────┐
                          ▼             ▼                          ▼
                 ┌──────────────┐ ┌───────────────┐ ┌──────────────────────┐
                 │ GradingRunner│ │ ReviewEngine  │ │     ForgeClient      │
                 │   (Stub)     │ │   (Stub)      │ │      (Stub)          │
                 └──────┬───────┘ └───────┬───────┘ └──────────┬───────────┘
                        │                 │                     │
                        ▼                 ▼                     ▼
              pass/fail fixture   ai_review.generate_review  本地 clone fixture
                                  （复用存活代码，真 Protocol）  + 真 git diff + 占位 PR
                          ┌─────────────────────────────────────────────┐
                          ▼   platform_service/（纯域层，全部存活）        ▼
                 PlatformService(InMemoryStore, track, tickets)
                 enroll→start→submit→review→decide→(pass⇒unlock | fail⇒retry)
                 content_loader（发卷）· review_diff（真本地 git diff）· unlock（DAG）
```

**分层纪律**：
- `platform_service/` = 纯域层（无 HTTP/DB/引擎）。Protocol 定义（缝）放这里；桩实现也放这里（纯 / 仅本地 git&clone 的 subprocess I/O，与既有 `review_diff.generate_diff` 同类）。
- `platform_api/` = 组合根 + REST 传输。**唯一**知道"现在用桩、将来用真后端"的地方（依赖注入）。路由薄，业务全在 `PlatformService`。
- `frontend/` = SPA，只调 `platform_api/` REST，不碰 git/文件系统。

### C.3 后端 `platform_api/`

新顶层包。建议结构（writing-plans 可细化）：

```
platform_api/
  __init__.py
  app.py          # create_app(...) -> FastAPI；组合根入口
  deps.py         # 依赖装配：选 Stub vs 真后端；持有 PlatformService 单例
  schemas.py      # 请求/响应 DTO（pydantic，extra="forbid"），与域模型解耦
  routes/
    __init__.py
    track.py      # 发卷：GET /tracks, GET /tracks/{id}, GET /tickets/{id}
    progress.py   # 生命周期：enroll/start/submit/review/decide/retry
    workspace.py  # git：provision/diff/pr（gate + 指示）
```

**REST 端点（A′ 全集，纯 REST）**：

| 方法 | 路径 | 作用 | 后端 |
|---|---|---|---|
| GET | `/tracks` | 列 Track（发卷首页） | `content_loader` |
| GET | `/tracks/{track_id}` | Track 清单：stages + tickets + 解锁 DAG | `content_loader` |
| GET | `/tickets/{ticket_id}` | 渲染单张工单（problem/concepts/files_to_change/hints） | `content_loader` |
| POST | `/enrollments` | 入学：mint TicketInstance（root=available，余=locked） | `service.enroll` |
| GET | `/instances?learner=` | 列学员实例 + 状态 | `service`/`store` |
| GET | `/instances/{id}` | 单实例 + **gate 指示文案**（下一步该做什么 git 动作） | `service` |
| POST | `/instances/{id}/start` | available→in_progress | `service.start` |
| POST | `/instances/{id}/provision` | 本地 clone track-repo fixture → 返回 workspace 路径 + dev 分支指示 | `ForgeClient`(Stub) |
| GET | `/instances/{id}/diff?base=` | **真本地 git diff**（`review_diff.generate_diff`） | `review_diff` |
| ~~POST~~ | ~~`/instances/{id}/pr`~~ | **已删（L3 / 设计 v0.6）**：不再有 PR；提交走 `submit` | — |
| POST | `/instances/{id}/submit` | 对账（fetch + 无损快进到学员 `dev`）→ 跑阅卷桩→GradingReport；in_progress→submitted | `GradingRunner`(Stub) + `service.submit` |
| POST | `/submissions/{id}/review` | 跑评审桩→Review；submitted→grading | `ai_review.generate_review` + `service.review` |
| POST | `/submissions/{id}/decide` | 确定性门；grading→passed\|failed；pass 则 unlock 下一阶段 | `service.decide` |
| POST | `/instances/{id}/retry` | failed→in_progress | `service.retry` |

**gate 指示**：平台不替学员跑 git 写操作（不 commit/merge/cherry-pick/push）。它在 `GET /instances/{id}` 与 provision/diff 响应里返回**文案指示**："请在本地 `dev` 分支完成 Stage00；完成后把 `stage-01` 分支合入 `dev`（cherry-pick / merge 都行），再开始 Stage01"。学员驱动，平台 gate（用 decide 的结果决定下一阶段是否解锁）+ 指示（告诉学员下一步 git 动作）。

**错误形状**：HTTP 错误统一 `{ "detail": "<短码>" }`（机器可判定），沿用平台既有约定。pydantic 校验失败时 `detail` 为标准数组。状态码：400 语义不可用 / 404 资源不存在 / 409 状态冲突（非法转移，映射 `IllegalTransitionError`）/ 422 形状不可用 / 500 内部。

### C.4 三个 Protocol 桩 + 双分支 fixture

**诚实占位原则**：桩不假装是真后端；UI 明标"模拟"；每个桩备 **pass 与 fail 两套 fixture**，确保 decide 两条分支都被真实跑到（不是只测 happy path）。

| Protocol（缝） | 方法 | 桩行为 | fixture |
|---|---|---|---|
| `GradingRunner`（新增于 `platform_service/grading.py`） | `grade(repo, *, ticket) -> GradingReport` | 返回预置 GradingReport | pass：`correctness.passed=True`；fail：`passed=False` |
| `ReviewEngine`（**已存在**于 `ai_review.py`） | `create_submission(...)` / `submit_for_ai_review(...)` | 返回预置 ReviewResponse 形状 dict | approve：`recommendation=approve`；request_changes：`recommendation=request_changes` |
| `ForgeClient`（新增于 `platform_service/forge.py`） | `provision(instance) -> Workspace`；`create_pr(instance, ...) -> str` | provision=本地 `git clone` track-repo fixture 到工作区目录；create_pr=返回占位 URL（如 `https://gitea.local/pr/SIMULATED-{id}`） | track-repo fixture：含 stage 分支的最小本地 git 仓库 |

**桩选择由 fixture 驱动**：A′ 用"按实例/提交选择 pass 或 fail fixture"的机制（如请求带 `?scenario=pass|fail`，或 provision 时绑定），以便前端能演示两条分支。**真实后端将来按 Protocol 替换，路由与 `PlatformService` 不变。**

**`StubReviewEngine` 与 `ai_review.generate_review` 的契合**：`generate_review(engine, session_id, *, diff, dimensions, ...)` 会先 `preflight_diff_volume(diff)`（真本地 git diff 通常远小于 120k，不触发 `ReviewDiffTooLargeError`），再 `engine.create_submission` → `engine.submit_for_ai_review`，最后 `_map_review_response` 投影成 `models.Review`。桩只需返回符合 §5.2 形状的 dict（`id` / `result.recommendation` / `result.summary` / `result.comments` / `result.unanchored` / `result.dimension_findings` / `result.truncated_files`）。`session_id` 形参 A′ 传实例 id 或占位串即可。

### C.5 git 进度模型（Track 固定阶段 + 学员驱动）

```
track-repo（fixture，将来=Gitea 上的真实 track 仓库）
  分支：main（骨架）· stage-00 · stage-01 · stage-02 …（各阶段起始代码）
        │
        │ ① provision：平台指示/桩执行 本地 clone
        ▼
学员本地 clone（workspace 目录）
  当前分支：dev（学员从 stage-00 切出）
        │
        │ ② 学员在 dev 上亲手写 Stage00 代码（平台不碰，D1：人类写被评分代码）
        ▼
  ③ diff：平台跑 git diff stage-00...dev（真本地 git，review_diff.generate_diff）
        │
        │ ④ submit→grade（桩）→review（桩）→decide（真门）
        ▼
  decide=pass ⇒ unlock stage-01 实例；平台指示：
        "把 stage-01 分支合入你的 dev（cherry-pick / merge），再开发 Stage01"
  decide=fail ⇒ retry；平台指示：按 review 意见修改 dev，重新 submit
```

> **L3 更新（设计 v0.6，本节仍是权威契约）**：真 Gitea 下**没有 fork、没有 PR**——只有**一个上游内容仓**，
> 学员直接把自己的 `dev` push 上去（loader 给他开写权限），平台 `provision` 就是 clone 那个仓。
> 上面流程图里"提 PR"的位置一律读作"push `dev`"；阅卷只需要"后端读得到那个 commit"，PR 从未参与
> 裁决（设计 §11.3 的输入是阅卷报告 + 评审建议 + 信任级 + 判定面篡改检测）。理由与代价见设计稿 §8.3。
> 因此 `§C.5` 在代码注释里指的是**本节这段 git 进度模型**：一阶段一 ref、平台只读、`head_ref` = `dev`。

**防剧透（D2，与设计 §8 一致）**：未解锁阶段的分支内容**不暴露**给学员 UI；`GET /tickets/{id}` 对 locked 实例只给元信息（标题/考点名词），不给该阶段起始代码或答案。stage 分支是独立 ref，clone 只检出已解锁阶段。

**平台不跑 git 写操作**：clone（provision）是只读拉取；commit/merge/cherry-pick 与 push 是**学员在自己机器上**做的（gate+指示）。diff 是只读 `git diff`，平台跑（已在 `review_diff`）。

### C.6 前端 `frontend/`

React + Vite + TypeScript SPA。建议屏幕（writing-plans 细化）：

1. **Track 总览**：stages 列表 + 各 stage 的 ticket 卡片 + 解锁状态（locked/available/in_progress/passed…）。
2. **工单详情（发卷）**：渲染 `ticket.yaml`——problem / concepts / files_to_change / hints；locked 阶段显示防剧透占位。
3. **工作区/进度**：实例状态 + **gate 指示文案**（git 下一步）+ provision 按钮 + 本地 workspace 路径展示 + diff 预览。
4. **提交/评审**：diff 视图 + 阅卷报告（桩，标"模拟"）+ AI 评审（桩，标"模拟"，按 dimension/anchor 展示行级评论）。
5. **决策结果**：pass → 显示"stage-01 已解锁"+ 推进指示；fail → 显示 retry 入口 + review 意见。

**诚实占位 UI**：凡桩产出的内容（阅卷分、AI 评审、PR URL）一律带"模拟"徽标 + 悬浮说明"当前为占位后端，真实 X 在未来切片接入"。

**前端只调 REST**：不碰 git/文件系统；所有 git 动作要么平台只读代跑（diff/clone），要么以文案指示学员本地执行。

### C.7 端到端数据流（A′ happy path + fail path）

```
发卷     GET /tracks/{id}                         → 渲染 Track（content_loader）
入学     POST /enrollments {learner}              → service.enroll：stage-00=available，余=locked
开始     POST /instances/{stage00}/start          → available→in_progress
备料     POST /instances/{stage00}/provision      → ForgeClient(Stub) 本地 clone fixture → workspace 路径 + dev 指示
开发     （学员本地 dev 分支手写代码——平台外）
取 diff  GET /instances/{stage00}/diff?base=stage-00 → review_diff.generate_diff（真本地 git）
提交     POST /instances/{stage00}/submit         → GradingRunner(Stub) 出 GradingReport；service.submit：in_progress→submitted
评审     POST /submissions/{sub}/review           → ai_review.generate_review(StubReviewEngine)；service.review：submitted→grading
决策     POST /submissions/{sub}/decide           → service.decide（真门）：
            pass 分支（pass fixture + approve）  → grading→passed；unlock stage-01
            fail 分支（fail fixture 或 request_changes）→ grading→failed
返工     POST /instances/{stage00}/retry          → failed→in_progress（再走 submit→review→decide）
推进     （stage-01 解锁后，学员 cherry-pick/PR 合入 stage-01，重复上述）
```

**两条 decide 分支都必须被 fixture 真实跑到**（C.4 双分支）：pass→unlock 与 fail→retry。

### C.8 错误处理

| 场景 | 处理 |
|---|---|
| 非法状态转移（如 locked 实例 start） | `transition` 抛 `IllegalTransitionError` → 路由映射 409 + 短码 |
| decide 缺 report 或 review | `service.decide` 抛 `ValueError`（service.py:170-173）→ 路由映射 409/400（先 submit+review 再 decide） |
| diff 首文件超 120k | `generate_review` 抛 `ReviewDiffTooLargeError` → 路由映射 422 + 提示拆分（A′ 真本地 diff 通常不触发，但路径存在） |
| 内容 YAML 不合 schema | `content_loader` 抛 `ContentValidationError`（含全部错误）→ 路由映射 400 + 错误清单 |
| provision 目标目录已存在/clone 失败 | `ForgeClient`(Stub) 抛错 → 路由映射 409/500 + 短码 |
| 未知 instance/submission id | `service` 抛 `KeyError` → 路由映射 404 |

路由层统一异常处理器把域异常映射成 `{ "detail": "<短码>" }`，不泄露堆栈。

### C.9 测试与验证策略（TDD）

**铁律**：无失败测试不写生产代码（superpowers:test-driven-development）。每个新 Protocol/桩/路由/屏幕先写测试、看它失败、再写最小实现。

| 层 | 工具 | 覆盖 |
|---|---|---|
| 域桩 | pytest（`.venv\Scripts\python.exe -m pytest`） | StubGradingRunner/StubReviewEngine/StubForgeClient 各返回正确 fixture；双分支（pass/fail）都测 |
| 编排 | pytest | `PlatformService` 端到端：enroll→…→decide 两分支；既有 test_service.py 已覆盖，A′ 补桩集成 |
| 路由 | pytest + FastAPI `TestClient` | 每个端点 happy + 错误形状（409/404/422/400）；DTO `extra="forbid"` |
| diff | pytest | `review_diff.generate_diff` 对真本地 git fixture 仓库跑通（既有 test_review_diff.py 已覆盖） |
| 前端单元 | vitest + React Testing Library | 组件渲染、"模拟"徽标存在、调 REST 的 mock |
| 端到端 | **browser-use MCP** | 启动 `platform_api`（uvicorn）+ `frontend`（vite dev），用浏览器真点：发卷→provision→submit→review→decide→看到 unlock/retry。**UI 改动必须在浏览器里验过才算完成**（项目铁律） |
| 静态 | ruff（`.venv\Scripts\python.exe -m ruff check .`） | E/F/I/UP/B，line-length=100，py311 |

**验证命令（Git Bash，无 make）**：
- 后端测试：`.venv/Scripts/python.exe -m pytest`
- 静态检查：`.venv/Scripts/python.exe -m ruff check .`
- 前端测试：`cd frontend && npm test`（vitest）
- 起服务：`.venv/Scripts/python.exe -m uvicorn platform_api.app:app --reload`（后端）；`cd frontend && npm run dev`（前端）

### C.10 切片任务分解（writing-plans 的输入预览）

按依赖序的原子步骤（每步 TDD：先测后码）：

1. **桩缝定义**：`platform_service/grading.py`（GradingRunner Protocol）、`platform_service/forge.py`（ForgeClient Protocol + Workspace 模型）。ReviewEngine 已存在。
2. **桩实现 + fixture**：StubGradingRunner（pass/fail GradingReport）、StubReviewEngine（approve/request_changes ReviewResponse dict）、StubForgeClient（本地 clone track-repo fixture + 占位 PR）；track-repo fixture（含 stage 分支的最小本地 git 仓库）。
3. **DTO 层**：`platform_api/schemas.py`（请求/响应模型，extra="forbid"）。
4. **组合根**：`platform_api/deps.py`（装配 PlatformService + 三桩）、`platform_api/app.py`（create_app）。
5. **发卷路由**：`routes/track.py`（GET /tracks, /tracks/{id}, /tickets/{id}）+ 防剧透（locked 只给元信息）。
6. **进度路由**：`routes/progress.py`（enroll/start/submit/review/decide/retry）+ 异常映射。
7. **工作区路由**：`routes/workspace.py`（provision/diff/pr）+ gate 指示文案。
8. **后端集成测试**：TestClient 跑通 C.7 两条 decide 分支。
9. **前端脚手架**：`frontend/`（Vite+React+TS）+ REST 客户端 + "模拟"徽标组件。
10. **前端屏幕**：Track 总览 / 工单详情 / 工作区进度 / 提交评审 / 决策结果（C.6）。
11. **前端单元测试**：vitest + RTL。
12. **端到端浏览器验证**：browser-use MCP 真点全流程（C.9）。

> 步骤 1-8（后端）与 9-11（前端）可在组合根契约（C.3 端点表 + C.4 桩接口）冻结后部分并行；步骤 12 收口。

---

## 2. 未决与后续切片（A′ 之后）

| 项 | 现状 | 后续 |
|---|---|---|
| 真实阅卷 | ✅ **已完成**（`DockerGradingRunner` + `--grading docker`）：跑测试前**默认**按 `runtime.compose` 重置环境（`down -v` + `up -d --wait`），再真跑 canonical 测试，surefire 结果映进报告 | canonical 测试**运行时注入**仍未做（已由**判定面硬门**顶上，设计 §11.3，实测见下）；判別性校验仍复用长活环境，宜改成一次性环境 |
| 真实 Forge | ✅ **已完成并端到端实测**（`GiteaForgeClient` + `--forge gitea`）：`provision` = API 确保 fork → clone 到工作区 → 建 `dev` 分支 → 把阶段分支的 SHA 记进本地 `bagu.baseRef`；`create_pr` = 真开 PR 并返回 `html_url`。token 只用于那次 clone，随后把 remote 改回干净 URL | private fork 的凭据配置要写进部署文档；判別性校验仍在长活环境里跑 |
| 真实 AI 评审/带教 | 桩 | 轻量 harness（如 DeepSeek harness）插入 `ReviewEngine` 缝；可能引入流式（届时再议 SSE/WS） |
| 持久化 | `InMemoryStore` | PostgreSQL `Store`（sqlalchemy + asyncpg，pyproject 已列 optional `service`） |
| 个性化 | 无 | LearnerProfile（设计 §10，P1）：画像驱动名词解释补强，不降考点难度 |
| 多 Track 管理 | 单 Track fixture | 贡献者后台（社区提交新 Track / 扩展八股面） |
| 退休执行 | ✅ **已执行**（Section B，获独立授权） | — |

**真阅卷的实测记录（2026-09-10，在一台装了 Docker/JDK17/Maven 的 Linux 机器上）**：

- 平台以 `--grading docker` 起，跑完 **三次 attempt** 的完整闭环（`报名 → start → provision(真 clone) → submit → review → decide → retry` ×2 → 改题 → submit → decide），全部通过。
- **attempt #1（骨架，未做题）**：`passed=False`，`searchReturnsCorrectOrderedResults=error`（`Invalid bound statement ... searchProducts`）+ `explainUsesIndex=fail`（`EXPLAIN 的 key 为 NULL`），`exit=1`、约 9.4s，`host_env.simulated=false`，越界文件被 `out_of_scope_edits` 抓到 → `decide` 判 **fail**（`tampering: []`）。
- **attempt #2（做对了，但把验收测试掏空）** —— 判定面硬门的关键证据：报告**确实变绿**（`passed=true`、`triviallyGreen=pass`、`exit=0`），说明"改测试变绿"这条通道**真实存在**；但真 `git diff` 认出改动里含 `src/test/.../ProductQueryIndexAcceptanceTest.java`，`decide` 的输入变成 `tampering: ["src/test/java/com/devteam/seckill/stage01/ProductQueryIndexAcceptanceTest.java"]`，于是**判 fail**。
- **attempt #3（还原判定面，保留解法）**：两条测试 `pass`、`exit=0`、约 7.8s → `tampering: []` → `decide` 判 **pass**，看板 `db-stage-01: passed`。越界笔记（`SCRATCH-notes.md`）三次都只出现在 `out_of_scope_edits` 里，**没有**被硬门误伤。
- **判别性只在干净环境里成立**：同一环境下先跑过参考解会把 `idx_product_search` 留在共享 MySQL 数据卷里，之后骨架的 `explainUsesIndex` 会**假绿**（详见设计 §15 风险 6）。
- **Track 侧同时修好**：`seckill-lab` 的 `solutions` 是历史分叉（缺 `track.yaml` / `stages/` / `scripts/` 等后加的 Track 契约文件），不能直接当 `<stage>-sol` 用；已改为**基于 `main`** 只带该阶段那两个文件的分支（§8.1 的「一阶段一 ref」），并声明了 `runtime.protected_paths`（§11.3 判定面）。

**"库里的残留"绕过的 A/B 实测（同一天，同一台机器）**：

- 机制：验收测试的 EXPLAIN 断言只看数据库里有没有索引。库里一旦建过一次，**之后把迁移脚本改回骨架的 `SELECT 1;` 也仍然是绿的**——工作区里看不出任何痕迹（真 `git diff` 干净，判定面硬门也拦不住，因为没改文件）。
- **phase A（`--grading-reset never`）**：先按参考解改对两个文件 → 绿，库里 `information_schema.STATISTICS` 有 3 行（复合索引三列各一行）；然后把**迁移脚本改回骨架版**再交卷 → **两条测试仍然全 pass**。绕过坐实。
- **phase B（默认 `auto`）**：同一份工作区、同一个实例状态 → 阅卷前重置 → `explainUsesIndex=fail`（`EXPLAIN 的 key 为 NULL`）、库里索引行数 = 0 → **红**；报告里 `host_env.infra.reset=true`（可审计）。再把迁移脚本改对 → 绿 → `decide` **pass**（诚实路径在重置之下照样走得通）。
- 重置的代价：`down -v` 后 MySQL 要重新初始化，本机实测**等到 TCP 就绪约 93s**（5 万行种子 + ANALYZE）。
- **顺带挖出一个 Track bug（已修）**：`docker-compose.yml` 的健康检查写的是 `mysqladmin ping -h localhost`——走的是 **unix socket**，而 entrypoint 初始化期间的**临时** mysqld 也在同一 socket 上应答，于是容器在"TCP 还没就绪"时就报 `healthy`，`up -d --wait` 提前返回，紧接着跑的测试连不上库（`CommunicationsException` / `EOFException`，两条用例全 `error`）——**平台会把这份环境问题的红记在学员头上**。改成 `-h 127.0.0.1` 强制 TCP 后，"healthy" 才代表"可连接"；`digest_pin.compose` 已用 Track 自己的 `scripts/digest-pin.sh` 重算（其余 pin 未变，已与脚本输出逐项核对）。

---

## 附录 — 退休 / 存活 / 改造 代码账

**存活（一字不改，A′ 直接复用）**：
- `platform_service/`：`service.py` · `store.py` · `models.py` · `lifecycle.py` · `unlock.py` · `acceptance.py` · `admission.py` · `content_loader.py` · `review_diff.py` · `ai_review.py` · `__init__.py`
- `tests/`：`test_service.py` · `test_store.py` · `test_models.py` · `test_lifecycle.py` · `test_unlock.py` · `test_acceptance.py` · `test_admission.py` · `test_content_loader.py` · `test_review_diff.py` · `test_ai_review.py` · `conftest.py`
- `content/schema/*.json`
- `docs/paper-platform-design.md`（修订，非退休）

**退休（Section B，独立授权删除）**：
- `orchestrator/`（prar_bridge.py · session_pool.py · __init__.py）
- `platform_service/mentor.py`
- `contracts/`（test_prar_contract.py · test_boundary.py · __init__.py）
- `tests/test_session_pool.py` · `tests/test_mentor.py`
- `prar-agent/` submodule + `.gitmodules` 段

**改造（Section B.3）**：
- `pyproject.toml`（packages / deps / testpaths）
- `platform_service/ai_review.py` · `review_diff.py`（仅 docstring；可选改名 `ENGINE_DIFF_CHAR_LIMIT`）

**新增（A′）**：
- `platform_api/`（app · deps · schemas · routes/{track,progress,workspace}）
- `platform_service/grading.py` · `platform_service/forge.py`（Protocol 缝）
- 桩实现：`platform_service/stubs.py`（StubGradingRunner / StubReviewEngine / StubForgeClient，与 C.2 分层一致——纯 / 仅本地 git&clone subprocess I/O）；真实后端将来落 `platform_api/` 侧适配层，由 `deps.py` 切换
- track-repo fixture：`tests/fixtures/track-repo/`（含 stage 分支的最小本地 git 仓库；provision/diff 测试共用）
- `frontend/`（Vite+React+TS SPA）
- 对应测试（pytest 路由/桩测试 · vitest 前端测试）

---

## 自检结论（spec self-review）

- **占位扫描**：无 TBD/TODO；所有"未来项"明确归入 §2 并标注环境缺失。
- **内部一致性**：C.3 端点表 ↔ C.7 数据流 ↔ C.10 任务分解 三者端点/步骤一致；C.4 桩接口 ↔ ai_review.py 实读签名一致（已核对 `generate_review` / `ReviewEngine`）。
- **范围**：聚焦单一实现计划（A′ 切片）；退休（B）与后续切片（§2）明确分离，不混入 A′。
- **歧义**：provision/diff/PR 三件占位的真假已逐一标注（clone=真本地、diff=真本地 git、PR=占位）；gate+指示的"平台不跑 git 写操作"边界已写明。
- **授权边界**：Section B 全程标注"独立授权、勿现在执行"；brainstorm 阶段不改 `paper-platform-design.md`、不删文件、不提交。

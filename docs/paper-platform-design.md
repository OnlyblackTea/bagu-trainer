# 发卷平台 (Paper Platform) · 总体设计稿

> **一句话**：一个**开源、可自托管**的"八股实战演练所"，供部署者（也是唯一用户）自习、刷题。平台从 seckill-lab 这类内容轨里**发工单**，部署者当程序员（**亲手写代码**），在**自己的设备**上跑 canonical 测试完成**本地阅卷**，类 GitHub 地 fork → 开发 → 提 PR → 过关 → 解锁下一张工单。AI 评审是**未来可选的挂件**，不在闭环的必经路径上（D4）。
>
> **定位**：供部署者（=用户）自习/刷题的**交互式演练所**，是 seckill-lab 的延伸；接受社区贡献（提交新 Track、扩展八股面）。**不含班级 / 排名 / 得分**，不是多租户 SaaS。
>
> **状态**：总体设计稿 v0.5（2026-09-10），实现期。**引擎无关**——A′ 引擎无关切片（`platform_api/` REST 控制面 + `frontend/` 学习者 SPA）已落地并验证。
>
> **变更记录**（v0.2–v0.4 条目中的 prar-agent / GPL / 契约冻结是**当时**的决策，v0.5 已退休，见 §6 决策记录）：
> - **v0.2** — 落定三层许可拓扑与 GPL 边界（§6.7），prar-agent 打 `v0.1.0` tag、补 GPL-3.0；seckill-lab=MIT。
> - **v0.3** — 据评审补强五处：① **评估版本钉 / 数据模型**：新增 `TicketInstance`（learner×ticket×engine×test×seed 版本钉）与 `AcceptanceDecision`（确定性验收门），重连实体关系（§9）。② **证据信任 + 评审/验收分离**：本地阅卷报告定性为**不可信 claim**（带 provenance），AI Review（咨询、非确定）与 AcceptanceDecision（确定、可审计）解耦（§7.4/§11）。③ **社区 track 供应链信任与安全模型**：track.yaml 拆 `manifest`(inert)/`runtime`(可执行)，引入 `trust_level` 一等概念 + 披露/digest 钉/sandbox 纵深防御（§4.2/§4.6/§13）。④ **prar-agent 契约面分级**：区分 v0.1.0 真稳定 API 与 planned API（§6.6）。⑤ **ticket schema**：`unlock_next`→`prerequisites` DAG、按 concern 命名空间分组（§4.3）。⑥ **许可措辞降调**为工程意图而非法律定论（§6.7/附录 A）。
> - **v0.4** — 据主人指示收缩范围 + 重定位：平台定位为**供部署者（=用户）自习/刷题的交互式演练所**（seckill-lab 延伸、接受社区 Track 贡献），删除班级/排名/得分相关冗余——**删** 参数化试卷（原 §12.3）、排行榜（原 §12.4）、认证模式 runner（原 §7.6）、rubric 权重判分（§11.2）、teacher-confirm 老师终审（§11.3）、`fixture_seed` 版本钉（§9.1/§9.2）与 `parametric` schema 命名空间（§4.3）；§2.3 部署拓扑三档→两档。**保留** AI Review 建议（approve/request_changes）+ AcceptanceDecision 确定性硬门（解锁不塌陷）+ `runner_trust`（恒 `self` 的诚实标记）+ 学习者画像（§10，剥排名耦合）+ AI 口试（§12.2）。
> - **v0.5** — **引擎退休 + 全稿契约对齐**：平台从"prar-agent 引擎驱动"改写为 **Track 内容 + git + 本地阅卷 + 自有 UI + 未来可选轻量 AI**。逐章处置：§0 增 **D4（引擎无关）** 支柱、D1 改述为结构性保证；§1 删"AI 经理 / Executor 六阶段映射"叙事，改述平台自有 `InstanceState`；§2 组件视图 / 算力表 / 部署拓扑去掉引擎；§3 三层解耦改为「内容 / 工具链 / 平台」，引擎模块对照表退役；§4 发卷措辞去 AI 味、`mentor_policy` 退役、`solutions` 改为**一阶段一 ref**；§5 流程表按平台状态与 REST 重写；**§6 整章退休**（保留一段决策记录）；§7 形态由 paper-cli 改为 `platform_api/`(REST) + `frontend/`(SPA)，报告格式对齐 `models.GradingReport`；§8 细化为「一阶段一 ref」；§9 `MentorSession` 退役；§10 学习者画像降为**未来切片**（不再声称复用 pgvector LTM）；§11 评审改为 `ReviewEngine` 缝；§12 口试降为**可选 harness**；§14/§15 路线图与风险重写；附录 A 亮点重拣、附录 B 退役、附录 C Q1/Q2 标记已决。决策依据：`docs/superpowers/specs/2026-09-10-learner-webui-and-prar-agent-retirement-design.md`。

---

## 0. 四条已拍板的设计前提

本设计严格遵循以下四条决策，全文不再回退：

| # | 决策 | 对设计的硬约束 |
|---|------|---------------|
| **D1** | **角色反转**：AI 不代写。平台从 seckill-lab 内容里**提出需求**，学员完成；AI 评审（可选挂件）只给建议 | 平台里**不存在** Executor 这个座位——不是"禁用了写代码的动作空间"，而是**根本没有会写业务代码的组件**。D1 因此是**结构性保证**，不靠 guardrail 守（§1.2） |
| **D2** | **fork 防剧透走君子协定**：让答案/后续进度"不那么方便"被看到即可，基于"用户主动参与"，防剧透**可少做甚至不做** | 只加**轻摩擦**（不 surfaced、需刻意操作），**不建硬墙**，不为藏答案牺牲架构简洁；落地形态 = **一阶段一 ref**（§8） |
| **D3** | **阅卷用使用方自己的算力**，不用平台云 | **本地优先阅卷**：学员设备跑 canonical 测试；平台中心**零阅卷算力**。LLM 推理（若将来启用挂件）也由部署方自带 key/本地模型——平台中心**零强制 LLM 成本** |
| **D4** | **引擎无关**：平台的依赖面只有 **Track 内容 + git + 本地工具链**；AI 只作为**可选挂件**接在缝上 | 平台不得把任何 AI/LLM 放在闭环的**必经路径**上。AI 评审缺席时，确定性验收门（§11.3）依然能独立裁决、独立解锁；反之，接上再强的模型也不能越过硬门 |

补充承继的前提（来自上一轮）：**自建 Gitea（或任意 git 服务器）** 承载 git/fork/PR，平台只**指示**、不代跑 git 写操作；**内容标准化 + track 脚手架生成器是开源社区能否贡献的命脉**，列为一等公民。

> **D2 与 D3 的协同**：既然阅卷在学员本机、防剧透走荣誉制，整个平台就**不需要中心化的可信计算**——这让"开源自托管"在成本和信任模型上都成立。代价是：平台**不提供"防铁了心作弊"的强保证**，详见 §13。
>
> **D4 的来源**：v0.4 曾以 prar-agent 为引擎（AI 经理发卷 / 导师陪练 / AI 评审）；该设计已退休，理由与决策记录见 §6，完整论证见 spec §0。

---

## 1. 核心洞察：Manager ↔ Human-Executor 角色反转

这是整个平台的架构支点，也是它区别于"给 agent 套壳"的关键。

### 1.1 起因：为什么最初想接一个 agent 引擎（已退休）

平台的起点是一个观察：通用的自治 agent 循环**天然是"AI 写代码、人看结果"**——

```
Manager(Planner LLM)     →  生成计划
User                     →  批注 / 答决策题 / 批准
Programmer(Executor LLM) →  调工具(shell/fs.write)动手执行
User                     →  review 动作结果
```

而 Executor LLM 会**把八股答案直接写出来**，与 seckill-lab "你亲手写代码、面试要讲出自己思路" 的教学哲学**正面冲突**。v0.4 的方案是引入 prar-agent 当引擎、把 ACTING 的执行者从 LLM 换成人类（详见 §6 决策记录）。

**该方案已于 v0.5 退休**：为了"让人占据执行位"而引入一个引擎、再给它加约束防止代写，是把简单问题做复杂——约束越精巧，越说明这一步本可以不引入。平台改为从**角色定义**上直接没有 Executor 这个座位（§1.2）；退休理由、代价与决策记录见 §6。

### 1.2 发卷平台的反转

**把"执行者"从「AI」换成「人」——并且平台里根本没有 AI 执行者这个角色。**

```
Track 内容(ticket.yaml) →  平台渲染「工单」：线上问题 / 考点 / 限定改动面 / 阶梯提示
学员                    →  读工单 → 在本地 IDE 亲手写代码（在 git 开发分支上留痕）
本地工具链              →  runtime 测试 / EXPLAIN / 并发 sim 跑在**学员自己的机器**上
学员                    →  提 PR + 填答辩模板（= 交卷）
确定性验收门            →  硬门（测试绿 + 证据信任级）→ pass/fail → 解锁下一张
[可选挂件] AI 评审      →  读 PR diff + 答辩 + 阅卷报告，锚定 diff 出**建议**（不置 passed、不参与解锁）
```

> **为什么这个反转优雅**：不是"给自治 agent 加约束防止它代写"，而是**从角色定义上就让人占据执行位**。
> 导师不代写不再是"需要守护的红线"，而是"它本来就不在 Executor 座位上"——平台里**没有**会写业务代码的组件，
> 因此 D1 无需 guardrail 去守：v0.4 的三层 guardrail（§6.4）随引擎一并退休（§6）。

### 1.3 平台状态机：`InstanceState`（v0.5 替换原六阶段映射）

v0.4 把 prar-agent 的 6 阶段逐阶段映射到平台语义。v0.5 退休引擎后，平台**直接持有自己的状态机**（`platform_service/models.py::InstanceState`；`platform_service/lifecycle.py` 的 `LEGAL_TRANSITIONS` 是迁移合法性的唯一判据），每个 `TicketInstance`（§9.1）落在恰好一个状态上：

| 状态 | 含义 | 进入条件 |
|------|------|---------|
| `locked` | 前置未过，不可接取 | 发卷时默认（`progression.prerequisites` 未全 `passed`，§4.3） |
| `available` | 已解锁，可接取 | 依赖的 `prerequisites` **全部** `passed`（DAG 判定，§9.2） |
| `in_progress` | 学员开发中 | 学员 `start`：工作区 provision 出本阶段的开发 ref（§8） |
| `submitted` | 已交卷：PR + 答辩 + 阅卷报告齐 | 学员 `submit`（同 Instance 内第 N 次 attempt 记为 `seq=N`） |
| `grading` | 评审 / 裁决进行中 | `review` 被调用（跑阅卷 + 可选 AI 评审） |
| `passed` | 通过 → 解锁下游 | **确定性验收门**判 pass（§11.3） |
| `failed` | 打回 → 可 retry | 验收门判 fail；retry = 同 Instance 新 Submission（`failed → in_progress`） |
| `stale` | 版本变更作废（保留历史） | `ticket_version` / `test_version` 变更 → re-issue 新 Instance（§9.2） |

**算力归属随之简化**：所有**状态迁移**都发生在平台（一台轻量服务）；重的部分（写代码、跑测试、跑 EXPLAIN）发生在**学员设备**上，平台只收一份"证据声明"（§7.4）。平台侧没有任何"等 LLM 产码"的状态——这正是 D1 结构性成立之处。

---

## 2. 总体架构

### 2.1 组件视图

```
                         ┌──────────────────────────────────────────────────┐
                         │           题库 content/ (开源 · Track 内容)          │
                         │  <track>/{track.yaml, stages/*/ticket.yaml,      │
                         │  canonical tests, bagu-map.md, hints/,           │
                         │  solutions/（按阶段切分为独立 ref，§8）}             │
                         └──────────────────────┬───────────────────────────┘
                                                │ 启动时读入（只读；惰性声明）
                                                ▼
┌────────────────┐   fork/PR   ┌────────────────────────────────┐   REST   ┌──────────────────┐
│   自建 Gitea     │◄───────────►│  平台服务 (FastAPI · 纯 REST)      │◄────────►│   学习者 SPA       │
│  git / fork/PR  │  平台只「指示」│  platform_service/  纯领域逻辑    │          │  frontend/       │
│  refs           │  不代跑 git 写 │  platform_api/      组合根       │          │  看板·发卷·工作区   │
└───────┬────────┘               └───────────────┬────────────────┘          │  提交评审·决策     │
        │ clone / pull / push                   │ 发卷 / 收 PR + 阅卷报告      └──────────────────┘
        ▼                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                学员设备（自带算力）· 本地 IDE + Track runtime 工具链               │
│  dev(在 stage 开发 ref 上写代码) → grade(运行时注入 canonical 测试，本地跑)         │
│  → push 分支 + 开 PR(答辩模板 + 阅卷报告) → 平台裁决 → 解锁下一阶段                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 算力原则（落实 D3）

| 计算 | 谁出算力 | 说明 |
|------|---------|------|
| git 托管 / fork / PR | 部署方的 Gitea（一台小机器即可） | 不是"我们的云"，是**自托管** |
| **阅卷（跑 canonical 测试 / EXPLAIN / 并发 sim）** | **学员自己的设备** | docker compose 起 MySQL(13306)+Redis(16379)+app，本地 `mvn test` |
| **AI 评审（可选挂件）** | **部署方自带** API key 或本地模型 | 平台中心**零强制 LLM 成本**（D4）：挂件缺席时闭环照走。挂件经 `ReviewEngine` 缝注入（§11.2） |
| 平台编排（状态机 / 解锁 / 发卷渲染 / 报告留痕） | 部署方平台服务（轻量） | 无重计算 |

> **结论：平台中心零阅卷算力、零强制 LLM 成本。** 部署者（也是唯一用户）用一台机器就能 all-in-one 跑起一个完整演练所（§2.3）——**不需要任何 LLM key**；想接 AI 评审时，再带上自己的 key 装上挂件。

### 2.3 部署拓扑（两档）

| 档 | 形态 | 适用 |
|----|------|------|
| **单机 all-in-one** | docker-compose 一把起 Gitea + 平台服务 + 前端（AI 挂件可选，缺席不影响闭环） | 部署者自习 / 刷题（唯一运行档） |
| **社区中心** | 官方维护**题库**（开源）+ track 准入校验 CI；不托管运行，只发内容 | 开源生态：大家各自部署，共享 track |

---

## 3. 三层解耦：内容 / 工具链 / 平台

| 层 | 是什么 | 现状 | 本平台新增 |
|----|--------|------|-----------|
| **内容层** | 题库：track / stage / ticket + 骨架 + canonical 测试 + 八股地图 + hints + 参考解 | seckill-lab = 第一个 Track（数据库八股），已有 stage 设计、验收测试、并发 sim、八股地图 | 把 seckill-lab 的**隐式约定显式建模**为 schema（`content/schema/`）+ 准入门 `validate_track`（§4.6）；脚手架生成器待做（§4.5） |
| **工具链层** | 阅卷：docker compose + maven + canonical 测试 + EXPLAIN + 并发 sim | seckill-lab 学员本来就在本地跑（§7.1） | **不改**，只把它**标准化 + 工具化**（`GradingRunner` 缝，§7） |
| **平台层** | 发卷 / 接卷 / fork-PR 指示 / 状态机 / 解锁 / 报告留痕 / 看板 | 无 | **全部新建**（本设计主体）——纯领域逻辑 + 纯 REST 控制面 + 学习者 SPA |

> **v0.5 删掉了第四层（引擎层）**：v0.4 曾把"AI 经理 / 导师 / 评审"当独立一层、由 prar-agent 承担。
> 退休后 AI 降为**可选挂件**，接在 `ReviewEngine` 缝上（§11.2），不再是一层架构——挂件缺席时，
> 上面三层依然构成完整闭环。这正是 D4 的落地形态。

### 3.1 平台自身的复用 / 新建（模块级对照）

v0.4 的"prar-agent 模块级复用对照表"已随引擎退休。平台当前的真实账目如下：

| 模块 | 是什么 | 状态 |
|------|--------|------|
| `platform_service/models.py` | 纯 Pydantic 领域模型（`TicketInstance` / `GradingReport` / `Review` / `AcceptanceDecision`…），`extra="forbid"` | 存活（§9.1） |
| `platform_service/lifecycle.py` · `unlock.py` · `acceptance.py` | 状态机合法性、DAG 解锁、确定性验收门（含**判定面硬门** `find_tampering`） | 存活（§9.2 / §11.3） |
| `platform_service/content_loader.py` · `admission.py` | 读 `track.yaml` / `stages/*/ticket.yaml`；schema + 准入门 | 存活（§4.2 / §4.6） |
| `platform_service/service.py` · `store.py` | 用例编排 + 存储（当前 `InMemoryStore`） | 存活；PostgreSQL 存储列入后续切片 |
| `platform_service/grading.py` · `forge.py` | 两个 Protocol 缝：`GradingRunner`（阅卷）/ `ForgeClient`（git / fork / PR） | 存活（§7 / §8） |
| `platform_api/grading_docker.py` | **真阅卷后端**：在工作区跑 `mvn -B -Dgroups=<test_group> test`，把 surefire 的真实结果映成 `GradingReport`（真实 provenance + 真 `git diff` 物证）；环境故障抛 `GradingUnavailableError` → 503 | **新建**（`--grading docker`；§7.1） |
| `platform_service/ai_review.py` · `review_diff.py` | `ReviewEngine` Protocol + 评审前 diff 预处理（体积预检、整文件截断） | 存活（§11.2） |
| `platform_service/stubs.py` | 三个"诚实占位"桩：Stub 阅卷 / Stub 评审 / Stub Forge（真本地 clone + 真本地 git diff，PR 为占位） | **新建**（真假逐项标注见 spec §C.9） |
| `platform_api/` | 组合根 + 适配层：FastAPI 纯 REST 控制面（`app` / `deps` / `schemas` / `routes/*` / `serve`）与真实后端实现（`grading_docker`） | **新建**（§5） |
| `frontend/` | 学习者 SPA（React + Vite + TS，**从零写**，Apache-2.0） | **新建**（§7.2） |
| 脚手架生成器 / 准入门 CLI / 学习者画像 / PG 存储 / 真实 Forge / canonical 测试运行时注入 | 未来切片 | 见 spec §2 后续切片表与 §14 路线图 |

> **两处诚实债务（纯改名/删字段即可收敛，留待真实挂件落地时一并处理）**：
> `TicketInstance.engine_version` 与 `GradingReport.engine_version` 是 v0.4 的版本锚点，退休引擎后语义悬空；
> `ai_review.ReviewEngine` 的 `session_id` 形参同样是引擎味命名。现在不动，是为了不让本次文档修订混进代码改动。

---

## 4. 内容模型与标准化 ★（社区贡献的命脉）

> 用户明确：这关乎开源社区能否 contribute 进来。**平台软件不是瓶颈，内容才是。** 本节是开源成败的核心。

### 4.1 三级内容结构

```
Track（八股方向，如"数据库八股"）
 └─ Stage / Module（工单序列，如 Stage1 索引&执行计划）
     └─ Ticket（工单 = 线上问题 + 考点 + canonical 测试 + hints + 八股讲解）
```

每个 Track 由一个**骨架仓 (skeleton repo)** 承载（seckill-lab 就是"数据库八股"track 的骨架仓）。

### 4.2 track manifest —— `track.yaml`

```yaml
track:
  id: database-bagu
  name: 数据库八股实战
  language: zh-CN
  trust_level: self              # self | verified | community（供应链信任级别，见 §4.6 / §13）

  # ── manifest：声明式元数据（惰性、不可执行；平台只读取来渲染看板 / 体检环境）──
  manifest:
    requires:                    # 学员设备前置（仅声明，平台据此做环境体检，不代装）
      java: "17"
      build: maven
    skeleton_repo: ./skeleton    # 发卷时 fork 的骨架
    bagu_map: ./bagu-map.md      # 八股地图（讲解锚点）
    stages:                      # 渐进解锁；每张 ticket 用 progression.prerequisites 表达依赖
      - stage-00-onboarding
      - stage-01-product-query-index
      - stage-02-order-transaction

  # ── runtime：可执行代码（供应链入口，真在学员设备上跑，是攻击面）──
  runtime:
    compose: ./docker/docker-compose.yml   # mysql:8.0@13306 + redis:7@16379（避开默认端口）
    build: ./scripts/build.sh              # 骨架构建
    tests: ./stages/*/tests/               # canonical 测试（运行时注入，§7.3）
    sim: ./stages/*/sim/                   # 并发模拟器（Stage3+）
    protected_paths:                       # ★判定面（§11.3，**必填**）：改了这些 → 验收门直接 fail
      - ./stages/*/tests/                  #   判据本身（canonical 测试 + fixture）
      - ./pom.xml                          #   构建配置（可以拿它"跳过测试"）
      - ./docker/                          #   基础设施（可以拿它弱化环境）
    digest_pin:                            # 锁定可执行依赖 digest，防供应链漂移
      mysql: "sha256:..."
      redis: "sha256:..."

  contribute:
    license: MIT                 # 内容仓许可（内容 MIT / 平台 Apache-2.0；含引擎 GPL 的三层拓扑已于 v0.5 随引擎退休，见 §6）
    maintainers: ["@xxx"]
    review:
      ci: paper validate-track   # 准入校验门（§4.6，CI 强制）
      signed_by: []              # verified track 需维护者签名背书
```

> **v0.3 结构变更（manifest / runtime 二分 + trust_level）**：track 的本质是"一组会在学员设备上执行任意代码的供应链包"，所以把 `track.yaml` 显式拆成两半——`manifest` 是**惰性声明式元数据**（平台只读取来渲染看板、体检环境，**不执行**），`runtime` 是**可执行入口**（compose/build/tests/sim，真在学员机器上跑，是供应链攻击面）。`trust_level` 把信任做成一等公民：`self`（自托管 / 自己写的，默认信任）、`verified`（维护者背书 + digest 锁定 + 过 CI 准入）、`community`（第三方贡献，**默认不完全信任**——主要防线是披露 + 评审 + digest 锁定 + 声誉，sandbox 只是纵深防御而非硬保证，详见 §13）。这与 §4.6 准入校验门、§13 供应链威胁模型配套。


### 4.3 ticket schema —— `stages/stage-NN/ticket.yaml`

把 seckill-lab 的 `stage-NN.md` **结构化**，平台才能渲染、验收、控解锁：

```yaml
# ── identity：身份 + 版本锚点（钉进 §9.1 TicketInstance，回答"这 PR 基于哪个版本"）──
identity:
  id: db-stage-01
  schema_version: 1              # ticket.yaml 结构版本（schema 演进用）
  ticket_version: 1              # 本工单内容版本；改动题面/判据 → bump → 旧 Instance 失效（§9.2）
  stage: 1
  title: 商品搜索查询慢

# ── brief：题面（平台据此渲染工单；**不含答案**）──
brief:
  problem: |                      # 线上问题（工单背景）
    运营反馈商品列表页"按分类+上架状态+价格区间"搜索越来越慢，
    product 表 5 万行全表扫描，DBA 说一个合适的二级索引都没有。
  concepts: [最左前缀, 覆盖索引, EXPLAIN, 索引失效]

# ── scope：限定改动面（便于评审与防越界，§7.3）──
scope:
  files_to_change:
    - src/main/resources/mapper/ProductMapper.xml
    - src/main/resources/db/migration/V1__stage01_product_search_index.sql

# ── grader：判据（客观红绿，学员设备本地跑；其结果按"不可信证据"处理，§7.4）──
grader:
  correctness:
    test_group: stage01
    test_version: 1              # canonical 测试版本；与 ticket_version 共同钉进 Instance
    canonical_test: tests/ProductQueryIndexAcceptanceTest.java   # 运行时注入（§7.3）
    expect: "初始红(未绑定+无索引) → 改对后绿"
  performance:
    type: explain
    assert: "type != 'ALL' && key != null && Extra not contains 'Using filesort'"
  concurrency: null              # Stage3 才有：sim/零超卖

# ── hints：阶梯式提示（默认折叠，展开计入画像）──
hints:
  - id: H1
    text: "@Sql 每次测试方法前自动执行 V1 脚本，脚本幂等，不用手动建索引"
  - id: H2
    text: "EXPLAIN 断言的查询形状与 searchProducts 一致"
  # H3/H4...

# ── submission：PR 即答辩（§11）──
submission:
  template:
    require:
      - 改后的 searchProducts SQL
      - 索引列序 + 一句话理由
      - 建索引前后两次 EXPLAIN 输出对比
  bagu_map_ref: "bagu-map.md#stage-1-索引--执行计划"

# ── mentor_policy：⚠ v0.5 退休（原 per-ticket 导师权限）──
# 引擎退休后，平台里没有任何"会动工具的导师"角色，本块**当前无消费方**。
# 但它**故意保留在 schema 里**、且仍是必填字段，原因有二：
#   ① 已发布的 Track（seckill-lab 的 stage-01）仍写着它，删字段会让存量 track 校验失败；
#   ② 未来的可选 AI 导师挂件若需要 per-ticket 权限，仍从这里读。
# `platform_service/models.py::MentorPolicy` 同步保留，D1 语义（绝不写 files_to_change）不变。
mentor_policy:
  mode: pure_mentor             # pure_mentor | mentor_then_reveal（当前无消费方）
  may_write: []                 # 绝不写 files_to_change（D1）

# ── progression：解锁依赖（v0.3 反转——声明"我依赖谁"，而非"我解锁谁"）──
progression:
  prerequisites: [db-stage-00]  # 单前置时退化为线性；多前置 = DAG（§9.2）
```

> **v0.3 结构变更（namespace 分组 + unlock_next→prerequisites + 版本锚点）**：
> ①**按 concern 分组**——identity/brief/scope/grader/hints/submission/mentor_policy/progression，平台渲染、验收、防越界、控解锁各取所需，互不串味（`mentor_policy` 已于 v0.5 退休但**保留在 schema 里**，理由见上方 inline 注释）；
> ②**`unlock_next` 反转为 `progression.prerequisites`**——工单不再"向前指"下一张（那样新增/重排工单要改一片），而是"向后指"自己依赖谁，整体构成 DAG，解锁判定 = "prerequisites 全 passed"（§9.2）；单前置时天然退化为线性，迁移零成本；
> ③**版本锚点**——`schema_version` / `ticket_version` / `grader.correctness.test_version` 与 `engine_version` 一起在发卷时钉进 `TicketInstance`（§9.1），从而能回答用户提的五个硬问题（"这 PR 基于哪个 ticket 版本？""grading report 是哪个 test version 跑的？""题目更新后旧 submission 还有效吗？""平台更新后旧 submission 用哪个平台版本？""retry 是重新发卷还是续上一张？"）——全部在 schema 层解决，不留给运行时扯皮。


### 4.4 track 资产清单（贡献者必须提供）

| 资产 | 必需 | 说明 |
|------|:---:|------|
| `skeleton/` 骨架仓 | ✅ | 能编译、能 `docker compose up`、冒烟测试绿 |
| `stages/*/ticket.yaml` | ✅ | 工单（结构化） |
| `stages/*/tests/` canonical 测试 | ✅ | **判别性**：骨架上红、参考解上绿（准入校验门把关） |
| `bagu-map.md` 八股地图 | ✅ | 过关后讲解锚点 |
| `hints/` | ✅ | 阶梯提示 |
| `solutions/` 参考解 | ✅ | **按阶段切分为独立 ref**（`<stage>-sol`，§8）：准入校验（骨架红 / 解绿）+ 过关后对照；开源下不藏 |
| `runtime.protected_paths` 判定面 | ✅ | 声明"判据依赖哪些文件"（§11.3）：改了它**即使阅卷绿也判 fail**；准入门会验证每条声明真能在骨架里匹配到——**写错一个字母就是静默漏洞** |

### 4.5 track 脚手架生成器 —— `paper new-track`（AI 辅助）

降低贡献门槛的关键工具。**当前状态：未实现**——A′ 切片只落了内容 schema（§4.2/§4.3）与准入门（§4.6）；脚手架生成器是 spec §2 后续切片表。将来即使接上可选 AI 挂件来起草草稿，**生成器本体也不许依赖 LLM**（D4：离线也能手工出卷）。

```
paper new-track --topic "JVM 八股" --concepts "GC,类加载,内存模型,JIT"
```

生成：
1. 骨架仓脚手架（build 配置、docker 基础设施、CI 配置、目录约定，仿 seckill-lab `docs/架构与约定.md`）；
2. `track.yaml` + 每个 stage 的 `ticket.yaml` 草稿（"线上问题 + 考点 + files_to_change"；接了 AI 挂件可由它起草，没接就手写）；
3. canonical 测试**桩**（断言骨架，作者补全）；
4. `bagu-map.md` 骨架；
5. 一份**准入校验清单**（作者照单自测）。

> 若接上可选的 AI 挂件，AI 在这里是"出题助手"，作者是"出题人"——人最终决策验收每张工单的质量，与教学闭环的哲学一致。

### 4.6 社区贡献工作流 + 准入校验门（最重要）

```
贡献者 fork 题库 → （`paper new-track` 起草，未来工具）→ 补全工单/测试/八股地图
   → 本地跑 `paper validate-track`（= `admission.validate_track` 的 CLI 包装，待做）→ 提 PR 到中心题库
   → 题库 CI 自动校验 → 维护者 review → merge → track 进 catalog
```

**准入校验门 `paper validate-track`（CI 强制）** —— 没有它，社区 track 必然良莠不齐：

> **当前状态**：静态门**已实现**为 `platform_service/admission.py::validate_track`（内容 schema / `files_to_change` 存在 / 八股锚点可解析 / `runtime` 每个已声明入口都被 `digest_pin` 覆盖 / `trust_level` 背书 / D1 角色反转 / **ticket 目录覆盖**）；包一层 CLI 与 CI 接线是后续切片。
> A′ 阶段另补了一条**硬门**：`manifest.stages` 必须覆盖所有被发现的 `stages/*/ticket.yaml`（允许多声明 board-only 阶段，但**不能漏**）——身份与发现的契约见 §8。

| 校验项 | 判据 |
|--------|------|
| 骨架可构建 | `mvn -DskipTests compile` 成功 |
| 基础设施可起 | `docker compose up` 后服务 healthy |
| **测试判别性（核心）** | canonical 测试在**骨架**上 = 红；在 **solutions** 上 = 绿。两者都验，防"永远红/永远绿"的废测试 |
| ticket schema 合法 | `ticket.yaml` 过 JSON Schema |
| files_to_change 存在 | 工单声明的待改文件在骨架里确实存在 |
| 八股地图锚点有效 | `bagu_map_ref` 指向的章节存在 |
| 无泄题 | 工单/hints 文本里不含 solutions 的关键实现（粗筛 + 维护者人审） |
| **manifest/runtime 二分合规** | `manifest` 不含可执行内容；`runtime` 声明的每个可执行入口（compose/build/tests/sim）都有 `digest_pin` |
| **判定面已声明且真实存在** | `runtime.protected_paths`（§11.3，必填）每条都要能在骨架里匹配到东西——**写错一个字母就是静默漏洞**（门会放过改了 canonical 测试的人），所以要硬卡 |
| **trust_level 标注** | `community` track 必须显式标 `trust_level`；升 `verified` 需 `signed_by` 维护者签名 + digest 锁定 |

> **供应链告警（核心安全认知）**：community track 不是"数据"，而是**一组会在学员设备上执行任意代码的供应链包**——`runtime.compose` 能起任意容器、`runtime.build` 能跑任意脚本、canonical 测试本身就是可执行代码。所以 `validate-track` 不只校验"内容质量"，更要校验"runtime 入口被 digest 锁定、被 CI 在隔离环境跑过、trust_level 被如实标注"。完整威胁模型（信任分级、sandbox 纵深防御及其在学员机器上的**诚实边界**）见 §13。

---

## 5. 端到端流程

```
选方向 → 接工单 → 发卷 → 开发(本地 IDE + runtime) → 本地阅卷 → 提交 PR(=答辩)
       → [可选]AI 评审 → 确定性裁决 → 过关 → 讲八股 → 解锁下一张 → (循环)
```

| 步 | 学员做 / 看到 | 平台做什么（REST） | 算力 |
|----|-------------|------------------|------|
| **1 选方向** | 浏览已部署的 Track 与其 stage 看板 | `GET /tracks` · `GET /tracks/{id}`（board 含 `board_only` 阶段，不取卷） | 平台 |
| **2 报名 + 接工单** | 看 stage 看板（🔒/🟥/✅，沿用 seckill-lab TASKS 看板语义），点"接取" | `POST /enrollments` 建 Enrollment 并**批量发卷**（按 `progression.prerequisites` 算 DAG 初始状态：无前置 ⇒ `available`，否则 `locked`）；`GET /instances` 出看板 | 平台 |
| **3 发卷** | 读工单：线上问题 / 考点 / `files_to_change` / 阶梯提示 | `GET /tickets/{ticket_id}`（**只给题面**；答案与参考解**不 surfaced**，§8）；`POST /instances/{id}/start` → `in_progress` | 平台 |
| **4 起工作区** | 在本地 IDE 写代码；需要时 `docker compose up` 起 runtime 依赖 | `POST /instances/{id}/provision`：clone 骨架仓、checkout 本阶段 `base_ref`，落到 `head_ref` 开发分支 | 平台（指示）+ **学员设备** |
| **5 开发** | 在 `head_ref` 上提交；随时看平台给出的 diff 概览 | 平台**不跑** git 写操作，只指示（§8）；`GET /instances/{id}/diff` 出 base↔head diff（真本地 git） | **学员设备** |
| **6 本地阅卷** | 跑 canonical 测试 / EXPLAIN / 并发 sim，产出**结构化阅卷报告** | 收报告（`GradingRunner` 缝）。**报告是不可信证据**：自带 `provenance` + `runner_trust`（§7.4） | **学员设备** |
| **7 提交 PR** | 填**答辩模板** + 附阅卷报告 → 开 PR | `POST /instances/{id}/pr` 生成 PR 描述 + 体积预检；`POST /instances/{id}/submit` → `submitted` | 学员设备 + Gitea |
| **8 [可选] AI 评审** | 收到锚定 diff 的**点评** + approve/request_changes 建议 | `POST /submissions/{id}/review`：跑阅卷 + 可选评审 → `grading`。**挂件缺席时照常产出结论，闭环不中断**（§11.2 / D4） | 平台（+ 可选部署方 LLM） |
| **9 裁决** | 看是否通过；不过则按点评改 → `POST /instances/{id}/retry` 回到 4 | `POST /submissions/{id}/decide` → **确定性验收门**（§11.3）：`correctness` 硬门 ∧ `review=approve` ∧ 证据信任级 ∧ `auto` ⇒ `passed` / `failed` | 平台 |
| **10 讲八股** | 过关后读 `bagu-map.md` 对应章节，对照参考解 | 渲染 `submission.bagu_map_ref` 指向的章节 + `<stage>-sol` 参考解（§8） | 平台 |
| **11 解锁** | 看板下一张变 🟥 | `passed` 触发 DAG 解锁（§9.2）：把本 ticket 列为 prerequisite 的下游 Instance `locked → available` | 平台 |

> **与 v0.4 的两处实质差别**：① 第 3 步不再是"AI 生成工单 plan"，而是**直接渲染 Track 内容**——题面是内容作者的固定产物，不是每次发卷即兴生成的，因此**可复现**（同一 `ticket_version` 每次发卷看到的题面一致）；② 第 8 步是**可选挂件**，缺席时第 9 步的确定性门照样独立裁决（D4）。

---

## 6. 决策记录：为什么引擎层被退休（v0.5）★

> **本章是历史决策记录，不是现行设计。** v0.4 的 §6 是一整章"prar-agent 复用与改造"（多 session 编排 / PLANNING 改造 / 导师 sub-loop / 三层 guardrail / submodule 集成策略 / 契约冻结 / GPL 边界）。v0.5 把整章退休，只留这一段决策记录；**现行架构见 §1–§3、§7、§11**，完整论证见 `docs/superpowers/specs/2026-09-10-learner-webui-and-prar-agent-retirement-design.md` §0 与 Section B。

### 6.1 曾经的选择

v0.4 的判断是：平台的"AI 经理 / 导师 / 评审"三种角色与 prar-agent 的 6 阶段状态机高度同构，于是打算**复用引擎内核**（状态机、plan engine、critic、记忆、LiteLLM router、checkpoint），只在平台层做多 session 编排（`orchestrator/session_pool.py`），并给引擎加 `mentor_mode`（`actor="human"` + workspace tool policy）来保证"人类亲手写代码"。源码用 **git submodule** 引入、钉 `v0.2.0` tag，运行时只经 `orchestrator/prar_bridge.py` 打它的 HTTP/WS API；为降低 GPL-3.0 的 copyleft 波及，专门立了一条"平台永不 import 引擎内部符号"的边界纪律，并用 `contracts/test_boundary.py` 守卫。

### 6.2 为什么放弃

1. **代价与收益不成比例**：D1 想要的是"人占据执行位"。引入一个自带 Executor 的引擎、再层层加锁（工具白名单 / 角色不调用 / 输出守护）去阻止它写代码，等于先造一个风险再治理它——不引入就没有这个风险（§1.2）。
2. **平台的闭环不需要 LLM**：发卷 = 渲染 `ticket.yaml`（内容是固定产物，本就可复现）；开发 = 学员在本地 IDE 写；阅卷 = 本地 canonical 测试；解锁 = 确定性验收门（§11.3）。LLM 只在"点评"这一环有价值，而它是**建议性**的——把它放进必经路径，等于让一个非确定性组件持有解锁权。
3. **依赖面收窄**：退休引擎后平台只剩三个依赖——**Track 内容 + git + 本地工具链**（D4）。少一个快速演进的上游、少一套契约冻结流程、少一条 GPL 边界纪律，也少一整类"引擎升级把平台炸了"的故障。
4. **许可拓扑简化**：不再有 GPL 代码进仓，平台干净 Apache-2.0；v0.4 §6.7 那套"进程外边界 ⇒ 降低衍生作品风险"的工程论证**整条消解**（不是结论变了，而是前提没了）。

### 6.3 代价（诚实记账）

- 失去引擎自带的**多 session 编排、沙箱、git checkpoint、pgvector 长期记忆、LiteLLM 路由**。其中只有"记忆"是平台真需要的（学习者画像，§10）——已降为后续切片，将来用平台自己的 `Store`（PostgreSQL）实现，不再借道引擎。
- **AI 评审与 AI 口试失去现成实现**，降为可选挂件（§11.2 / §12.2）。这是可接受的：它们是**增量**，不是闭环的一部分。
- 已写好的引擎适配代码（`orchestrator/`、`platform_service/mentor.py`、`contracts/`）成为**待删的死代码**；删除清单与验收见 spec Section B——**本节只做决策记录，删除需独立授权**。

### 6.4 边界纪律的去向

那条"平台永不 import 引擎内部符号"的纪律随引擎退休而失效；取而代之的是一条**同类但更简单**的纪律（D4）：**平台不得把任何 AI/LLM 放在闭环的必经路径上**——AI 只能接在 `ReviewEngine` 缝（§11.2）后面，只出建议。`ReviewEngine` 的 `session_id` 形参、`TicketInstance.engine_version` 字段是这次退休留下的**命名债务**（§3.1），待真实挂件落地时统一收敛。

---

## 7. 本地阅卷 ★（学员自有算力，落实 D3）

### 7.1 为什么本地优先

1. **零云成本**：契合 D3 与开源自托管；平台中心**零阅卷算力**，也不引入强制 LLM 成本（D4）。
2. **契合 seckill-lab 既有事实**：它本来就要求学员本地 `docker compose up` 真 MySQL(13306)+Redis(16379) 跑 `mvn test -Dgroups=stageNN`（**拒绝 H2/Testcontainers**，因为 InnoDB 间隙锁/MVCC/EXPLAIN 行为不对会教错八股）。本地阅卷 = 把这个既有动作**工具化、标准化**。
3. **真环境真信号**：并发 sim（Stage3 零超卖）这类测试在学员真机上跑最贴近实际。
4. **平台侧只留一个缝，缝上挂两个后端**：阅卷经 `platform_service/grading.py::GradingRunner` Protocol 注入，由 `platform_api/deps.py` 按 `--grading` / `BAGU_GRADING` 选：
   - **`stub`（默认）**——`StubGradingRunner`："诚实占位"。**什么都不跑**，只按 `scenario` 返回一份**预置**的 `GradingReport`，并在 `provenance.host_env` 里打上 `{"simulated": true}`（不假装跑了 maven，也不假装测过）。
   - **`docker`（真实）**——`platform_api/grading_docker.py::DockerGradingRunner`：在工作区真跑 `mvn -B -Dgroups=<test_group> test`，把 **surefire XML** 映成逐条测试结果（**断言失败 `fail` 与异常 `error` 分开**，这两者对学员的意义不同）；`files_changed` / `out_of_scope_edits` 来自**真 `git diff`**（有 provision 记下的基线 ref 就按它比，能看见**已提交**的改动）；`provenance.host_env` 记 java / maven 版本、退出码、耗时。
     环境故障（缺 maven、工作区不存在、ticket 没声明 `test_group`）抛 `GradingUnavailableError` → 路由映成 **503 `grading_unavailable`**：**绝不伪装成 `passed=False`**，否则等于把平台配置问题栽给学员。

### 7.2 学习者命令面：CLI → REST + SPA（形态已改）

**v0.5 变更**：v0.4 设想的形态是 `paper-cli`（学员设备上的命令行客户端）。A′ 已把命令面改为
**平台 REST 控制面 + 学习者 SPA**（浏览器即可完成全流程）；`paper-cli` **降级为可选未来项**
（`paper_cli/` 目前只是脚手架 docstring）。理由：SPA 天然贴合"看板 + 工单 + diff + 评审"这类渲染密集界面，
而 CLI 的每一件事都能落成一次 REST 调用——**命令面即端点表**，不必维护两套。

| v0.4 设想的命令 | 现在的形态 | 端点 / 页面 |
|----------------|-----------|------------|
| `paper enroll <track>` | SPA 报名 + 接取 | `POST /enrollments` |
| `paper up` | 学员自己在本地跑 Track 的 `runtime.compose`（平台不代跑） | —（本地工具链） |
| `paper dev` | 用自己的 IDE；SPA 只提示工作区路径与分支 | `POST /instances/{id}/provision` |
| `paper mentor` | **退休**（无导师引擎）；未来可选挂件，见 §11.2 | — |
| `paper grade` | 本地跑测试 → 提交阅卷报告 | `POST /submissions/{id}/review` |
| `paper submit` | SPA 填答辩模板 + 开 PR | `POST /instances/{id}/pr` · `POST /instances/{id}/submit` |
| `paper sim` | 本地跑并发模拟器，结果并入阅卷报告 | —（本地工具链，§7.5） |

> 端点的权威清单以代码为准（`platform_api/routes/{track,progress,workspace}.py`）；错误一律是
> `{"detail": "<短码>"}`，短码由前端翻成人话（spec §C.8）。CLI 若将来补上，只是这层 REST 的又一个客户端。

### 7.3 canonical 测试的运行时注入（轻摩擦防篡改，呼应 D2）

- 学员的考卷仓**不含** canonical 测试（`src/test` 里只有冒烟测试占位）。
- 本地阅卷工具（脚本 / 未来的 CLI / SPA 触发的本地任务）运行时从**学员 PR 改不到的来源**拉 canonical 测试注入：
  - 来源选项：题库私有 ref（推荐）/ 本地内置模板；
  - 注入到一次性目录，跑完即清，**不落进学员可提交的 working tree**。
- 学员对 `src/test`、`pom.xml`、fixture 的改动**一律不参与阅卷**（grader 只认 canonical 版本 + `files_to_change` 白名单内的业务改动）。

> 这是**轻摩擦**：挡住"顺手改测试作弊"，但**不挡铁了心的人**（D2 君子协定，§13）。

> **⚠ 本节（运行时注入）仍未实现**：现状是 `runtime.tests` 与 `ticket.grader.correctness.canonical_test`
> 都指向**仓内 `src/test`**——canonical 测试**就在学员的工作区里**，"注入、学员改不到"还只是设计意图。
>
> **已用一条更便宜的替代挡住主要通道**：Track 声明 **`runtime.protected_paths`（判定面）**，平台在
> `decide` 时把它与真 `git diff` 出的 `files_changed` 求交，非空即 **fail**（§11.3 的 `tampering`
> 输入）——**哪怕报告全绿**。seckill-lab 现在声明的是 `src/test/`、`pom.xml`、`scripts/`、`docker/`。
> 准入门会检查每条声明**真的指向骨架里的东西**：写错一个字母就是静默漏洞（门会放过改了测试的人），
> 所以宁可让 `validate_track` 响亮地失败。
>
> **剩下的口子（诚实记账）**：① 判定面是 Track **自己声明**的，声明漏了就等于没保护——这依赖内容
> 作者 + 准入门，不是平台能推断出来的；② 报告本身是**不可信自报告**（§7.4），`files_changed` 也由
> 它提供，所以在"铁了心伪造"那一档上这条门同样能绕过（D2 荣誉制，§13.1）。它挡的是**顺手改测试**，
> 不是密码学级防伪。

### 7.4 阅卷报告（evidence）结构化格式

本地阅卷产出 `grading-report.json`（= `models.GradingReport`，`extra="forbid"`，未知字段直接校验失败），可选 AI 评审据此点评：

```json
{
  "submission_id": "sub-001",
  "ticket": "db-stage-01",
  "ticket_version": 1,
  "test_version": 1,
  "engine_version": "a-prime-0.1.0",
  "learner": "deployer",
  "provenance": {
    "generated_at": "2026-09-10T10:00:00Z",
    "generated_by": "local-grader@0.1.0",
    "host_env": {"os": "...", "mysql": "8.0@13306", "redis": "7@16379", "java": "17"},
    "runner_trust": "self"
  },
  "correctness": {
    "test_group": "stage01",
    "passed": true,
    "tests": [{"name": "shouldReturn3SortedByPrice", "status": "PASS"}]
  },
  "performance": {
    "explain_before": {"type": "ALL", "key": null, "rows": 50000, "extra": ""},
    "explain_after":  {"type": "ref", "key": "idx_product_search", "rows": 3, "extra": "Using index"},
    "assert_pass": true
  },
  "concurrency": null,
  "lint": {"violations": 0},
  "files_changed": ["ProductMapper.xml", "V1__stage01_*.sql"],
  "out_of_scope_edits": []
}
```

> **`engine_version` 的现状**：字段名是 v0.4 的版本锚（当时钉的是引擎版本），现在装的是**平台版本**
> （`platform_api/deps.py::PLATFORM_VERSION`，当前 `a-prime-0.1.0`）——模型遗留命名，见 §3.1 债务。

> **报告怎么拿到**：`GET /submissions/{submission_id}/report`（报告在 `submit` 时产生；此前调用 404 `report_not_found`）。
> 桩时代报告内容是预置的，看不出缺这个端点；接上真后端后它是**必需**的——否则学员只知道"没过"，不知道**哪条测试、为什么、在什么环境下**没过。

> **v0.3 定位变更：grading-report = 不可信证据（claim，带 provenance）**。本地阅卷发生在学员自有设备（D3），平台**无法把红绿当事实**——`correctness.passed` 是学员侧工具的**自报声明**，不是已验证事实。所以报告必须自带 `provenance`（谁、什么环境、何时）+ 版本锚点（钉回 §9.1 TicketInstance）+ `runner_trust` 信任级，且**证据继承与 track 同源的信任分级**（self/verified/community，§13.2）：
> - `self`：自习平台（v0.4 起）的**唯一**取值——没有受控 runner 复跑，红绿始终是学员侧工具的自报声明，靠荣誉制（§13.1）而非密码学签名背书。
>
> 这一条统一了三个看似不同的问题——"本地阅卷可伪造""社区 track 可投毒""AI 评审权力过大"——它们本质都是**信任分级**问题：AI Review **读**证据并给建议，AcceptanceDecision（§11.3）按"证据信任级 + 测试硬门 + 配置"**裁决**，谁都不盲信单一来源。

### 7.5 并发 / 性能测试的特殊处理（Stage 3+）

- 并发 sim（`SeckillLoadSimulator`）**慢且可能 flaky**：与正确性测试**分开评级**。
- 并发模拟器用受控并发跑，输出 QPS + **超卖数**；超卖数必须 = 0 才算过。
- flaky 容忍：可重试 N 次取多数；报告里标**置信度**；评审对低置信度结果保守处理（必要时请学员复跑）。

---

## 8. git 进度模型与防剧透（轻量 · 君子协定，落实 D2）

**设计原则：不建硬墙，只加"轻摩擦"，依赖"用户主动参与"的君子协定。** 开源场景下答案本就藏不住（公开 repo 的参考解分支照样能 checkout），死磕防剧透是浪费。

### 8.1 一阶段一 ref（v0.5 明确的契约）

**一个阶段 = 一条独立分支**，分支名 = `track.yaml.manifest.stages` 里的条目 = `stages/<name>/` 目录名：

| ref | 内容 | 谁来用 |
|-----|------|-------|
| `<stage>` | 本阶段的**开发基线**（骨架 + 之前阶段已完成的代码）——学员从这里开始写 | `provision` 的 `base_ref` |
| `<stage>-sol` | 本阶段的**参考解**（仅准入校验与过关后对照） | 准入校验 / §12.1 |

- 平台只**解析**这些 ref 是否存在（`git rev-parse --verify`），**不创建**它们（Track 侧负责）。
  ref 缺失时接口返回短码 `workspace_ref_missing`，并把仓库里**实际可用的 ref 列表**一并告诉学习者，而不是抛一个裸 500。
- `base_ref` / `head_ref` 由平台按阶段推导（`content_loader.load_stage_refs`）：`base_ref` = 阶段分支，`head_ref` = 学员自己的开发分支。
  **绝不用 `ticket.identity.id` 当分支名**——身份（DAG 的键）与分支（内容发布单位）是两个命名空间，混用会让解锁判定与实际 checkout 的内容悄悄错位。

> **为什么必须一阶段一 ref**：防剧透的全部机制都建立在这上面。学员 clone 时只检出**已解锁阶段**的 ref，
> 未解锁阶段的骨架与参考解**不在他的工作区里**；若所有阶段的解都挤在一条 `solutions` 分支上，
> 那么一次 clone 就等于把全部答案摊开——防剧透从"轻摩擦"直接塌成"零摩擦"。

**三个命名空间不要混**（A′ 阶段抓到的一类真实缺陷）：

| 名字 | 出处 | 用途 |
|------|------|------|
| stage 目录名 | `stages/<name>/` | 看板渲染、**分支名**、`base_ref` |
| `ticket.identity.id` | `ticket.yaml` | **权威身份**：DAG 键、Instance、取卷、解锁判定 |
| `manifest.stages` | `track.yaml` | 看板**列表**，可以多声明 **board-only** 阶段（无 ticket、无分支，如环境入职阶段） |

平台启动时校验：所有被发现的 `stages/*/ticket.yaml` 都必须出现在 `manifest.stages` 里（允许反过来多声明），
且 ticket 映射的键必须等于 `identity.id`——任一不满足即**启动失败**，绝不"静默降级"。

### 8.2 防剧透清单

| 维度 | 做法（轻摩擦） | 明确不做 |
|------|--------------|---------|
| **后续 stage** | 工作区只 checkout 当前已解锁阶段的 ref；未解锁阶段是**独立 ref**，平台默认不拉 | 不做加密、不做服务端硬隔离 |
| **canonical 测试** | 不在考卷仓里，本地阅卷时运行时注入（§7.3） | 不做混淆 |
| **参考解 `<stage>-sol`** | UI **不 surfaced**；不在考卷仓里链接；要看得刻意去题库找 | 不删除、不隐藏（开源 + 过关后对照学习有价值） |
| **过关后对照** | 通过后**主动**展示参考解 +（可选）AI 讲解"你的解法 vs 参考解" | — |
| **board-only 阶段** | 看板显示为不可点的占位（`kind: board_only`）；**不会**去取卷 | 不为了"点了不报错"而伪造一张 ticket |

> 与 seckill-lab 现有哲学一致："强烈建议先自己做完再看；你面试时要能讲出自己的思路。" 平台把这句话**产品化**为：默认不递到眼前，但你想看、做完了看，都允许。

---

## 9. 数据模型

### 9.1 核心实体（平台服务 PostgreSQL）

```
User              部署者(=学员)/社区贡献者(作者)
Track             八股方向（题库 catalog；带 trust_level，§4.2/§13.2）
Stage             工单序列节点
Ticket            工单（ticket.yaml 的 DB 投影；带 ticket_version / test_version）
Enrollment        学员 × Track 的注册（含当前进度、已解锁集合）
TicketInstance    ★v0.3 新：一次"发卷"的版本冻结快照
                  = (learner, ticket_id, ticket_version, engine_version, test_version)
                  engine_version 现装平台版本（遗留命名，见 §3.1）
Submission        ★v0.3 改名（原"答卷"）：TicketInstance 内的一次 attempt = PR + diff + 答辩文本
GradingReport     阅卷结果（正确性/性能/并发/lint/越界告警 + provenance + runner_trust）= 不可信"证据声明"（§7.4）
Review            ★v0.3 定位变更：AI 评审 = 顾问性、非确定性（rubric 维度点评 + 锚定点评 + 建议），不直接置 passed
AcceptanceDecision ★v0.3 新：确定性、可审计的验收裁决
                  = f(测试硬门, AI 建议, 证据信任级, 配置[auto]) → pass/fail → 触发解锁
LearnerProfile    学习者画像（**未来切片**；将来落在平台自己的 Store，§10）
```

关系：
- `User 1—N Enrollment`
- `Enrollment 1—N TicketInstance`
- `Ticket 1—N TicketInstance`（同一工单：不同学员各一个；版本变更后同一学员再得一个新 Instance）
- `TicketInstance 1—N Submission`（**retry = 同 Instance 内新增 Submission**）
- `Submission 1—1 GradingReport`；`Submission 1—N Review`；`Submission 1—1 AcceptanceDecision`

```
Ticket(db-stage-01, v1)
  └─ TicketInstance #A {learner=deployer, ticket_version=1, engine_version=a-prime-0.1.0, test_version=1}
       ├─ Submission #1 → GradingReport(红) + Review(建议打回) → AcceptanceDecision(fail)
       └─ Submission #2 → GradingReport(绿) + Review(建议通过) → AcceptanceDecision(pass) → 解锁下游
```

> **为什么必须有 TicketInstance**：它把"一次考核所依赖的全部版本"冻结成一个不可变快照，从而在 schema 层（而非运行时扯皮）回答用户提的五个硬问题：

| 硬问题 | schema 层答案 |
|--------|--------------|
| 这个 PR 基于哪个 ticket 版本？ | `Submission → TicketInstance.ticket_version` |
| 这个 grading report 是哪个 test version 跑的？ | `GradingReport → TicketInstance.test_version`（+ report 自带 provenance） |
| 题目更新后，旧 submission 还有效吗？ | 旧 Instance 标 `stale`（保留不删）；新 `ticket_version` → re-issue 新 Instance（§9.2） |
| 平台升级后，旧 submission 用哪个平台版本？ | `TicketInstance.engine_version` 冻结（字段名遗留，装的是平台版本）；升级只影响新建 Instance |
| 同一 ticket retry 是重发卷还是续上一张？ | **续**：retry = 同 Instance 新 Submission；只有版本变更才 re-issue 新 Instance |

### 9.2 TicketInstance 生命周期状态机（实现即 §1.3）

```
locked → available → in_progress → submitted → grading
   → passed → (unlock dependents)
   → failed → in_progress（同一 Instance 内新 Submission，可多次 attempt）
```

- `available`：本 ticket 的 `progression.prerequisites`（§4.3）**全部 passed**（DAG 判定，非单一线性"上一张过了"）。
- `submitted`：PR 已开 + 答辩 + 阅卷报告（证据声明）已附。
- `grading → passed/failed`：由 **AcceptanceDecision**（确定性策略门，§11.3）裁决，**不是 AI Review 直接置位**——Review 只给建议。
- `passed` 触发解锁：所有把本 ticket 列为 prerequisite 的**下游 TicketInstance** 由 `locked → available`。
- **retry（同一 Instance）**：`ticket_version`/`engine_version`/`test_version` 全部不变，仅新增 Submission；每次 attempt 留痕不覆盖（画像可看"几次过"）。
- **re-issue（新建 Instance）**：当 `ticket_version` 或 `test_version` 或 `engine_version` 变更 → 旧 Instance 标 `stale`（**保留历史，不删**），新建 Instance 重新发卷。

### 9.3 ~~导师 session 状态机~~（v0.5 退休）

导师会话随引擎一并退休（§6）：平台**不再有第二种状态机**。整个平台只有一个状态机——`TicketInstance` 的 `InstanceState`（§1.3 / §9.2），由 `platform_service/lifecycle.py::LEGAL_TRANSITIONS` 定义迁移合法性。

---

## 10. 学习者画像与个性化（未来切片）

> **v0.5 状态：未实现。** v0.4 的设想是"把 prar-agent 的 pgvector 三层记忆换个用途"，该设想随引擎退休而失效。
> 画像本身**仍然要做**（它是个性化教学的载体），但改为**平台自己的后续切片**：数据落平台的 `Store`（PostgreSQL，当前是 `InMemoryStore`），不借道任何引擎。

**画像打算记什么**（语义沿用 v0.4，实现换成平台自己的表）：

| 层 | 记什么 | 例子 |
|----|--------|------|
| 经历（episodic） | 每次考核的结果与过程 | "2026-09-10 通过 db-stage-01，一次过，独立解决（没用 hints）" |
| 认知（semantic） | 反复薄弱的考点 | "**乐观锁 vs 悲观锁反复混淆**；EXPLAIN 解读薄弱" |
| 习惯（procedural） | 解题节奏与求助模式 | "习惯先写测试再调索引；卡住平均求助 2 次" |

**打算怎么用**：

- **个性化题面包装**：按画像调节背景名词解释的详略（**不降考点难度**）。
- **薄弱点重练**：从历史失败与求助记录里提炼反复错的考点 → 推送针对性变体工单。
- **间隔重复**：八股考点按遗忘曲线重现（配合 §12.1 的讲解）。
- **hints 用量进画像**："独立解决" vs "靠提示" 区分，供自我复盘（**不参与排名 / 得分**）。

**数据源现在就在库里（A′ 已具备）**：`TicketInstance`（`state` / 创建时间）、每次 `Submission`（`seq` 即 attempt 次数）、
`GradingReport` 的测试明细与 `out_of_scope_edits`、`AcceptanceDecision` 的输入快照，以及学员在 `head_ref` 上的 git 提交历史（§11.4）。
——**缺的只是一张投影表和一个消费它的界面**。

### 10.1 个性化的不变量 vs 可变项（防止画像把考试"调水"）

画像驱动个性化有个隐患：若它能改"考什么/判分线在哪"，就会**悄悄把难度调到学员舒适区**，让刷题失去意义。所以划死一条边界：

| 维度 | 不变量（invariant，个性化**绝不能碰**） | 可变项（variable，画像可调节） |
|------|--------------------------------------|------------------------------|
| 判据 | `grader` 判据、canonical 测试、通过门槛 | — |
| 难度 | ticket 核心考点与判分标准 | — |
| 题面包装 | — | 背景名词解释的详略、术语展开程度 |
| 求助路由 | — | hints 的推送时机 / 条数 |
| 复习节奏 | — | 间隔重复的周期、薄弱点变体的推送顺序 |

> **原则**：个性化只改"**怎么教 / 怎么包装 / 怎么排复习**"，绝不改"**考什么 / 判分线在哪**"。不变量由 §4.3 `grader` + canonical 测试钉死、随 TicketInstance 版本冻结；可变项都落在发卷渲染层与画像层，碰不到判据。这样既个性化，又保住刷题的真实有效性。

---

## 11. 评审与答辩（PR 即答辩）

### 11.1 答辩模板（强制，承继 seckill-lab "做完之后三件套"）

PR 描述模板由 `ticket.yaml.submission.template` 生成，学员必填：
1. 改后的关键实现（如 searchProducts SQL）；
2. 设计决策 + 一句话理由（如索引列序为什么这么排）；
3. 客观证据（建索引前后 EXPLAIN 对比 / sim 前后超卖数）——由阅卷报告自动附。

### 11.2 AI 评审（可选挂件 · 顾问性 · 非确定性）

**形态（v0.5）**：AI 评审不是平台的一部分，而是一个**可选挂件**，经 `platform_service/ai_review.py::ReviewEngine` Protocol 注入。
A′ 装的是 `StubReviewEngine`（诚实占位：按 `scenario` 返回 approve / request_changes 两套**预置**响应，正文自己写着"模拟评审结论"，**不读任何输入**）；
真实实现（任意轻量 LLM harness）是 spec §2 后续切片表。**挂件缺席时闭环照走**——这正是 D4 要的（§11.3 的裁决不依赖它）。

AI 评审产出**建议 + 锚定点评**，写进 `Review` 实体；它**不直接置 passed**——非确定性的 LLM 输出不能直接驱动确定性的状态机解锁（见 §11.3）。

| 维度（定性，不计分） | 评什么 |
|------|--------|
| **思路质量** | 答辩里的设计理由对不对、是否真懂（AI 重点评这个，呼应"面试要讲出自己思路"） |
| 代码质量 | 是否符合 track 约定（seckill-lab `docs/架构与约定.md`：分层/命名/MyBatis SQL 约定） |
| 证据完整度 | EXPLAIN/sim 证据是否齐、是否佐证结论 |
| 独立性 | hints 用量、attempt 次数（来自画像） |
| 越界告警 | `out_of_scope_edits` 非空（改了白名单外的文件）→ 标红，提示 AcceptanceDecision 重点核查。**注意这不是硬门**：记笔记、加自己的辅助文件都算越界，但都不该拦人。真正的硬门是"动了判定面"，归 §11.3 的 `tampering` |

输出：`recommendation ∈ {approve, request_changes}` + 每条 rubric 的锚定点评。锚点由平台自己维护为三元组 `(path, line, side)`（`models.ReviewComment`），`anchor` 只作展示、**从不回解析**。**注意：正确性红绿不在此表**——它是确定性硬门，归 §11.3，不交给 AI 自由裁量。

### 11.3 AcceptanceDecision（确定性、可审计的验收门）

把"是否通过"从 AI 手里拿出来，交给一个**确定性策略函数**，产出可审计的 AcceptanceDecision 记录（§9.1）：

```
AcceptanceDecision = f(
    correctness_gate,       # 硬门：grading-report.correctness.passed（按 runner_trust 决定采信度，§7.4）
    review_recommendation,  # AI Review 的建议（approve / request_changes），非确定性被隔离在此单一输入
    evidence_trust,         # 证据信任级 self|verified|community（§7.4 / §13.2）
    tampering,              # 学员改动的**判定面**文件（runtime.protected_paths ∩ files_changed，§7.3）
    config                  # auto（自习场景唯一模式）
) → pass | fail
```

| 模式 | 谁/什么拍板 | 适用 |
|------|-----------|------|
| **auto**（唯一） | correctness 硬门绿 + review=approve + 证据信任级达标 + **判定面未被改动** → 自动 pass | 部署者自习 |

- **确定性**：相同输入永远得到相同 pass/fail，可重放、可审计；AI 的非确定性被关进 `review_recommendation` 这一个输入。
- **解耦**：危险的 `AI output → passed → unlock` 耦合被切断，改为 `AI output → recommendation →（连同硬门/证据信任/判定面/配置）→ AcceptanceDecision → unlock`。
- **`tampering` 是硬输入，不是评分**：它来自**真 `git diff`**（§7.4）与 Track 声明的判定面**求交**，非空即 `fail`——**哪怕报告全绿**。理由见 §7.3：canonical 测试此刻就躺在学员的工作区里，"把测试改成永远绿"必须不能算过。它与 AI 评审无关，因此这条门在挂件缺席时照样成立。
- **留痕**：每次裁决写一条 AcceptanceDecision（输入快照 + 结果 + 时间 + 裁决者[auto]），re-issue 或申诉时可回溯；`tampering` 里**点名**是哪几个文件，学员不用猜自己为什么"绿了还不过"。

> 这是 seckill-lab 原本"人决策验收"的延续：自习场景把"人"换成"确定性策略门 + 客观测试 + AI 建议"，部署者本人无需第三方终审。
>
> **D4 的含义在这一节最具体**：`review_recommendation` 是一个**输入**，不是一个主人。挂件缺席时，A′ 用确定性占位评审填这一格，裁决与解锁**照常发生**；反之，再强的模型也只能给建议，越不过 `correctness` 硬门。

### 11.4 过程回放评审（用 git 历史，增强"真懂"判定）

**v0.5 变更**：v0.4 设想借 prar-agent 的每步 commit 做过程留痕；退休引擎后这一能力**天然还在**——
学员本来就在**自己的开发分支**上写代码、自己 commit，**git 历史就是过程证据**，平台只需按需读取（§8.1 的 `head_ref`）。
不引入引擎，反而少了一层"引擎替我提交"的间接性。

- **过程证据 → 喂给 §11.2 思路质量维度**：即使 correctness 硬门已绿，若过程显示"反复试错撞对""抄了又删""改完讲不出理由"，评审仍可返回 `request_changes`，经 §11.3 拦下"蒙对"。
- **绿 ≠ 懂**：把"结果对"与"过程真懂"分开评，正是面试要考的能力——这也是过程回放相对单看 PR diff 的增量价值。
- **状态**：A′ 已实现 diff 生成与体积预检（`review_diff.py`）；把提交历史整理成"过程视图"是后续切片。

---

## 12. 过关后：八股讲解 +（可选）AI 口试

### 12.1 八股讲解

过关 → 展示 `bagu-map.md` 对应章节（如 Stage1 "索引 & 执行计划"）+ 参考解 `<stage>-sol` 对照 +（可选挂件）串讲。
锚点由 `ticket.yaml.submission.bagu_map_ref` 给出；准入门会检查它指向的章节**真的存在**（§4.6）。

### 12.2 AI 口试（可选挂件，直击面试目标）

**v0.5 状态：未实现**，设计降为**可选挂件**——与 §11.2 共用同一个 `ReviewEngine` 缝，不新增架构。

- 过关后由挂件发起一轮八股口试："为什么范围列之后的索引列会失效？""Using filesort 说明什么？"
- 口试表现回写画像（§10）→ 影响薄弱点重练与间隔重复。
- 这是把"做对题"升级为"讲得出来"，正中面试痛点；**缺席时只是少了一层练习**，不影响闭环（D4）。

---

## 13. 安全 / 滥用 / 信任边界

### 13.1 学员侧信任边界（D2 君子协定）

| 我们**防** | 我们**不防**（D2 君子协定） |
|-----------|---------------------------|
| 阅卷信号被顺手篡改（canonical 测试运行时注入、`out_of_scope_edits` 告警） | 铁了心的学员深挖题库找 `<stage>-sol` / 伪造阅卷报告 |
| 越界改测试 / pom / fixture（`files_to_change` 白名单 + 告警） | 学员自己拿外部 AI 代写（无法在本地设备防住，靠答辩 + 可选口试暴露） |
| 未解锁阶段的剧透（一阶段一 ref + UI 不 surfaced，§8.1） | — |
| ~~导师代写答案~~（**已结构性消解**：平台里没有会写业务代码的组件，§1.2） | — |

**信任模型**：开源自托管 + 本地阅卷 ⇒ **没有中心化可信计算**。平台提供的是"**让认真学习阻力最小、让偷懒在答辩时暴露**"的机制，而非密码学级防作弊。自习定位下不设认证模式 / 老师终审——红绿始终是自报声明，靠荣誉制与答辩交叉暴露兜底。

**LLM / 算力自带**：AI 评审 / 口试是**可选挂件**，由部署方提供 LLM key 或本地模型；平台不分发、不代付、不托管密钥，**挂件缺席时平台功能完整**（D4）。

### 13.2 track 供应链信任模型（社区贡献的真正风险）

> **认知校正**：平台最大的潜在安全问题**不是学员作弊，而是"社区 track"**。学员作弊只坑自己（君子协定，§13.1）；一个恶意/被投毒的 track 会在**每个拉取它的学员设备上执行任意代码**——`runtime.compose` 起容器、`runtime.build` 跑脚本、canonical 测试本身就是可执行 Java。track 本质是**供应链包**，必须按供应链安全治理，不能按"内容数据"治理。

**信任分级（一等公民，写进 `track.yaml.trust_level`）**：

| trust_level | 含义 | 默认待遇 |
|-------------|------|---------|
| `self` | 部署方自己写 / 自托管的 track | 默认信任，直接跑 |
| `verified` | 维护者签名背书（`signed_by`）+ digest 锁定 + 过 CI 准入 | 信任，但展示来源与签名 |
| `community` | 第三方 PR 进 catalog | **默认不完全信任**：拉取前显著披露、首跑前需用户显式确认 |

**主防线（按有效性排序，前四条是主力）**：
1. **披露**——拉取 community track 前明确告知"这将执行第三方代码"，列出 runtime 入口；
2. **评审**——`admission.validate_track`（CLI / CI 接线待做）在**隔离环境**跑通骨架 / 测试 / 判别性，维护者人审 runtime；
3. **digest 锁定**——`runtime.digest_pin` 钉死容器镜像/依赖 digest，防 merge 后偷偷漂移；
4. **声誉**——maintainer 署名、commit 历史、catalog 声誉，让投毒有代价。

**纵深防御（不是硬保证）**：sandbox（rootless Docker、无 host 挂载、egress allowlist 只放 LLM/Gitea）能**抬高**攻击成本，但——

> **诚实边界**：阅卷发生在**学员自己拥有的设备**上，平台无法控制其内核/权限/Docker 配置。一个有 root 权限、能改 compose、能装内核模块的学员机器，**没有任何本地 sandbox 能给出密码学级的隔离保证**。所以我们不把 sandbox 当银弹宣传，而是：信任分级 + 披露 + digest 锁定 + 评审为主，sandbox 为纵深防御的一层。

---

## 14. 分期路线图（开源教学项目导向）

| 阶段 | 目标 | 交付 | 状态 |
|------|------|------|------|
| **A′ 引擎无关切片** | 单 Track + 单机跑通完整闭环，**不依赖任何引擎** | `platform_api/`（纯 REST 控制面）+ `frontend/`（学习者 SPA）+ 三个 Protocol 缝与"诚实占位"桩 + 内置 demo Track；`content/schema/` + `admission.validate_track` | ✅ **已完成**（后端全量测试 + ruff 绿；前端单测 + 真浏览器 e2e 绿） |
| **P1 真实化** | 把三个桩换成真实现，接上真 Track | ✅ 真实阅卷（`DockerGradingRunner` + `--grading docker`，JDK17 + Maven）、✅ seckill-lab 补齐**一阶段一 ref**（§8.1）、✅ 报告可读（`GET /submissions/{id}/report`）、✅ **判定面硬门**（§11.3）；⬜ 真实 Forge（Gitea fork/PR，**待环境**）、⬜ PostgreSQL `Store`、⬜ canonical 测试运行时注入（§7.3） | 🔶 **进行中** |
| **P2 教学增强** | 让"刷题"更像"带教" | 学习者画像（§10）、过程回放视图（§11.4）、（可选）AI 评审 / 口试挂件（§11.2 / §12.2） | ⬜ |
| **P3 社区可贡献**（开源命脉） | 让别人也能出题 | `paper new-track` 脚手架生成器、`paper validate-track` CLI + CI 接线、贡献工作流与文档；第二个 Track（JVM / 并发）打样 | ⬜ |

> **务实建议**：先把**数据库八股一个 Track** 真正跑顺（A′ 已证明软件不是瓶颈），再谈内容扩张。**内容生产是长期瓶颈，不是软件。**

---

## 15. 风险与不在范围

**风险**：
1. **内容生产是真正瓶颈**：做一个 seckill-lab 质量的 Track 工作量极大；脚手架生成器只能降边际成本，不能替代人工打磨。
2. **本地阅卷天然可伪造**：荣誉制下接受，靠答辩（+ 可选口试）交叉暴露兜底；自习定位下不设认证模式 / 老师终审。
3. **并发 sim 阅卷 flaky**：重试 + 置信度，不追求 100% 确定。
4. **平台与 Track 的契约漂移**：平台依赖 Track 侧的目录 / 分支 / 字段约定（§8.1）。约定一旦漂移，最坏的表现不是报错，而是**解锁判定与 checkout 到的内容悄悄错位**。缓解：启动期硬校验（ticket 目录覆盖 `manifest.stages`、ticket 映射键 = `identity.id`、分支 ref 可解析），任一不满足即**拒绝启动**或返回可读短码，绝不静默降级。
5. **桩会给人"已经做完了"的错觉**：阅卷已经真了，但 **Forge 仍是占位**（clone 是真的、PR URL 是编的），而 `Workspace.simulated` 与 `provenance.host_env.simulated` **说的不是同一件事**。缓解：两者分开标注，别用一个"模拟"徽标盖住；接口与 UI 都要**逐项**说明真假。
6. **阅卷的"红"依赖干净的基础设施状态（实测教训，不是推演）**：Track 的验收测试用 `@Sql` 跑迁移脚本，而**参考解那次运行留下的索引留在了共享 MySQL 数据卷里**；于是在同一环境里再跑骨架，`explainUsesIndex` **假绿**（只剩一条 BindingException 红），判别性悄悄消失。把索引删掉后，骨架两条全红、参考解两条全绿——即 **"骨架红 / 参考解绿"只在干净环境里成立**。缓解：① 判别性校验（准入门）必须在**一次性环境**里跑，不能复用长活数据卷；② 真阅卷若要严格，应在每次阅卷前重置 Track 基础设施——当前 `DockerGradingRunner` **不做**这件事，只在 `provenance` 里如实记录环境。

**不在范围**：
- 班级管理 / 排行榜 / 数值化得分 / 参数化试卷 / 认证模式 runner / 老师终审（v0.4 收缩：平台只服务部署者本人自习刷题，不含多用户教学治理）。
- 中心化云托管考场 / 多租户 SaaS 计费（D3 反向）。
- 密码学级防作弊（D2 反向）。
- 移动端 / 实时多人协作编辑。
- 自动大规模生产 Track 内容（AI 起草 + 人工验收，不做全自动）。
- **任何形式的 AI 执行者**（D1 反向：平台里不存在会写业务代码的组件，§1.2）。

---

## 附录 A · 架构亮点（可对外讲述 / 面试）

1. **角色反转，而且是结构性的**：平台里**没有 Executor 这个座位**——不是"禁用了写代码的工具"，而是**没有任何会写业务代码的组件**。D1 因此不需要 guardrail 去守，它是状态机的形状（§1.2 / §1.3）。
2. **零中心算力的开源自托管**：阅卷在学员设备、git 自托管、**AI 可选且自带 key** ⇒ 平台中心零云成本、零强制 LLM 成本，信任模型自洽（D3 / §2.2）。
3. **引擎无关的依赖面（D4）**：整个闭环只依赖 **Track 内容 + git + 本地工具链**；AI 只作为**建议性挂件**接在 `ReviewEngine` 缝上，缺席时闭环照走。相比 v0.4"复用引擎内核"的方案，少一个快速演进的上游、少一套契约冻结流程、少一条许可边界纪律（§6）。
4. **建议与确定性门分离**：`AI 建议 → recommendation →（连同硬门 / 证据信任级 / 配置）→ AcceptanceDecision → 解锁`。非确定性被关进**一个输入**，危险的 `AI → passed → unlock` 耦合被切断（§11.3）。
5. **内容标准化 + 准入校验门**：把"测试判别性（骨架红 / 参考解绿）"做成硬门，是开源内容生态能否规模化的关键工程；A′ 另加了身份与发现的硬校验（§4.6 / §8.1）。
6. **一阶段一 ref 的防剧透**：把"进度"建模成**独立 git ref**，让防剧透与解锁判定**共用同一个机制**——不是加一层权限开关，而是让未解锁的内容**根本不在工作区里**（§8.1）。
7. **三个命名空间的纪律**：stage 目录名 / `identity.id` / `manifest.stages` 各司其职，并在启动期硬校验。这类"看着是命名小事、实则是 DAG 与分支错位"的缺陷，是 A′ 阶段**真实抓到过**的一类 bug——把它变成契约，比写一句注释可靠（§8.1）。

## 附录 B · 与 Track 仓的对接清单（seckill-lab）

| Track 侧现状 | 对接点 / 状态 |
|-------------|-------------|
| `track.yaml`（`manifest` / `runtime` / `trust_level`） | 已按 §4.2 落地，可通过 `admission.validate_track` |
| `stages/stage-01-product-query-index/ticket.yaml` | 已按 §4.3 落地，是**权威契约**；`TASKS/*.md` 退为给人看的叙述视图 |
| `TASKS/stage-NN.md` | 叙述视图（不再驱动平台）；平台只读 `stages/*/ticket.yaml`（§8.1） |
| `src/test/.../ProductQueryIndexAcceptanceTest.java` | canonical 测试（§7.3 运行时注入；**当前仍留在仓内**，迁出为待办） |
| `src/test/.../sim/SeckillLoadSimulator.java` | 并发模拟器（§7.5） |
| `docker/`（MySQL 13306 + Redis 16379） | 学员本地 runtime（§7.1） |
| `docs/数据库八股地图.md` | `bagu_map` 讲解锚点（§12.1） |
| `docs/架构与约定.md` | 代码质量评审依据（§11.2）+ 脚手架模板（§4.5） |
| `solutions` 单分支 | ⚠ **待改造**：应改为**一阶段一 ref**（`<stage>` / `<stage>-sol`，§8.1）。当前仓里只有 `main` + `solutions`，所以 `provision` 会如实返回短码 `workspace_ref_missing` 并列出实际可用的 ref——这是平台**预期内**的行为，不是 bug |
| `stages/stage-00-onboarding` | **board-only** 阶段：无 `stages/` 目录、无 ticket、无分支；`manifest.stages` 列出即可，看板显示为不可点占位（§8.1） |

## 附录 C · 开放问题（待评审拍板）

1. ~~**平台服务与 prar-agent 的进程关系**~~ ✅ **已决（v0.5）**：引擎整体退休——"取码 / 交互"两轴均不再适用，见 §6 决策记录。
2. ~~**paper-cli 形态**~~ ✅ **已决（v0.5）**：Web（`platform_api/` REST + `frontend/` SPA）；CLI 降级为**可选未来项**，"命令面即端点表"（§7.2）。
3. ~~**认证模式的签名强度**~~ ✅ **已决（v0.4）**：删除认证模式与报告签名——自习平台纯荣誉制不签，保留 `runner_trust`（恒为 `self`）作"本地自报"诚实标记；`signed_by` 仅用于 verified track 的供应链背书（§4.2/§4.6），与阅卷报告无关。
4. **第二个打样 Track 选哪个**：JVM / 并发 / 网络？（P3 验证脚手架通用性）
5. **题库与平台是否同仓**：monorepo（content/ 与 platform/ 同仓）还是分仓（利于社区只 fork 题库）？倾向**分仓**。本问只决定**内容仓**（seckill-lab 类）相对 platform 的位置，与引擎耦合方式无关——引擎已退休，不再有 submodule 问题。
6. ~~**平台自身许可**~~ ✅ **已决（2026-09-05）= Apache-2.0**：执行的是 `seckill-lab`（内容 MIT）+ 本仓（平台 Apache-2.0）**双层拓扑**。v0.4 曾有的引擎 GPL 层随引擎退休而消解（§6.2），那套"进程外边界降低 copyleft 风险"的论证不再需要。仓根已放 `LICENSE` + `NOTICE`。

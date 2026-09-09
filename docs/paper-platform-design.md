# 发卷平台 (Paper Platform) · 总体设计稿

> **一句话**：一个**开源、可自托管**的"八股实战演练所"，供部署者（也是唯一用户）自习、刷题。AI 当经理/导师（从 seckill-lab 这类内容里**发需求、review、点评**），部署者当程序员（**亲手写代码**），在**自己的设备**上跑 canonical 测试完成**本地阅卷**，类 GitHub 地 fork → 开发 → 提 PR → 过关 → 解锁下一张工单。
>
> **定位**：供部署者（=用户）自习/刷题的**交互式演练所**，是 seckill-lab 的延伸；接受社区贡献（提交新 Track、扩展八股面）。**不含班级 / 排名 / 得分**，不是多租户 SaaS。
>
> **状态**：总体设计稿 v0.4（2026-09-07），实现期。
>
> **变更记录**：
> - **v0.2** — 落定三层许可拓扑与 GPL 边界（§6.7），prar-agent 打 `v0.1.0` tag、补 GPL-3.0；seckill-lab=MIT。
> - **v0.3** — 据评审补强五处：① **评估版本钉 / 数据模型**：新增 `TicketInstance`（learner×ticket×engine×test×seed 版本钉）与 `AcceptanceDecision`（确定性验收门），重连实体关系（§9）。② **证据信任 + 评审/验收分离**：本地阅卷报告定性为**不可信 claim**（带 provenance），AI Review（咨询、非确定）与 AcceptanceDecision（确定、可审计）解耦（§7.4/§11）。③ **社区 track 供应链信任与安全模型**：track.yaml 拆 `manifest`(inert)/`runtime`(可执行)，引入 `trust_level` 一等概念 + 披露/digest 钉/sandbox 纵深防御（§4.2/§4.6/§13）。④ **prar-agent 契约面分级**：区分 v0.1.0 真稳定 API 与 planned API（§6.6）。⑤ **ticket schema**：`unlock_next`→`prerequisites` DAG、按 concern 命名空间分组（§4.3）。⑥ **许可措辞降调**为工程意图而非法律定论（§6.7/附录 A）。
> - **v0.4** — 据主人指示收缩范围 + 重定位：平台定位为**供部署者（=用户）自习/刷题的交互式演练所**（seckill-lab 延伸、接受社区 Track 贡献），删除班级/排名/得分相关冗余——**删** 参数化试卷（原 §12.3）、排行榜（原 §12.4）、认证模式 runner（原 §7.6）、rubric 权重判分（§11.2）、teacher-confirm 老师终审（§11.3）、`fixture_seed` 版本钉（§9.1/§9.2）与 `parametric` schema 命名空间（§4.3）；§2.3 部署拓扑三档→两档。**保留** AI Review 建议（approve/request_changes）+ AcceptanceDecision 确定性硬门（解锁不塌陷）+ `runner_trust`（恒 `self` 的诚实标记）+ 学习者画像（§10，剥排名耦合）+ AI 口试（§12.2）。

---

## 0. 三条已拍板的设计前提

本设计严格遵循以下三条决策，全文不再回退：

| # | 决策 | 对设计的硬约束 |
|---|------|---------------|
| **D1** | **角色反转**：AI 不代写。AI 从 seckill-lab 内容里**提出需求**，学员完成，AI **review + 点评** | AI 永远是 Manager/Mentor/Reviewer，**绝不是 Executor**。prar-agent 的写代码动作空间在本平台不启用 |
| **D2** | **fork 防剧透走君子协定**：让答案/后续进度"不那么方便"被看到即可，基于"用户主动参与"，防剧透**可少做甚至不做** | 只加**轻摩擦**（不 surfaced、需刻意操作），**不建硬墙**，不为藏答案牺牲架构简洁 |
| **D3** | **阅卷用使用方自己的算力**，不用平台云 | **本地优先阅卷**：paper-cli 在学员设备跑 canonical 测试；平台中心**零阅卷算力**。LLM 推理也由部署方自带 key/本地模型 |

补充承继的前提（来自上一轮）：**自建 Gitea + 自有 runner** 承载 git/fork/PR；**内容标准化 + track 脚手架生成器是开源社区能否贡献的命脉**，列为一等公民。

> **D2 与 D3 的协同**：既然阅卷在学员本机、防剧透走荣誉制，整个平台就**不需要中心化的可信计算**——这让"开源自托管"在成本和信任模型上都成立。代价是：平台**不提供"防铁了心作弊"的强保证**，详见 §13。

---

## 1. 核心洞察：Manager ↔ Human-Executor 角色反转

这是整个平台的架构支点，也是它区别于"给 agent 套壳"的关键。

### 1.1 prar-agent 原本的角色

```
Manager(Planner LLM)  →  生成计划
User                  →  批注 / 答决策题 / 批准
Programmer(Executor LLM) →  调工具(shell/fs.write)动手执行
User                  →  review 动作结果
```

问题：Executor LLM 会**把八股答案直接写出来**，与 seckill-lab "你亲手写代码、面试要讲出自己思路" 的教学哲学**正面冲突**。

### 1.2 发卷平台的反转

**把 ACTING 的执行者从「LLM」换成「人」，把 ACTION_REVIEW 的评审者从「人」换成「AI」。** Manager 与整套循环机制原样保留。

```
AI 经理(Planner)   →  从 ticket 模板生成「工单/需求」计划（个性化）
学员               →  读工单 / 提问(comment) / 接卷(批准)
学员(人类程序员)   →  在沙箱/IDE 亲手写代码   ← 执行者反转：LLM ⇒ 人
AI 导师            →  仅在学员求助时给思路/hints（绝不代写）
AI 评审(Reviewer)  →  读 PR + 答辩 + 阅卷报告，锚定 diff 点评、给建议  ← 评审者反转：人 ⇒ AI
```

### 1.3 状态机逐阶段映射（prar-agent 6 阶段 → 平台语义）

| prar-agent 阶段 | 原本（LLM 双角色） | **发卷平台（角色反转）** | 算力来源 |
|----------------|-------------------|------------------------|---------|
| `INIT` | 用户提需求 | 平台按 enrollment + 学习者画像**触发发卷** | 平台 |
| `PLANNING` | Manager LLM 生成 plan | **AI 经理**从 `ticket.yaml` 生成个性化工单 plan（含决策题/名词解释/锚点，**不含答案**） | 部署方 LLM |
| `PLAN_REVIEW` | 用户批注/答决策题/批准 | 学员读工单、提 comment、AI 修订；学员**「接卷」=批准** | 部署方 LLM |
| `ACTING` | **Executor LLM** 调工具写代码 | **学员（人类程序员）亲手写代码**；AI 导师仅在求助 sub-loop 里指导（**不代写**） | **学员设备** |
| `ACTION_REVIEW` | 用户 review 动作结果 | 学员提交 PR + 答辩 + 阅卷报告；**AI 评审**锚定 diff 点评、给出 approve/request_changes 建议 | 学员设备(阅卷) + 部署方 LLM(评审) |
| `DONE` | 完成 | stage 通过 → 讲八股 + AI 口试 → **解锁下一张** | 部署方 LLM |

> **为什么这个反转优雅**：它不是"给自治 agent 加约束防止它代写"，而是**从角色定义上就让人占据执行位**。导师不代写不再是"需要守护的红线"，而是"它本来就不在 Executor 座位上"。`fs.write`/`shell` 写业务代码的能力**根本不需要启用**——见 §6.4 guardrail。

---

## 2. 总体架构

### 2.1 组件视图

```
                         ┌─────────────────────────────────────────┐
                         │            题库 content/ (开源)            │
                         │  tracks/<track>/{track.yaml, stages/,     │
                         │  skeleton/, tests/(canonical), bagu-map,  │
                         │  hints/, param/(模板), solutions/}         │
                         └───────────────┬─────────────────────────┘
                                         │ 发卷时实例化「考卷」
                                         ▼
┌──────────────┐   fork/PR   ┌──────────────────────┐   编排/桥接   ┌──────────────────┐
│  自建 Gitea   │◄───────────►│   平台服务 (FastAPI)   │◄────────────►│  prar-agent 引擎   │
│ git/fork/PR  │             │ catalog/enrollment/   │  N 个 session │ (AI 经理/导师/评审) │
│ refs/webhook │             │ ticket-lifecycle/     │  多路复用      │ 单session不变,    │
└──────┬───────┘             │ unlock/review/gitea   │               │ mentor_mode 配置  │
       │ clone/push          └───────────┬──────────┘               └──────────────────┘
       │                                 │ 发考卷 / 收 PR+阅卷报告
       ▼                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     学员设备 (自带算力)  ·  paper-cli 本地考场客户端              │
│  enroll → up(docker MySQL+Redis) → dev(IDE) → mentor(连导师) →                 │
│  grade(运行时注入 canonical 测试,本地跑,出阅卷报告) → submit(PR+答辩+报告)        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 算力原则（落实 D3）

| 计算 | 谁出算力 | 说明 |
|------|---------|------|
| git 托管 / fork / PR | 部署方的 Gitea（一台小机器即可） | 不是"我们的云"，是**自托管** |
| **阅卷（跑 canonical 测试 / EXPLAIN / 并发 sim）** | **学员自己的设备** | docker compose 起 MySQL(13306)+Redis(16379)+app，本地 `mvn test` |
| LLM 推理（发卷/导师/评审/口试） | **部署方自带** API key 或本地模型 | 复用 prar-agent 的 LiteLLM router；hints/口试可路由到便宜/本地模型 |
| 平台编排（状态机/解锁/画像） | 部署方平台服务（轻量） | 无重计算 |

> **结论：平台中心零阅卷算力、零强制 LLM 成本。** 部署者（也是唯一用户）用一台机器 + 自己的 LLM key 就能 all-in-one 本地跑起一个完整演练所（§2.3）。

### 2.3 部署拓扑（两档）

| 档 | 形态 | 适用 |
|----|------|------|
| **单机 all-in-one** | docker-compose 一把起 Gitea + 平台 + prar-agent + paper-cli | 部署者自习 / 刷题（唯一运行档） |
| **社区中心** | 官方维护**题库**（开源）+ track 准入校验 CI；不托管运行，只发内容 | 开源生态：大家各自部署，共享 track |

---

## 3. 三层解耦：内容 / 引擎 / 平台

| 层 | 是什么 | 复用现状 | 本平台新增 |
|----|--------|---------|-----------|
| **内容层** | 题库：track/stage/ticket + 骨架 + canonical 测试 + 八股地图 + hints + 参考解 | seckill-lab = 第一个 track（数据库八股），已有 6 stage 设计、验收测试、并发 sim、八股地图 | 把 seckill-lab 的**隐式约定显式建模**为 schema；脚手架生成器；社区贡献工作流 |
| **引擎层** | AI 经理/导师/评审 + 沙箱 + git checkpoint + 记忆 | prar-agent（M1–M4 已完成，功能完整） | **不改内核**，只加 `mentor_mode` 配置 + 角色反转编排 |
| **平台层** | 多路复用编排 + 发卷/接卷 + fork/PR + 阅卷 + 解锁 + 画像 + 看板 | 无 | **全部新建**（本设计主体） |

### 3.1 复用 / 扩展 / 新建（prar-agent 模块级对照）

| prar-agent 模块 | 平台如何用 | 动作 |
|----------------|-----------|------|
| `core/state_machine.py`（6 阶段硬控） | 直接当**导师 session 状态机**，按 §1.3 重解释 ACTING/ACTION_REVIEW | 复用 + 重映射 |
| `core/plan_engine.py` + `llm/prompts/planner.md` | **发卷**：从 ticket 模板生成工单 plan | 复用 + 换 prompt |
| `core/critic.py` | 发卷前自检工单质量（决策题是否泄题、step 是否绑工具） | 复用 |
| `core/review_merger.py` | 学员 comment → 工单 plan 修订 | 复用 |
| `core/action_dispatcher.py` + ReAct | **导师求助 sub-loop**（只读/分析/跑测试，不写业务码） | 复用 + 限工具白名单 |
| `core/checkpoint.py`（pygit2，每 step 一 commit） | 学员解题**全过程留痕** → 过程回放评审（§11.4） | 复用 |
| `tools/registry.py` + `tools/sandbox.py` + `builtin/{shell,fs,http}` | 学员开发沙箱 + 阅卷执行隔离 | 复用 + **mentor_mode 白名单**（§6.4） |
| `memory/long_term.py`(pgvector 三层) + `consolidator.py` + LTM_RECALL | **改造为学习者画像**（§10），per `user_id` 天然多用户隔离 | 复用 + 换语义 |
| `llm/router.py`(LiteLLM) | hints/口试用便宜或本地模型，评审用强模型 → **成本分级** | 复用 |
| `api/ws.py` + 前端 Tiptap/Monaco | 工单 plan 渲染、comment 锚定、浏览器 IDE、**口试**载体 | 复用 |
| 局部 rerun(`rollback_to`/`start_from`/`POST /rerun`) | 学员"重做这一步"、并发 sim flaky 重试 | 复用 |
| —（无） | **多 session 编排**：每学员-每工单一个 prar-agent session | **新建** |

> **DESIGN.md §2/§9 的"单用户单 session"约束不被违反**：引擎仍单 session，**多路复用在平台层做**（§6.1）。这是尊重既有架构、不重写内核的关键。

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
    digest_pin:                            # 锁定可执行依赖 digest，防供应链漂移
      mysql: "sha256:..."
      redis: "sha256:..."

  contribute:
    license: MIT                 # 内容仓许可（与 §6.7 三层许可拓扑一致）
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

# ── brief：题面（AI 经理据此发卷）──
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

# ── mentor_policy：per-ticket 导师权限（§6.4），可覆盖全局；D1 角色反转的硬约束 ──
mentor_policy:
  mode: pure_mentor             # pure_mentor | mentor_then_reveal
  may_write: []                 # 绝不写 files_to_change

# ── progression：解锁依赖（v0.3 反转——声明"我依赖谁"，而非"我解锁谁"）──
progression:
  prerequisites: [db-stage-00]  # 单前置时退化为线性；多前置 = DAG（§9.2）
```

> **v0.3 结构变更（namespace 分组 + unlock_next→prerequisites + 版本锚点）**：
> ①**按 concern 分组**——identity/brief/scope/grader/hints/submission/mentor_policy/progression，平台渲染、验收、防越界、控解锁各取所需，互不串味；
> ②**`unlock_next` 反转为 `progression.prerequisites`**——工单不再"向前指"下一张（那样新增/重排工单要改一片），而是"向后指"自己依赖谁，整体构成 DAG，解锁判定 = "prerequisites 全 passed"（§9.2）；单前置时天然退化为线性，迁移零成本；
> ③**版本锚点**——`schema_version` / `ticket_version` / `grader.correctness.test_version` 与 `engine_version` 一起在发卷时钉进 `TicketInstance`（§9.1），从而能回答用户提的五个硬问题（"这 PR 基于哪个 ticket 版本？""grading report 是哪个 test version 跑的？""题目更新后旧 submission 还有效吗？""引擎更新后旧 session 用哪个 engine？""retry 是重新发卷还是续上一张？"）——全部在 schema 层解决，不留给运行时扯皮。


### 4.4 track 资产清单（贡献者必须提供）

| 资产 | 必需 | 说明 |
|------|:---:|------|
| `skeleton/` 骨架仓 | ✅ | 能编译、能 `docker compose up`、冒烟测试绿 |
| `stages/*/ticket.yaml` | ✅ | 工单（结构化） |
| `stages/*/tests/` canonical 测试 | ✅ | **判别性**：骨架上红、参考解上绿（准入校验门把关） |
| `bagu-map.md` 八股地图 | ✅ | 过关后讲解锚点 |
| `hints/` | ✅ | 阶梯提示 |
| `solutions/` 参考解 | ✅ | 仅用于准入校验 + 过关后对照；开源下不藏（§8） |

### 4.5 track 脚手架生成器 —— `paper new-track`（AI 辅助）

降低贡献门槛的关键工具。**Dogfood 角色反转**：用 prar-agent 的 AI 经理能力，帮内容作者"出卷"。

```
paper new-track --topic "JVM 八股" --concepts "GC,类加载,内存模型,JIT"
```

生成：
1. 骨架仓脚手架（build 配置、docker 基础设施、CI 配置、目录约定，仿 seckill-lab `docs/架构与约定.md`）；
2. `track.yaml` + 每个 stage 的 `ticket.yaml` 草稿（AI 经理起草"线上问题 + 考点 + files_to_change"）；
3. canonical 测试**桩**（AI 起草断言骨架，作者补全）；
4. `bagu-map.md` 骨架；
5. 一份**准入校验清单**（作者照单自测）。

> AI 在这里是"出题助手"，作者是"出题人"——人最终决策验收每张工单的质量，与教学闭环的哲学一致。

### 4.6 社区贡献工作流 + 准入校验门（最重要）

```
贡献者 fork 题库 → paper new-track 起草 → 补全工单/测试/八股地图
   → 本地跑 `paper validate-track` → 提 PR 到中心题库
   → 题库 CI 自动校验 → 维护者 review → merge → track 进 catalog
```

**准入校验门 `paper validate-track`（CI 强制）** —— 没有它，社区 track 必然良莠不齐：

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
| **trust_level 标注** | `community` track 必须显式标 `trust_level`；升 `verified` 需 `signed_by` 维护者签名 + digest 锁定 |

> **供应链告警（核心安全认知）**：community track 不是"数据"，而是**一组会在学员设备上执行任意代码的供应链包**——`runtime.compose` 能起任意容器、`runtime.build` 能跑任意脚本、canonical 测试本身就是可执行代码。所以 `validate-track` 不只校验"内容质量"，更要校验"runtime 入口被 digest 锁定、被 CI 在隔离环境跑过、trust_level 被如实标注"。完整威胁模型（信任分级、sandbox 纵深防御及其在学员机器上的**诚实边界**）见 §13。

---

## 5. 端到端流程

```
选方向 → 接工单 → 发卷 → 开发(导师陪练) → 本地阅卷 → 提交PR(=答辩)
       → AI评审+点评 → 过关 → 讲八股+口试 → 解锁下一张 → (循环)
```

| 步 | 学员看到/做 | 系统做 | 算力 |
|----|-----------|--------|------|
| **1 选方向** | 浏览 track catalog（数据库八股 / JVM / 并发…），看难度/考点 | 列已部署 track + 学员画像推荐 | 平台 |
| **2 接工单** | 看 stage 看板（🔒/🟥/✅，复用 seckill-lab TASKS 看板语义），点"接取" | 校验前置 stage 已过 → 创建 enrollment | 平台 |
| **3 发卷** | 收到一份**工单 plan**（Tiptap 渲染：线上问题/考点/决策题/名词解释/锚点） | AI 经理从 `ticket.yaml` + 画像生成个性化工单 | 部署方 LLM |
| **4 接卷** | 读工单、提 comment 问问题、答决策题；点"接卷" | review_merger 修订工单；批准后实例化**考卷 fork**（仅当前已解锁内容） | 部署方 LLM + Gitea |
| **5 开发** | `paper up` 起本地 MySQL/Redis；在 IDE/浏览器写代码；卡住 `paper mentor` 求助 | 导师 sub-loop 给思路/hints/跑 EXPLAIN，**不代写** | **学员设备** |
| **6 本地阅卷** | `paper grade` | paper-cli 运行时注入 canonical 测试 → 本地跑 → 出**结构化阅卷报告**（红绿+EXPLAIN+sim） | **学员设备** |
| **7 提交 PR** | `paper submit` → 填**答辩模板**（三件套）+ 附阅卷报告 → 开 PR | 在 Gitea 考卷仓建 PR | 学员设备 + Gitea |
| **8 AI 评审** | 收到锚定 diff 的**点评** + approve/request_changes 建议 | AI 评审读 PR diff + 答辩 + 阅卷报告，按 rubric 维度评（不只评红绿，评思路） | 部署方 LLM |
| **9 过关/打回** | 看是否通过；不通过则按点评改 → 回到 5 | 验收决策（§11.3）：AI 自动（auto） | 平台 |
| **10 讲八股+口试** | 过关后读八股地图对应章节；接受 AI **口试** | 决策题节点发起交互式八股口试，表现回写画像 | 部署方 LLM |
| **11 解锁** | 看板下一张变 🟥 | unlock_engine 推进 + 写入 episodic 记忆 | 平台 |

---

## 6. 角色反转下的 prar-agent 复用与改造

### 6.1 多 session 编排（平台层，不违反引擎单 session）

prar-agent `DESIGN.md §2` 锁死"单用户单 session"。一个部署者可能同时推进多张工单 → **平台层做 session 多路复用**：

- 每个 `(学员, 工单)` = 一个 prar-agent session（独立 sandbox / git workspace / 记忆 user_id）。
- `orchestrator/session_pool.py` 管理 session 生命周期（创建/挂起/恢复/回收），按部署规模限并发。
- 引擎本身**零改动**：它依旧认为自己在服务"单用户单 session"。
- 单部署者并发量级很小（个位数 session），进程级/容器级隔离足够，无需重写引擎为多租户。

### 6.2 PLANNING 改造：发卷

- 输入：`ticket.yaml`（problem/concepts/files_to_change/hints）+ LTM_RECALL（学员画像）。
- AI 经理产出工单 plan（ProseMirror JSON）：线上问题陈述、考点名词解释、**澄清型决策题**（关于需求边界，**不含解题决策**）、可评论锚点。
- `critic.py` 自检：**决策题不得泄题**（不能问"索引列序怎么排"——那是答案；可问"是否需要支持分页"这类需求澄清）。
- 个性化：画像显示学员"乐观锁反复混淆" → 工单补充背景名词解释，但**不降考点难度**。

### 6.3 ACTING 改造：人类开发期 + 导师 sub-loop

ACTING 阶段的"actor"是**人**。prar-agent 的 Executor LLM **不被调用**。学员求助时进入**导师 sub-loop**：

- 学员问："我建的索引为什么 EXPLAIN 还 filesort？"
- 导师用 `action_dispatcher` 调**只读/分析工具**：读学员的 SQL、跑 EXPLAIN、跑测试看红绿。
- 导师**苏格拉底式反问**："范围列 price 在联合索引里排在等值列前面还是后面？范围列之后的索引列会怎样？"——引导而非给答案。
- 全程不写 `files_to_change`。

### 6.4 导师 guardrail（D1：不代写）—— 三层保险

| 层 | 机制 | 实现 |
|----|------|------|
| **工具白名单** | mentor_mode 下 Tool registry 只放行只读/分析/测试类 | `fs.write`/`shell` 对 `files_to_change` 路径**禁用**；仅允许写 `scratch/`（临时验证脚本/演示） |
| **角色不调用** | ACTING 阶段根本不启动 Executor LLM | 状态机在 ACTING 等待**人类**提交，而非 LLM 工具循环 |
| **输出守护** | 导师回答前过 guard：命中"直接给完整答案"→ 改苏格拉底式反问 | prompt 约束 + 一道轻量 critic 检查 |

- **per-ticket 可覆盖**（`ticket.yaml.mentor_policy`）：Stage 0 入职可放宽（代写拉环境无害）；Stage 3 超卖锁严禁代写。
- **红线定义**：不是"导师不能写任何东西"，而是"**导师不写被 grader 评分的业务实现**"。演示 EXPLAIN、补 fixture、写临时压测脚本都允许（写进 `scratch/`，不进提交面）。

### 6.5 ACTION_REVIEW 改造：AI 评审（评审者反转）

- 复用 prar-agent 的 **comment 锚定**机制，但锚定对象从"plan 文档"换成"**PR diff**"——AI 评审在具体代码行上留点评（AnchorMark + fuzzy match 直接复用）。
- 输入：PR diff + 答辩文本 + 阅卷报告(evidence)。
- 输出：rubric 维度点评（§11.2）+ 锚定行级点评 + 一句话总评 + approve/request_changes 建议。

### 6.6 依赖与集成策略：prar-agent 作为 submodule + 适配层契约 ★（解决附录 C Q1）

**决策**：paper-platform 用 **git submodule 引入 prar-agent 源码**，但运行时**只通过 prar-agent 的稳定 HTTP/WS API 交互**，所有调用收敛到唯一适配模块 `orchestrator/prar_bridge.py`。

**两条正交的轴，别混为一谈**：

| 轴 | 问题 | 选项 | 本项目选择 |
|----|------|------|-----------|
| **A 取码** | 怎么把 prar-agent 代码弄进来 | submodule / subtree / 包 / 镜像 | **submodule**（单 clone、compose 从源码构建、贡献者可见全栈） |
| **B 交互** | 运行时怎么调它 | 进程内 import / 进程外 API | **进程外 HTTP/WS**（只绑稳定契约） |

> "prar-agent 更新时只维护接入接口"= **B 轴的解耦**。submodule（A 轴）本身**不提供**这个保证——它给的是源码耦合，你完全可以 `import prar_agent.core.*`，那样每次引擎内部重构都会炸到平台。解耦来自**纪律**：平台代码永不 import prar-agent 内部符号，只经 `prar_bridge` 打它的 REST/WS。

**纠正一个常见误解**：submodule **不会自动同步**。它钉住一个 commit SHA；prar-agent 更新后需显式 `git submodule update --remote` 再 commit 新指针。这是**特性不是缺陷**——保证"某次发卷用的引擎版本可复现"。

**让 B 轴解耦真正成立的四件事**：

1. **唯一适配层**：`orchestrator/prar_bridge.py` 是平台中**唯一**知道 prar-agent URL/endpoint/payload 的文件。其余平台代码只依赖 bridge 的抽象方法（`open_session/plan/tick_review/...`）。
2. **绑稳定 API，不绑内部**：prar-agent 已暴露 `api/{sessions,ws_plan,ws_act,comments,memories,providers,adapters}.py`（已核实）——这就是契约面。平台**禁止** import `core/*`、`llm/*`、`memory/*` 内部符号。
3. **钉 tag 而非裸 SHA**：prar-agent 已打 **`v0.1.0`**（公网可解析，已核实）；submodule 钉该 tag（记录其 commit SHA，detached at tag），升级 = 显式跳 tag（`make bump-submodule`）。
4. **契约测试守门**：一组针对 prar-agent API 的 contract test（respx/httpx 校验真实 endpoint 形状）。每次 submodule bump，CI 先跑契约测试，绿了才允许合并指针——把"引擎改坏了接口"挡在升级当下。

**契约面分级：v0.1.0 stable（现在就绑） vs planned（尚不存在，桥接层只占位）**：

| 端点 / 钩子 | 状态 | 桥接层处理 |
|------|------|-----------|
| `POST/GET /api/sessions`、`/{id}/plan(s)`、`/decisions/{id}`、`/advance-to-acting`、`/rerun`、`/complete`、`/merge` | **stable**（v0.1.0 已核实） | `prar_bridge` 直接绑真实形状 |
| `POST/GET /api/sessions/{id}/comments`（锚定点评） | **stable** | 直接绑（AI Review 锚定点评复用） |
| `/api/adapters`、`/api/providers`、`/api/memories`(含 search/consolidate) | **stable** | 直接绑 |
| `WS /api/ws/sessions/{id}/plan`（发卷/规划流） | **stable** | 绑（写 bridge 前先读 `ws_plan.py` 核实流事件名） |
| `WS /api/ws/sessions/{id}/act`（执行流） | **stable，但 D1 禁用** | **不桥接为业务码路径**——它跑的是 Executor LLM，桥过来即违反 D1 角色反转。human-actor 会话下引擎本就不启动它（§6.6.1） |
| `mentor_mode`（`actor="human"` + `workspace.tool_policy`） | **frozen v0.2.0**（tag 已打） | bump 后 `open_mentor_session` 解 `PENDING` 改绑真实形状（§6.6.1） |
| ACTING-without-Executor（human actor 的 ACTING） | **frozen v0.2.0** | 同上；`advance_to_acting_without_executor` 改绑。引擎结构上不启动 Executor |
| AI-PR-review 入口 `POST /api/sessions/{id}/reviews`（喂 diff+答辩+报告） | **frozen v0.2.0** | 同上；`submit_for_ai_review` 改绑（纯咨询、不移动状态机） |

> **桥接层铁律**：`prar_bridge` 只绑引擎**真实存在**的端点；未冻结的钩子一律标 `PENDING` 并 `raise`（契约测试守门），**永不发明** URL/payload。planned 项的实现工作在 prar-agent 侧（作为**版本化契约**冻结，写进它的 `docs/design/`），平台只消费契约、不碰实现。**更新（2026-09-09）：上表三项 planned 已冻结于引擎 v0.2.0（tag 已打）；消费约束与 bump 后待办见 §6.6.1。**

**贡献者摩擦与缓解（开源现实）**：

- 摩擦：`git clone --recursive` 易忘 → submodule 目录空；detached HEAD 易误提交；CI 需 `submodules: recursive`。
- 缓解：README 首屏放 `git clone --recursive` 一行；提供 `make bootstrap`（内部 `git submodule update --init --recursive`）；CI 模板内置 submodule 初始化；贡献指南明确"改 prar-agent 去它的仓提 PR，平台只升指针"。

**阻塞项（开源前提）**：

1. ~~**LICENSE 缺失 ×2**~~ ✅ **已解决（2026-09-05）**：prar-agent = **GPL-3.0-or-later**，seckill-lab = **MIT**（两仓 LICENSE + README License 段均已落地）。许可拓扑、GPL/平台边界与平台自身许可建议见 **§6.7**。
2. **prar-agent 可见性待确认**：`gh` 不可用，无法程序化确认 prar-agent 是 public 还是 private。**若 private，公开 paper-platform 无法 submodule 它**（贡献者拉不到）。需主人确认/转 public。

**与 Q5（题库与平台是否同仓）的关系**：本决策只钉 prar-agent ↔ platform 的取码方式；seckill-lab 类**内容仓**仍倾向**独立**（社区只 fork 题库、不背引擎），见附录 C Q5。

### 6.6.1 三项 planned 契约已冻结于引擎 v0.2.0 ★（2026-09-09 引擎交付）

引擎侧已实现并**冻结**上表三项原 planned 契约，版本 **prar-agent v0.2.0（tag 已打，commit `96e8375`）**。**权威契约面 = 引擎仓 `docs/CONTRACTS.md`**（bump 后即 `prar-agent/docs/CONTRACTS.md`，**按端点组织**：请求/响应形状、错误码矩阵、三档保证级别〔结构性 / 运行时门禁 / 尽力而为〕、§7「明确不存在的东西」负面清单）；设计论证在引擎仓 `docs/design/28-31`（28 human-actor、29 workspace-tool-policy、30 diff-review、31 contract-freeze）。**本节只记平台侧消费约束，形状一律以 CONTRACTS.md 为准、不在此复制。**

**平台将绑定的四个端点**（rebind 时映射到 bridge 方法）：

| 端点 | 作用 | bridge 方法 |
|------|------|------------|
| `POST /api/sessions`（新增 `actor`、`workspace` 字段） | 建 human-actor + external-workspace 会话 | `open_mentor_session` |
| `GET /api/sessions/{id}/workspace` | **自证端点**：断言 `effective_policy` / `rerun_available` / `shell_confinement` | （新增）核验白名单真生效 |
| `POST /api/sessions/{id}/submissions` | human 提交，驱动 `acting → action_review` | `advance_to_acting_without_executor` 一族 |
| `POST /api/sessions/{id}/reviews` | 纯咨询行级评审，不改 phase | `submit_for_ai_review` |

**边界不变量已满足**：三项契约的新增面**全部在 `api/` 层**，没有一处需要平台 `import app.core/llm/memory/tools`。引擎明确要求：**若发现必须引用内部符号才能实现某功能，当引擎缺陷报回，不要绕过**——`contracts/test_boundary.py` 应**一直是绿的**。

**bump + rebind 状态（2026-09-09：1/2/3/6 已完成；4/5 属下游集成，待相应平台流程落地）**：

1. ✅ submodule 已 bump 到 **v0.2.0**（commit `96e8375`；Makefile `PINNED_TAG` 同步）。前提已满足——引擎已把 tag 推到 GitHub `origin`；否则 submodule `git fetch --tags` 取不到，且把 gitlink pin 到**未推送**的 commit 会让贡献者 `git clone --recursive` 因 SHA 不可达而失败（违背 §6.6 开源模型）。
2. ✅ `contracts/test_prar_contract.py`：删 `test_pending_contracts_raise`，改为 respx **真实形状**断言（含 `shell_confinement` 枚举成员判断、review 响应无 `phase` 的咨询性断言、`submission_id` XOR `diff`）。
3. ✅ `orchestrator/prar_bridge.py`：`ENGINE_PINNED_TAG` 提到 `v0.2.0`；三处 `PENDING` 解绑为真实端点，并新增 `get_workspace`（§3.4 自证）与 `create_submission`（§4.2 human 提交）；`run_act_executor` 的 D1 拒绝不变。`ContractPendingError` 保留为**休眠**的纪律机制（当前无未冻结契约）。
4. ⬜ fork/实例化时把 `scratch/` 写进**学生仓** `.gitignore`（见契约一末条）——待平台 fork 流程落地。
5. ⬜ diff 生成用 **git 默认前缀** + **120k 体积预检**（见契约三）——待平台 diff 生成 / 集成层落地。
6. ✅ 全绿：契约测试 10 + 边界测试 18 + 全量 **110 passed**，ruff 干净。

#### 契约一：mentor_mode = 两把独立的锁

- 拆成 **`actor="human"`（结构性）** + **`workspace.tool_policy`（运行时门禁）** 两把**独立**的锁，引擎建议**同时用**。
- **部分拒绝一项（shell 无路径级限定）**：`shell_confinement` 是**枚举、非布尔**——`"not_enabled"`（缺省：shell 不在 external 白名单）/ `"none"`（平台显式放行 shell，但引擎**不做任何路径限定**）/ 未来加法 `"container_mount"`（Docker 只读挂载，ROADMAP M5+）。对 `sh -c` 做静态分析是**假的**——`cat scr""atch/../src/a.py` 就绕过；引擎不愿给一个能被绕过的保证。
  - **断言写法铁律**：把 `assert shell_confinement == "not_enabled"` 这类**等值判断**改成**枚举成员判断**（`in {...}`），否则未来加法 `"container_mount"` 会打破断言。届时 `GET /workspace` 形状不变（加法），契约测试不必重写。
- **external workspace 下引擎不做 `git init` / checkpoint / rerun**：checkpoint 会 `git add -A` + `reset --hard` + `clean -fd`，对引擎**不拥有**的仓库是**破坏性**的。
  - 用响应里的 **`rerun_available` 字段直接隐藏按钮**，**别去探 409**。
- **`scratch/` 加进学生仓 `.gitignore` 是平台/内容侧的活**（fork/实例化时做）——那个仓库**不归引擎所有**，本仓暂无骨架模板可挂，落地在 fork 流程里。

#### 契约二：ACTING 的 actor 是人类时，引擎结构上不启动 Executor

- `actor="human"` 时，引擎**结构上不可能**启动 Executor 工具循环（三处「先于」判断）。
- **副作用（是设计不是故障）**：`WS /act` 对 human 会话**永远报错**——平台流程**从头到尾不需要连它**（与 D1、§6.6 表 ws_act 行一致）。
- `actor` 与 `workspace` 现已注册进引擎 `shared/schema.json`（**34 个 `$defs`**），**P0 入口可机器核验**了。这个洞是本次冻结前才发现的——原因是 06 号设计当时假定的读者是前端。

#### 契约三：AI-PR-review 锚点坐标系（纯咨询）

- **纯咨询性**：响应里**没有 `phase` 字段**，`POST /{id}/reviews` **结构上无法移动状态机**。AcceptanceDecision 仍是确定性闸。
- **锚点坐标系冻结为 `(path, line, side)`**：`line` 是**文件行号**（由 `@@ -a,b +c,d @@` 推出），`side ∈ {left, right}`；`anchor` 是**派生字符串**——**自己从三元组拼展示格式，不要解析 `anchor`**。
- **diff 生成铁律**：用 **git 默认前缀**，**不要传 `--src-prefix=` `--dst-prefix=`**。引擎设计草稿一开始写反了，用真 git diff 校准时才发现 **noprefix 会引入无法消除的歧义**。
- **实测结论（n=2，量级参考非统计结论）**：锚点命中率 **1.000（5/5，零降级）**，但**行号精度不高**——只有 1/5 语义精确，其余 4 条在同一 hunk 内偏 1-4 行。因此：
  - **(a) 平台 UI 不要把锚点行渲染成「就是这一行错了」，请展示 hunk 上下文。**
  - **(b) `quote` 的契约是「锚点行的原文」而非「点评所讨论的那一行」**——实测有一条评论 `quote` 是 `try:`，正文却在讨论上一行的 `cast`。
  - **确定性校验才是保证**：命中则用真实行文本**覆写 `quote`**，未命中则**降级为 unanchored 并清空五个锚点字段**；prompt 只是提升手段。
- **两个边界情况（体积）**：
  - diff 超过 **120,000 字符**时**按整文件截断**（绝不截半个文件）；**`truncated_files` 由引擎覆写，是平台唯一的不完整性信号**。
  - **首个文件就超限时 diff 实际为空，但评审仍返回一个看起来正常的结果**——平台必须**自己预检体积**。

### 6.7 许可拓扑与 GPL 边界 ★（2026-09-05 落地）

三层各自独立许可，且与 §6.6 的**进程外架构互为表里**：

| 层 | 仓 | 许可 | 性质 |
|----|----|------|------|
| 引擎 | prar-agent | **GPL-3.0-or-later** | 强 copyleft：改动/再分发引擎必须同样 GPL，社区改进持续回流 |
| 内容 | seckill-lab（及后续题库） | **MIT** | 宽松：作为**社区贡献题库**最低摩擦被 fork/改编，经 §4 标准工作流接入 |
| 平台 | paper-platform | **Apache-2.0**（已决 2026-09-05，见附录 C Q6） | 胶水层：宽松 + 专利授权，欢迎社区贡献平台代码 |

**为什么这套组合自洽**：

1. **MIT → 任意许可 单向兼容**：MIT 内容可被 GPL/Apache/专有项目自由消费。seckill-lab(MIT) 流入平台无冲突，**无论平台选什么许可**。✓
2. **GPL 引擎 + 进程外边界 ⇒ 降低（而非自动消除）平台被 GPL 传染的风险**：GPL-3.0 的 copyleft 附着于与 GPL 代码**链接/合并成单一程序**的衍生作品。§6.6 规定平台**只经 HTTP/WS 调引擎、永不 import 引擎 Python 模块**——该架构**旨在**把平台与引擎保持为**独立程序**、**降低形成 GPL 衍生作品的风险**，从而为平台保留自选许可的空间；分发时仍需**保留引擎的 GPL-3.0 与版权**（submodule 天然带着 `prar-agent/LICENSE`，别删即可）。**但"独立程序 / mere aggregation"的最终定性不能仅由"走 HTTP"自动推出**——它取决于实际分发结构（是否打包成单一产物、是否构成一个程序的整体）与适用法律判断。
3. **同一条边界的两份红利（工程确定，法律需复核）**：§6.6 的"禁止 import 内部"纪律，**工程上**确定给你"引擎升级只动适配层"；**法律上**它**降低**（不保证消除）平台被认定为 GPL 衍生作品的风险。反向红线明确：一旦平台进程内 import 引擎代码，几乎必然构成衍生作品 → 平台被迫 GPL-3.0。所以这条纪律值得严守，但"平台可自选许可"的结论应由**实际分发结构 + 法律意见**最终确认，而非架构本身自动担保。

> **许可免责（v0.3 降调）**：本节许可分析是**工程架构层面的风险判断，非法律意见**。"进程外边界 ⇒ 平台不被 GPL 传染"是一个**旨在降低风险的设计目标**，不是自动成立的法定结论。正式分发前应就实际打包/分发结构咨询法律意见，确认平台与 GPL 引擎是否构成"单一程序"。

**平台许可（已拍板 2026-09-05，附录 C Q6）= Apache-2.0**：

- ✅ **选定 · Apache-2.0**：胶水层宽松 + 带专利授权，欢迎社区贡献平台代码；与"内容 MIT、引擎 GPL"组成干净三层拓扑（前提：守住进程外边界以**降低**衍生作品风险；最终定性见上方免责，需按实际分发结构 + 法律意见确认）。
- ❌ **未选 · GPL-3.0**：与引擎同许可虽彻底消除衍生作品歧义、copyleft 精神最纯，但对惧 GPL 的贡献者摩擦略高；平台胶水层取宽松更利于生态。
- **落地动作**：paper-platform 仓脚手架时，仓根放 Apache-2.0 `LICENSE` + `NOTICE`；分发时保留 `prar-agent/LICENSE`(GPL-3.0) 与 `seckill-lab/LICENSE`(MIT)。

**可选升级（AGPL，一句话）**：平台是自托管网络服务，GPL-3.0 有"ASP 漏洞"——他人可把**改过的引擎**作为托管服务运行而不开源改动（GPL 只在**分发**时触发，不在网络使用时）。若日后想"托管部署也必须回流改进"，引擎可升级 **AGPL-3.0**。当前 D3 自托管 + 本地阅卷模型下 GPL-3.0 已够用，不必现在改。

**版权署名**：两仓 LICENSE/README 暂署 `Copyright (C) 2026 OnlyblackTea`（GitHub 身份）。若要改用法定姓名/组织名，改 LICENSE 与 README 各一处即可。

**seckill-lab 接入定性**：明确为**第一个社区贡献 track**，不被平台特殊硬绑——经 §4 的 `paper new-track` 脚手架 + `paper validate-track` 准入门（骨架红/参考解绿）走标准流程纳入，与未来任何社区题库同一通道。附录 B 的 seckill-lab 文件映射是"首个 track 的引导记录"，非平台硬依赖。

---

## 7. 本地阅卷 ★（paper-cli + 学员自有算力，落实 D3）

### 7.1 为什么本地优先

1. **零云成本**：契合 D3 与开源自托管。
2. **契合 seckill-lab 既有事实**：它本来就要求学员本地 `docker compose up` 真 MySQL(13306)+Redis(16379) 跑 `mvn test -Dgroups=stageNN`（**拒绝 H2/Testcontainers**，因为 InnoDB 间隙锁/MVCC/EXPLAIN 行为不对会教错八股）。本地阅卷 = 把这个既有动作**工具化、标准化**。
3. **真环境真信号**：并发 sim（Stage3 零超卖）这类测试在学员真机上跑最贴近实际。

### 7.2 paper-cli 命令面（学员设备）

```bash
paper enroll <track>            # 从 Gitea 拉「考卷」fork（仅当前已解锁 stage）
paper up                        # docker compose 起 MySQL+Redis（复用 track 的 docker/）
paper dev                       # 打开浏览器 IDE(Monaco/code-server) 或提示用本地 IDE
paper mentor                    # 连接 AI 导师 session（求助/讲解，不代写）
paper grade [--group stageNN]   # 运行时注入 canonical 测试 → 本地跑 → 出阅卷报告
paper submit                    # 建 PR + 答辩模板 + 附阅卷报告
paper sim                       # 跑并发模拟器（Stage3+），出 QPS/超卖数
```

### 7.3 canonical 测试的运行时注入（轻摩擦防篡改，呼应 D2）

- 学员的考卷仓**不含** canonical 测试（`src/test` 里只有冒烟测试占位）。
- `paper grade` 运行时从**学员 PR 改不到的来源**拉 canonical 测试注入：
  - 来源选项：题库私有 ref / paper-cli 内置模板；
  - 注入到一次性目录，跑完即清，**不落进学员可提交的 working tree**。
- 学员对 `src/test`、`pom.xml`、fixture 的改动**一律不参与阅卷**（grader 只认 canonical 版本 + `files_to_change` 白名单内的业务改动）。

> 这是**轻摩擦**：挡住"顺手改测试作弊"，但**不挡铁了心的人**（D2 君子协定，§13）。

### 7.4 阅卷报告（evidence）结构化格式

`paper grade` 产出 `grading-report.json`，AI 评审据此点评：

```json
{
  "ticket": "db-stage-01",
  "ticket_version": 1,
  "test_version": 1,
  "engine_version": "v0.1.0",
  "learner": "@xxx",
  "provenance": {
    "generated_at": "2026-09-05T10:00:00Z",
    "generated_by": "paper-cli@0.1.0",
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

> **v0.3 定位变更：grading-report = 不可信证据（claim，带 provenance）**。本地阅卷发生在学员自有设备（D3），平台**无法把红绿当事实**——`correctness.passed` 是学员/CLI 的**自报声明**，不是已验证事实。所以报告必须自带 `provenance`（谁、什么环境、何时）+ 版本锚点（钉回 §9.1 TicketInstance）+ `runner_trust` 信任级，且**证据继承与 track 同源的信任分级**（self/verified/community，§13.2）：
> - `self`：v0.4 自习平台的**唯一**取值——没有受控 runner 复跑，红绿始终是学员/CLI 的自报声明，靠荣誉制（§13.1）而非密码学签名背书。
>
> 这一条统一了三个看似不同的问题——"本地阅卷可伪造""社区 track 可投毒""AI 评审权力过大"——它们本质都是**信任分级**问题：AI Review **读**证据并给建议，AcceptanceDecision（§11.3）按"证据信任级 + 测试硬门 + 配置"**裁决**，谁都不盲信单一来源。

### 7.5 并发 / 性能测试的特殊处理（Stage 3+）

- 并发 sim（`SeckillLoadSimulator`）**慢且可能 flaky**：与正确性测试**分开评级**。
- `paper sim` 用受控并发跑，输出 QPS + **超卖数**；超卖数必须 = 0 才算过。
- flaky 容忍：可重试 N 次取多数；报告里标**置信度**；评审对低置信度结果保守处理（必要时请学员复跑）。

---

## 8. fork 与防剧透（轻量 · 君子协定，落实 D2）

**设计原则：不建硬墙，只加"轻摩擦"，依赖"用户主动参与"的君子协定。** 开源场景下答案本就藏不住（公开 repo 的 `solutions` 分支照样能 checkout），死磕防剧透是浪费。

| 维度 | 做法（轻摩擦） | 明确不做 |
|------|--------------|---------|
| **后续 stage** | 考卷 fork 只 checkout 当前已解锁 stage 的 ref；未来 stage 是 Gitea 上**独立 ref**，`paper enroll` 默认不拉 | 不做加密、不做服务端硬隔离 |
| **canonical 测试** | 不在考卷仓里，`paper grade` 运行时注入（§7.3） | 不做混淆 |
| **参考解 solutions** | UI **不 surfaced**；不在考卷仓里链接；要看得刻意去题库找 | 不删除、不隐藏（开源 + 过关后对照学习有价值） |
| **过关后对照** | 通过后**主动**展示 solutions + AI 讲解"你的解法 vs 参考解" | — |

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
MentorSession     学员 × TicketInstance 的 prar-agent session（导师状态机实例）
Submission        ★v0.3 改名（原"答卷"）：TicketInstance 内的一次 attempt = PR + diff + 答辩文本
GradingReport     阅卷结果（正确性/性能/并发/lint/越界告警 + provenance + runner_trust）= 不可信"证据声明"（§7.4）
Review            ★v0.3 定位变更：AI 评审 = 顾问性、非确定性（rubric 维度点评 + 锚定点评 + 建议），不直接置 passed
AcceptanceDecision ★v0.3 新：确定性、可审计的验收裁决
                  = f(测试硬门, AI 建议, 证据信任级, 配置[auto]) → pass/fail → 触发解锁
LearnerProfile    学习者画像（pgvector 三层记忆，per user_id）
```

关系：
- `User 1—N Enrollment`
- `Enrollment 1—N TicketInstance`
- `Ticket 1—N TicketInstance`（同一工单：不同学员各一个；版本变更后同一学员再得一个新 Instance）
- `TicketInstance 1—1 MentorSession`（一张考卷一个导师 session）
- `TicketInstance 1—N Submission`（**retry = 同 Instance 内新增 Submission**）
- `Submission 1—1 GradingReport`；`Submission 1—N Review`；`Submission 1—1 AcceptanceDecision`

```
Ticket(db-stage-01, v1)
  └─ TicketInstance #A {learner=alice, ticket_version=1, engine_version=v0.1.0, test_version=1}
       ├─ MentorSession（一张考卷一个）
       ├─ Submission #1 → GradingReport(红) + Review(建议打回) → AcceptanceDecision(fail)
       └─ Submission #2 → GradingReport(绿) + Review(建议通过) → AcceptanceDecision(pass) → 解锁下游
```

> **为什么必须有 TicketInstance**：它把"一次考核所依赖的全部版本"冻结成一个不可变快照，从而在 schema 层（而非运行时扯皮）回答用户提的五个硬问题：

| 硬问题 | schema 层答案 |
|--------|--------------|
| 这个 PR 基于哪个 ticket 版本？ | `Submission → TicketInstance.ticket_version` |
| 这个 grading report 是哪个 test version 跑的？ | `GradingReport → TicketInstance.test_version`（+ report 自带 provenance） |
| 题目更新后，旧 submission 还有效吗？ | 旧 Instance 标 `stale`（保留不删）；新 `ticket_version` → re-issue 新 Instance（§9.2） |
| 引擎更新后，旧 session 用哪个 engine？ | `TicketInstance.engine_version` 冻结；升级只影响新建 Instance |
| 同一 ticket retry 是重发卷还是续上一张？ | **续**：retry = 同 Instance 新 Submission；只有版本变更才 re-issue 新 Instance |

### 9.2 TicketInstance 生命周期状态机（平台层，叠加在导师 session 之上）

```
locked → available → in_progress → submitted → grading
   → passed → (unlock dependents)
   → failed → in_progress（同一 Instance 内新 Submission，可多次 attempt）
```

- `available`：本 ticket 的 `progression.prerequisites`（§4.3）**全部 passed**（DAG 判定，非单一线性"上一张过了"）。
- `submitted`：PR 已开 + 答辩 + 阅卷报告（证据声明）已附。
- `grading → passed/failed`：由 **AcceptanceDecision**（确定性策略门，§11.3）裁决，**不是 AI Review 直接置位**——Review 只给建议。
- `passed` 触发解锁：所有把本 ticket 列为 prerequisite 的**下游 TicketInstance** 由 `locked → available`，并写 episodic 记忆。
- **retry（同一 Instance）**：`ticket_version`/`engine_version`/`test_version` 全部不变，仅新增 Submission；每次 attempt 留痕不覆盖（画像可看"几次过"）。
- **re-issue（新建 Instance）**：当 `ticket_version` 或 `test_version` 或 `engine_version` 变更 → 旧 Instance 标 `stale`（**保留历史，不删**），新建 Instance 重新发卷。

### 9.3 导师 session 状态机

直接复用 prar-agent 6 阶段（§1.3 重映射），不新建。

---

## 10. 学习者画像与个性化（LTM 复用）

把 prar-agent 的 pgvector 三层记忆**换个用途** = 学习者画像。几乎零额外架构（`memory/long_term.py` + `consolidator.py` + LTM_RECALL 已就绪）。

| 记忆层 | 原语义 | 平台新语义 |
|--------|--------|-----------|
| Episodic | "某日用 Postgres 实现 X" | "2026-09-05 通过 db-stage-01，一次过，独立解决（没用 hints）" |
| Semantic | "用户偏好 Postgres" | "该学员**乐观锁 vs 悲观锁反复混淆**；EXPLAIN 解读薄弱" |
| Procedural | "shell→git→test 高频组合" | "该学员习惯先写测试再调索引；卡住平均求助 2 次" |

应用：
- **个性化发卷**（§6.2）：AI 经理据画像补背景名词解释。
- **薄弱点重练**：consolidator 提炼出反复错的考点 → 推送针对性变体工单。
- **间隔重复**：八股考点按遗忘曲线重现（口试/小测）。
- **hints 用量**进画像："独立解决" vs "靠提示" 区分，供薄弱点重练与自我复盘（不参与排名/得分）。

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

### 11.2 AI Review（顾问性、非确定性）

AI 评审产出**建议 + 锚定点评**，写进 Review 实体；它**不直接置 passed**——非确定性的 LLM 输出不能直接驱动确定性的状态机解锁（见 §11.3）。

| 维度（定性，不计分） | 评什么 |
|------|--------|
| **思路质量** | 答辩里的设计理由对不对、是否真懂（AI 重点评这个，呼应"面试要讲出自己思路"） |
| 代码质量 | 是否符合 track 约定（seckill-lab `docs/架构与约定.md`：分层/命名/MyBatis SQL 约定） |
| 证据完整度 | EXPLAIN/sim 证据是否齐、是否佐证结论 |
| 独立性 | hints 用量、attempt 次数（来自画像） |
| 越界告警 | `out_of_scope_edits` 非空（改了 test/pom/fixture）→ 标红，提示 AcceptanceDecision 重点核查 |

输出：`recommendation ∈ {approve, request_changes}` + 每条 rubric 的锚定点评（复用 prar-agent 评论锚定）。**注意：正确性红绿不在此表**——它是确定性硬门，归 §11.3，不交给 AI 自由裁量。

### 11.3 AcceptanceDecision（确定性、可审计的验收门）

把"是否通过"从 AI 手里拿出来，交给一个**确定性策略函数**，产出可审计的 AcceptanceDecision 记录（§9.1）：

```
AcceptanceDecision = f(
    correctness_gate,       # 硬门：grading-report.correctness.passed（按 runner_trust 决定采信度，§7.4）
    review_recommendation,  # AI Review 的建议（approve / request_changes），非确定性被隔离在此单一输入
    evidence_trust,         # 证据信任级 self|verified|community（§7.4 / §13.2）
    config                  # auto（自习场景唯一模式）
) → pass | fail
```

| 模式 | 谁/什么拍板 | 适用 |
|------|-----------|------|
| **auto**（唯一） | correctness 硬门绿 + review=approve + 证据信任级达标 → 自动 pass | 部署者自习 |

- **确定性**：相同输入永远得到相同 pass/fail，可重放、可审计；AI 的非确定性被关进 `review_recommendation` 这一个输入。
- **解耦**：危险的 `AI output → passed → unlock` 耦合被切断，改为 `AI output → recommendation →（连同硬门/证据信任/配置）→ AcceptanceDecision → unlock`。
- **留痕**：每次裁决写一条 AcceptanceDecision（输入快照 + 结果 + 时间 + 裁决者[auto]），re-issue 或申诉时可回溯。

> 这是 seckill-lab 原本"人决策验收"的延续：自习场景把"人"换成"确定性策略门 + 客观测试 + AI 建议"，部署者本人无需第三方终审。

### 11.4 过程回放评审（git checkpoint 复用，增强"真懂"判定）

借 prar-agent `checkpoint.py`（每 step 一 commit），AI 能看学员**解题全过程**（试了几次、走了哪些弯路、EXPLAIN 前后改了什么），据此判断"真懂 vs 蒙对"，给过程性反馈。人类导师都难做到的细粒度。

- **过程证据 → 喂给 §11.2 思路质量维度**：即使 correctness 硬门已绿，若过程显示"反复试错撞对""抄了又删""改完讲不出理由"，Review 仍可返回 `request_changes`，经 §11.3 AcceptanceDecision 拦下"蒙对"。
- **绿 ≠ 懂**：把"结果对"与"过程真懂"分开评，正是面试要考的能力——这也是过程回放相对单看 PR diff 的增量价值。

---

## 12. 过关后：八股讲解 + AI 口试

### 12.1 八股讲解

过关 → 展示 `bagu-map.md` 对应章节（如 Stage1 "索引 & 执行计划"）+ solutions 对照 + AI 串讲。

### 12.2 AI 口试（mock interview，直击面试目标）

- 复用 prar-agent 的**决策题节点 + 评论锚定**作为交互式口试载体。
- 过关后 AI 发起一轮八股口试："为什么范围列之后的索引列会失效？""Using filesort 说明什么？"
- 口试表现回写画像 → 影响薄弱点重练与间隔重复。
- 这是把"做对题"升级为"讲得出来"，正中面试痛点。

---

## 13. 安全 / 滥用 / 信任边界

### 13.1 学员侧信任边界（D2 君子协定）

| 我们**防** | 我们**不防**（D2 君子协定） |
|-----------|---------------------------|
| 阅卷信号被顺手篡改（canonical 测试运行时注入、`out_of_scope_edits` 告警） | 铁了心的学员深挖题库找 solutions / 伪造阅卷报告 |
| 导师代写答案（三层 guardrail，§6.4） | 学员自己拿外部 AI 代写（无法在本地设备防住，靠答辩+口试暴露） |
| 越界改测试/pom/fixture（白名单 + 告警） | — |

**信任模型**：开源自托管 + 本地阅卷 ⇒ **没有中心化可信计算**。平台提供的是"**让认真学习阻力最小、让偷懒在答辩/口试时暴露**"的机制，而非密码学级防作弊。v0.4 自习定位下不设认证模式 / 老师终审——红绿始终是自报声明，靠荣誉制（§13.1）与答辩/口试交叉暴露兜底。

**LLM/算力自带**：部署方提供 LLM key 或本地模型；平台不分发、不代付、不托管密钥。

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
2. **评审**——`paper validate-track` CI 在**隔离环境**跑通骨架/测试/判别性，维护者人审 runtime；
3. **digest 锁定**——`runtime.digest_pin` 钉死容器镜像/依赖 digest，防 merge 后偷偷漂移；
4. **声誉**——maintainer 署名、commit 历史、catalog 声誉，让投毒有代价。

**纵深防御（不是硬保证）**：sandbox（rootless Docker、无 host 挂载、egress allowlist 只放 LLM/Gitea）能**抬高**攻击成本，但——

> **诚实边界**：阅卷发生在**学员自己拥有的设备**上，平台无法控制其内核/权限/Docker 配置。一个有 root 权限、能改 compose、能装内核模块的学员机器，**没有任何本地 sandbox 能给出密码学级的隔离保证**。所以我们不把 sandbox 当银弹宣传，而是：信任分级 + 披露 + digest 锁定 + 评审为主，sandbox 为纵深防御的一层。

---

## 14. 分期路线图（开源教学项目导向）

| 阶段 | 目标 | 交付 |
|------|------|------|
| **MVP** | 单 track（数据库八股）+ 单机 all-in-one 跑通完整闭环 | Gitea + 平台服务 + prar-agent(mentor_mode) + paper-cli；seckill-lab Stage0/1 走通"发卷→开发→本地阅卷→PR→AI评审→讲解→解锁"；ticket schema + 内容标准化第一版 |
| **P1** | 把 seckill-lab 6 个 stage 全接入 + 教学增强 | 学习者画像、AI 口试、PR 答辩评审、过程回放评审 |
| **P2** | **社区可贡献**（开源命脉） | track 脚手架生成器 `paper new-track`、准入校验门 `paper validate-track`、贡献工作流 + 文档；第二个 track（如 JVM）打样 |

> **务实建议**：MVP **只深耕数据库八股一个 track**，先证明"角色反转 + 本地阅卷 + AI 评审"闭环成立，再谈内容扩张。内容生产是长期瓶颈，不是软件。

---

## 15. 风险与不在范围

**风险**：
1. **内容生产是真正瓶颈**：做一个 seckill-lab 质量的 track 工作量极大；脚手架生成器只能降边际成本，不能替代人工打磨。
2. **本地阅卷天然可伪造**：荣誉制下接受，靠答辩 + 口试交叉暴露兜底；v0.4 不设认证模式 / 老师终审。
3. **并发 sim 阅卷 flaky**：重试 + 置信度，不追求 100% 确定。
4. **prar-agent 单 session 编排**：单部署者档够用；若要大规模并发需重新评估引擎多租户（明确不在本期）。

**不在范围**：
- 班级管理 / 排行榜 / 数值化得分 / 参数化试卷 / 认证模式 runner / 老师终审（v0.4 收缩：平台只服务部署者本人自习刷题，不含多用户教学治理）。
- 中心化云托管考场 / 多租户 SaaS 计费（D3 反向）。
- 密码学级防作弊（D2 反向）。
- 移动端 / 实时多人协作编辑。
- 自动大规模生产 track 内容（AI 起草 + 人工验收，不做全自动）。

---

## 附录 A · 架构亮点（可对外讲述 / 面试）

1. **Manager↔Human-Executor 角色反转**：不是给自治 agent 加约束防代写，而是从角色定义上让人占据执行位——`fs.write` 写业务码的能力根本不启用。这是对"AI agent 用于教育"的本质性设计取舍。
2. **零中心算力的开源自托管**：阅卷在学员设备、LLM 由部署方自带、git 用自托管 Gitea ⇒ 平台中心零云成本，信任模型自洽。
3. **引擎不改内核的多路复用**：尊重 prar-agent "单 session" 约束，多用户编排放平台层，复用状态机/sandbox/git checkpoint/pgvector 记忆/LiteLLM router。
4. **内容标准化 + 准入校验门**：把"测试判别性（骨架红/参考解绿）"做成 CI 强制门，是开源内容生态能否规模化的关键工程。
5. **学习者画像 = LTM 换语义**：现成 pgvector 三层记忆几乎零成本改造为个性化教学引擎。
6. **取码与交互两轴解耦**：submodule（取码轴）给单仓体验，进程外稳定 API + 唯一适配层 + 契约测试（交互轴）给"引擎升级只动接口"的隔离。把"依赖一个快速演进的引擎"这件高风险事，拆成可复现（钉 tag）、可守门（契约测试）、可替换（适配层）三件小事——这是依赖治理的通用范式，不止用于本项目。
7. **三层许可拓扑 + GPL 边界（工程降风险，法律需复核）**：引擎 GPL-3.0（copyleft 保改进回流）、内容 MIT（低摩擦社区题库）、平台 Apache-2.0（已拍板）；§6.6 的进程外边界**旨在降低**平台被认定为 GPL 衍生作品的风险、为自选 Apache-2.0 留空间。把"用 copyleft 引擎又尽量不被其许可绑架"这件开源常踩的坑，用一条架构边界去**缓解**——但"独立程序 / mere aggregation"的最终定性仍取决于实际分发结构与法律意见，非架构自动担保（详见 §6.7 及其免责）。

## 附录 B · 与现有两仓的对接清单

| 现有 | 对接点 |
|------|--------|
| seckill-lab `TASKS/stage-NN.md` | → 转写为 `ticket.yaml`（§4.3） |
| seckill-lab `src/test/.../ProductQueryIndexAcceptanceTest.java` | → canonical 测试，运行时注入（§7.3） |
| seckill-lab `src/test/.../sim/SeckillLoadSimulator.java` | → `paper sim`（§7.5） |
| seckill-lab `docker/`（MySQL13306+Redis16379） | → `paper up` 直接复用 |
| seckill-lab `docs/数据库八股地图.md` | → `bagu-map.md`（讲解锚点） |
| seckill-lab `docs/架构与约定.md` | → 代码质量评审依据（§11.2）+ 脚手架模板 |
| seckill-lab `solutions` 分支 | → 准入校验 + 过关后对照（§8） |
| prar-agent `core/*`、`tools/*`、`memory/*`、`llm/*`、`api/ws.py`、前端 | → 见 §3.1 复用对照表 |
| prar-agent **整仓**（submodule）+ `api/{sessions,ws_plan,ws_act,comments,memories,providers,adapters}.py` 契约面 | → 取码与集成策略见 §6.6（唯一适配层 `prar_bridge`、钉 tag、契约测试） |

## 附录 C · 开放问题（待评审拍板）

1. ~~**平台服务与 prar-agent 的进程关系**~~ ✅ **已决（见 §6.6）**：进程外，平台经唯一适配层 `prar_bridge` 只绑 prar-agent 稳定 HTTP/WS API；源码用 git submodule 引入、钉 tag、契约测试守门。session_pool 据此实现为"对 N 个独立引擎会话的多路复用"。
2. **paper-cli 形态**：纯 Python CLI，还是带轻量本地 Web UI（复用 prar-agent 前端）？
3. ~~**认证模式的签名强度**~~ ✅ **已决（v0.4）**：删除认证模式与报告签名——自习平台纯荣誉制不签，保留 `runner_trust`（恒为 `self`）作"本地自报"诚实标记；`signed_by` 仅用于 verified track 的供应链背书（§4.2/§4.6），与阅卷报告无关。
4. **第二个打样 track 选哪个**：JVM / 并发 / 网络？（P2 验证脚手架通用性）
5. **题库与平台是否同仓**：monorepo（content/ 与 platform/ 同仓）还是分仓（利于社区只 fork 题库）？倾向**分仓**。注意：§6.6 已单独钉死 prar-agent ↔ platform 用 submodule；本问只决定**内容仓**（seckill-lab 类）相对 platform 的位置，与引擎耦合方式无关。
6. ~~**paper-platform 自身许可**~~ ✅ **已决（2026-09-05，见 §6.7）= Apache-2.0**：引擎 GPL-3.0、内容 MIT、平台 Apache-2.0 三层拓扑落定。前提是守住 §6.6 进程外边界以**降低** GPL 衍生作品风险；但"平台不被传染、可自选许可"的最终定性需按实际分发结构 + 法律意见确认（见 §6.7 免责）。平台仓脚手架时补 `LICENSE`+`NOTICE` 并保留引擎/内容各自许可证。

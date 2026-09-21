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

> 但 seckill-lab **尚未**按「一阶段一 ref」改造（它现在只有 `main` + `solutions` 两条分支，见设计 §8.1）。
> 所以对着它点「起工作区」会如实返回短码 `workspace_ref_missing`，并列出仓库里实际可用的 ref——
> 这是**预期内**行为，不是 bug（对接现状见设计附录 B）。

验证（全绿才算完成）：

```bash
.venv/Scripts/python.exe -m pytest -q        # 173 passed
.venv/Scripts/python.exe -m ruff check .

cd frontend
npm run build                                 # tsc --noEmit + vite build
npm test                                      # vitest + RTL，40 passed
npx playwright test                           # 真浏览器 + 真后端，3 passed
```

---

## 设计支柱（已锁定，不可逆）

- **D1 —— 角色反转，结构性保证。** 平台里**没有 Executor 这个座位**：不是"禁用了写代码的工具"，
  而是**根本没有会写业务代码的组件**。因此 D1 不需要 guardrail 去守，它是状态机的形状。
- **D2 —— 荣誉制防剧透。** 只做轻摩擦：**一阶段一 ref**（未解锁阶段不在工作区里）、过关前不 surfaced 参考解。
  不设硬性高墙；开源的答案本来也藏不住。
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
| **真实阅卷**（Docker + JDK17 + Maven） | ⬜ 桩；**环境缺失**（本机无 Docker、JDK 非 17），见设计 §7.1 |
| **真实 Forge**（Gitea fork / PR） | ⬜ 桩（真本地 clone + 真本地 git diff，PR 为占位） |
| **AI 评审 / 口试** | ⬜ 桩；设计为**可选挂件**（`ReviewEngine` 缝），见设计 §11.2 / §12.2 |
| PostgreSQL 存储 / 学习者画像 / 脚手架生成器 | ⬜ 后续切片，见设计 §14 路线图 |

> **"模拟"徽标**：桩实现下，UI 会明确标注哪些环节是占位（`SimulatedBadge`），
> 不让人误以为阅卷 / PR 已经是真的。

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

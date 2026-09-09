# 发卷平台 · bagu-trainer

> 开源、可自托管的 **八股实战演练所** —— 供部署者（也是唯一用户）自习、刷题。
> 它是 [`seckill-lab`](https://github.com/OnlyblackTea/seckill-lab) 的延伸，接受社区贡献（提交新轨道 / 扩展八股面）。
> **不含班级 / 排名 / 得分**，不是多租户 SaaS。
> AI 是 **管理者 / 导师 / 评审者**；**由人类学习者亲手写代码**。
> 阅卷在 **学习者自己的机器上** 运行标准测试（canonical tests）。流程仿 GitHub：
> `fork → 开发 → PR → 通过 → 解锁下一关`。

引擎 = [`prar-agent`](https://github.com/OnlyblackTea/prar-agent)（git 子模块）。
首个内容轨道 = [`seckill-lab`](https://github.com/OnlyblackTea/seckill-lab)（数据库八股）。
权威设计文档：[`docs/paper-platform-design.md`](docs/paper-platform-design.md)（v0.4）。

---

## 快速开始（贡献者）

```bash
# 一次性把引擎子模块一起 clone 下来 —— 记住这一行：
git clone --recursive https://github.com/OnlyblackTea/bagu-trainer.git

# 已经用普通方式 clone 了（没带 --recursive）？再把子模块拉进来：
cd bagu-trainer
make bootstrap          # 等价于 git submodule update --init --recursive
```

在完成上面任一操作之前，`prar-agent/` 目录会是 **空的**。
子模块 pin 在一个公开 tag 上（当前为 `v0.1.0`）；升级引擎是一次 **显式的 tag bump**，
绝不会自动同步 —— 因此一份考卷永远可以针对它「发卷时」的引擎版本 **复现**。

---

## 三层结构（以及为什么拆成独立的仓）

| 层 | 是什么 | 仓库 | 许可 |
|----|--------|------|------|
| **内容层（Content）** | 轨道 / 阶段 / 工单 + 骨架代码 + 标准测试 + 八股知识点地图 + 提示 + 参考解答 | `seckill-lab`（及未来的社区轨道） | **MIT** |
| **引擎层（Engine）** | AI 管理者 / 导师 / 评审者 + 沙箱 + git 检查点 + 记忆 | `prar-agent`（子模块） | **GPL-3.0-or-later** |
| **平台层（Platform）** | 多会话编排、fork/PR、阅卷、解锁、学习者档案、看板 | **本仓** | **Apache-2.0** |

### 许可拓扑与 GPL 边界（动 `orchestrator/` 之前请先读）

本仓是 **Apache-2.0**。这个选择依赖于一条纪律（外加下方的法律提醒）：

> **发卷平台只通过 prar-agent 的进程外 HTTP/WS API 与之通信，且每一次这样的调用都
> 收口到唯一的适配器 [`orchestrator/prar_bridge.py`](orchestrator/prar_bridge.py)。
> 没有任何平台模块导入引擎内部实现（`core/*`、`llm/*`、`memory/*`、`tools/*`）。**

这条「保持距离」的边界 **旨在** 让两者维持为 **相互独立的程序**，并 **降低** prar-agent 的
GPL copyleft 波及本仓的风险。一旦打破它（在进程内导入引擎的 Python 符号），发卷平台几乎
必然成为 GPL-3.0 的衍生作品。同一条边界也是 **工程上** 的收益：升级引擎永远只会动到适配器。
它由 [`contracts/test_boundary.py`](contracts/test_boundary.py) 守卫。

> **法律提醒。** 两者究竟是否构成「单纯聚合（mere aggregation）」而非单一的组合程序，是一个
> **法律判定**，取决于实际的分发结构 —— 不能仅凭「通过 HTTP 通信」就下结论。
> 这是一个工程层面的风险判断，**并非法律意见**；在再分发之前请咨询有资质的律师确认。

完整论证见设计文档 §6.6 + §6.7。

---

## 设计支柱（已锁定，不可逆）

- **D1 —— 角色反转。** AI 绝不编写计分业务代码。在导师模式下，针对 `files_to_change`
  的写入 / 执行工具被禁用；ACTING 阶段的 Executor LLM **不会** 被调用 —— 人类才是执行者。
- **D2 —— 荣誉制防剧透。** 只做轻量摩擦（运行时注入测试、通过之前不展示解答）。
  不设硬性高墙；开源的答案本来也藏不住。
- **D3 —— 阅卷跑在学习者的算力上。** 平台中心 **零** 阅卷算力、**零** 强制 LLM 开销；
  部署者自带 LLM key，由学习者的机器运行标准测试。
- **D4 —— prar-agent 作为子模块 + 单一适配器。** 取代码这一轴 = 子模块；
  交互这一轴 = 仅经由 `prar_bridge.py` 的进程外 API。

---

## 仓库结构

```
bagu-trainer/
├── LICENSE                 Apache-2.0（本仓）
├── NOTICE                  署名 + GPL 边界声明
├── Makefile                bootstrap / test / lint / 子模块 bump
├── pyproject.toml
├── prar-agent/             ← git 子模块，pin 到 tag v0.1.0（GPL-3.0）
├── orchestrator/           平台侧的引擎编排
│   ├── prar_bridge.py      ★ 唯一知道 prar-agent API 的文件
│   └── session_pool.py     按 (学习者, 工单) 复用会话（§6.1）
├── platform_service/       FastAPI 控制面：目录/报名/生命周期/解锁/评审/gitea（§2.1）
├── paper_cli/              学习者设备客户端：报名/up/开发/导师/阅卷/提交/模拟（§7.2）
├── content/
│   └── schema/             track.schema.json + ticket.schema.json（§4.2/§4.3）—— 贡献契约
├── contracts/              边界守卫测试 + prar-agent API 契约测试（每次子模块 bump 都要过）
└── docs/                   paper-platform-design.md（权威设计稿，v0.4）
```

---

## 状态

本仓处于 **脚手架（scaffold）** 阶段 —— 架构、边界、schema、以及唯一的引擎适配器都已就位；
平台服务、paper-cli、内容准入门禁目前是占位实现，在设计文档中跟踪。
凡依赖 **prar-agent 侧契约冻结** 的条目（导师模式工具白名单、无 Executor 的 ACTING 会话模式、
AI PR 评审入口）在适配器中标记为 `PENDING`，**绝不** 抢在引擎之前臆造。

分阶段路线图见设计文档 §14，未决问题见附录 C（paper-cli 形态 Q2、第二条轨道 Q4、
轨道/平台仓拆分 Q5）。

---

## 贡献

- **内容（新轨道 / 新八股方向）：** fork 一个轨道仓，用 `paper new-track` 起草，
  再通过 `paper validate-track`（准入门禁会检查测试的 *区分度*：在骨架代码上必须红、
  在参考解答上必须绿），然后向目录开 PR。见设计文档 §4。
- **引擎改动：** 直接向 [`prar-agent`](https://github.com/OnlyblackTea/prar-agent)
  本身开 PR，然后在本仓 bump 子模块指针。**不要** 从本仓就地修改 `prar-agent/`。
- **平台改动：** 在本仓走正常 PR —— 遵循 Apache-2.0。

## 许可

Apache-2.0 —— 见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。

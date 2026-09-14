# Plan 2 / 2 — 学习者 SPA（`frontend/`）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 A′ 后端做一张学习者自己的脸：一个 React + Vite + TS 的 SPA，把 Plan 1 冻结的纯 REST 契约渲染成 5 个屏幕（Track 总览 / 发卷 / 工作区进度 / 提交评审 / 决策结果），凡桩产出一律标「模拟」，并在真浏览器里验过整条闭环。

**前置：** Plan 1 已完成（A′ 后端 `215 passed`，REST 契约冻结）。本计划只加 `frontend/` 与两个后端运维脚本，**不改**任何已冻结的端点形状。

**许可边界（必须遵守）：** `frontend/` **从零写**，整体 Apache-2.0。**不得**复制 `prar-agent/frontend/`（GPL）的任何代码、样式或组件——那是本项目一直在守的 GPL 边界。可以参考它的存在，但不读它的源码。

**Tech Stack:** Vite 7 · React 19 · TypeScript 5 · vitest + React Testing Library · Playwright（真浏览器 e2e，替代 spec §C.9 里不可用的 browser-use MCP）· Node 24 / npm 11。

---

## 1. 为什么用 Playwright 替代 browser-use MCP

spec §C.9 的「UI 改动必须在浏览器里验过才算完成」是项目铁律，但本机**没有 browser-use MCP**。
Playwright 是可安装（registry 可达）、可重复、可进 CI 的等价物，且比人工点更严格：它把
「发卷 → provision → submit → review → decide → 看到解锁 / retry」固化成断言。本计划据此执行。

---

## 2. 冻结的 REST 契约（Plan 1 产出，本计划的唯一接口）

| 方法 | 路径 | 用途 | 关注点 |
|---|---|---|---|
| GET | `/tracks` | Track 列表 | — |
| GET | `/tracks/{id}` | 看板：`stages[]`，每条含 `stage` / `kind` / `ticket_id` / `stage_index` / `title` / `prerequisites` | `kind` 二分：`assessed` / `board_only` |
| GET | `/tickets/{ticket_id}` | 发卷：`problem` / `concepts` / `files_to_change` / `hints` / `grader` | **按 `identity.id` 取卷**；用目录名取 → 404 |
| POST | `/enrollments` | 入学（201） | 返回 `enrollment` + `instances[]` |
| GET | `/instances?learner=` | 实例列表 | 每条带 `gate` 文案 |
| GET | `/instances/{id}` | 单实例 | 带 `gate` |
| POST | `/instances/{id}/start` | `available → in_progress` | — |
| POST | `/instances/{id}/provision` | 拉本地工作区 | 返回 `path` / `branch` / `base_ref` / `head_ref` / `simulated` |
| GET | `/instances/{id}/diff` | 真本地 git diff | `diff` / `total_chars` / `truncated_files` |
| POST | `/instances/{id}/pr` | 占位 PR | `pr_url` / `simulated` |
| POST | `/instances/{id}/submit?scenario=pass\|fail` | 阅卷桩 → submission | `scenario` 选 fixture 分支 |
| POST | `/submissions/{id}/review?scenario=approve\|request_changes` | 评审桩 → review | `comments[]` 带 `anchor` |
| POST | `/submissions/{id}/decide` | 确定性门 | 返回 `decision` + **全量 `instances[]`**（据此刷新看板） |
| POST | `/instances/{id}/retry` | `failed → in_progress` | — |

**错误形状：** 一律 `{"detail": "<短码>"}`（`not_found` / `illegal_transition` /
`decide_needs_report_and_review` / `diff_too_large` / `git_command_failed` /
`workspace_ref_missing`）；`workspace_ref_missing` 另带 `ref` 与 `available[]`。UI 必须把短码
翻成人话，**不得**只显示短码。

---

## 3. 文件结构（先锁边界）

**新建（后端运维，两个小文件）：**
- `platform_api/serve.py` — `python -m platform_api.serve`：建 demo track-repo（或 `--content-dir`）
  并用 uvicorn 起服务。**spec §C.9 写的 `uvicorn platform_api.app:app` 跑不通**——`create_app`
  需要 `content_dir`，模块级没有 `app`。这就是补它的原因。
- `tools/dev.sh` / Makefile 目标（可选）——一条命令同时起后端与前端。

**新建（`frontend/`）：**
- `package.json` · `vite.config.ts` · `tsconfig.json` · `tsconfig.node.json` · `index.html`
- `src/main.tsx` · `src/App.tsx` · `src/styles.css`
- `src/api.ts` — 唯一碰 HTTP 的地方（`fetch` + 短码 → 人话的错误映射）
- `src/types.ts` — 契约的 TS 投影（**手写**，不引 codegen）
- `src/components/SimulatedBadge.tsx` — 「模拟」徽标（所有桩产出必须带）
- `src/components/StageBoard.tsx` — Track 总览（5 个屏幕的第 1 屏）
- `src/components/TicketView.tsx` — 发卷（第 2 屏，含 board-only 占位与 D2 约束）
- `src/components/WorkspaceView.tsx` — 工作区/进度 + gate 文案 + diff 预览（第 3 屏）
- `src/components/ReviewView.tsx` — 提交/评审 + 行级评论（第 4 屏）
- `src/components/DecisionView.tsx` — 决策结果 + 解锁/retry 指示（第 5 屏）
- `src/__tests__/*.test.tsx` — vitest + RTL
- `e2e/learner-flow.spec.ts` + `playwright.config.ts` — 真浏览器闭环

**不改：** `platform_api/` 的路由/契约、`platform_service/`（除 §4 说明外）。

---

## 4. 屏幕与关键约束

1. **Track 总览**：读 `/tracks/{id}` 渲染看板；每阶段显示状态徽章（locked/available/in_progress/
   passed/failed）。`kind == "board_only"` 的阶段显示为**未出卷占位**（可点但只给标题），
   **不得**去取 `/tickets/{stage_dir}`——那会 404（身份是 `identity.id`，不是目录名）。
2. **发卷**：渲染 `problem` / `concepts` / `files_to_change` / `hints` / `grader`。
   **D2 防剧透**：页面**不展示**任何解法 ref、分支内容或 `-sol` 文本；只给题面与判据。
3. **工作区/进度**：显示 `state` + `gate` 文案（平台指示的下一步 git 动作）+ provision 按钮 +
   workspace 路径 + diff 预览（`simulated` → 标「模拟」）。**平台不代跑 git 写操作**，
   文案里要讲清"你在本地做"。
4. **提交/评审**：`scenario` 选择器（pass/fail、approve/request_changes）让两条 decide 分支都可演示；
   阅卷报告与 AI 评审都标「模拟」；评审按 `dimension_findings` 与 `comments[].anchor` 展示行级评论。
5. **决策结果**：pass → 显示"下一阶段已解锁"+ 推进指示；fail → 显示 retry 入口 + 评审意见。
   决策后**用响应里的 `instances[]` 刷新看板**（不要另发一轮请求）。

**错误呈现铁律：** 短码 → 人话（如 `workspace_ref_missing` → "这个 Track 缺少阶段分支
`<ref>`；仓库里现有 `<available>`"），绝不把短码原样丢给学习者。

---

## 5. 验证命令

```bash
# 后端（仓库根）
.venv/Scripts/python.exe -m pytest -q          # 必须保持 215 passed
.venv/Scripts/python.exe -m ruff check .

# 前端
cd frontend
npm install
npm run build          # tsc + vite build 必须过
npm test               # vitest run
npx playwright test    # 真浏览器 e2e（需先起后端与 vite）
```

---

## 6. Task 分解（TDD：先测后码）

- [ ] **Task 1 — 后端可启动入口**：`platform_api/serve.py`（`--content-dir` 可选，默认建 demo
  track-repo 到临时目录）。测：`tests/test_serve.py` 断言 `build_server_config()` 造出 app +
  host/port，且默认 content-dir 下 `/tracks` 可用（TestClient 即可，不必真起 uvicorn）。
- [ ] **Task 2 — 脚手架**：手写 `package.json` / `vite.config.ts`（`server.proxy` 把 `/tracks` 等
  转发到 `127.0.0.1:8000`）/ `tsconfig` / `index.html` / `main.tsx`。验收：`npm install` +
  `npm run build` + `npm test`（空跑一个 smoke 用例）全过。
- [ ] **Task 3 — types + api**：`types.ts` 按 §2 表逐字段投影；`api.ts` 封装 fetch，把
  `{"detail": short}` 映射成带人话的 `ApiError`。测：`api.test.ts` mock fetch，断言成功解析、
  短码映射成可读消息、`workspace_ref_missing` 带上 `ref`/`available`。
- [ ] **Task 4 — SimulatedBadge + StageBoard**：测：徽标渲染；看板按 `kind` 二分，
  board-only 不触发取卷请求。
- [ ] **Task 5 — TicketView**：测：渲染题面字段；**断言页面文本里不出现解法/`-sol`**（D2 回归护栏）。
- [ ] **Task 6 — WorkspaceView + ReviewView + DecisionView**：测：gate 文案可见；provision
  后显示路径且带「模拟」；评审渲染锚定评论；decision pass/fail 两分支的文案与刷新。
- [ ] **Task 7 — App 装配**：入学 → 看板 → 选阶段 → 走闭环；测：一个集成用例覆盖
  enroll→start→provision→submit→review→decide 的 UI 路径（mock fetch）。
- [ ] **Task 8 — 真浏览器 e2e**：Playwright 起真后端 + vite，点完两条 decide 分支
  （pass→解锁 / fail→retry），断言看板状态变化。
- [ ] **Task 9 — 收口**：`npm run build` / `npm test` / `npx playwright test` 全绿 +
  后端 `pytest` 215 + `ruff` 全绿；更新 README 的启动说明。

---

## 7. 自检（占位扫描 / 一致性）

- 无 TBD；每个 Task 都有可执行验收命令。
- 端点形状全部来自 §2 表，与 `platform_api/routes/*` 实际实现一致（Plan 1 已 e2e 覆盖）。
- 屏幕 ↔ spec §C.6 的 5 屏一一对应；`模拟` 徽标 ↔ §C.6「诚实占位 UI」；
  D2 ↔ §C.5/§8；gate 文案 ↔ §C.3。
- **不做**：多 Track 管理、登录体系、i18n、真实 AI/Forge 接入（spec §2 的未来切片）。

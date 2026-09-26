# deploy/ —— 一条命令起整栈（Gitea + 后端 + 前端，**Track 自动载入**）

```bash
cd deploy
cp .env.example .env
vi .env                       # 至少改 BAGU_PUBLIC_URL（别用 127.0.0.1，见下）
docker compose up -d
docker compose logs -f track-loader     # 看它载入了哪些阶段分支
# 浏览器打开 http://<BAGU_PUBLIC_URL 的主机>:8080
```

## 它解决了什么

以前要手工做的一串事——建 Gitea 用户、造令牌、建上游仓、**把内容仓的每条阶段分支推上去**、
确保学员的 fork 里也有这些分支——现在由 `track-loader` 在启动时做，顺序是：

```
gitea(healthy) ──► gitea-admin(跑完)  ──┐
                                       ├──► track-loader(跑完) ──► platform(healthy) ──► web
gitea(healthy) ────────────────────────┘
```

每一步都在解决一个真实的坑：

| 服务 | 做什么 | 为什么必须这样 |
|---|---|---|
| `gitea` | 官方镜像 + sqlite + `INSTALL_LOCK` | —— |
| `gitea-admin` | `gitea admin user create`（一次性，与 gitea 共用数据卷） | 官方镜像**不认** `GITEA_ADMIN_*`：实测 1.22 带着那些变量启动后容器健康，但 `GET /api/v1/user` 回 401 `user does not exist` |
| `track-loader` | 建用户/仓 → **推全**内容仓的分支 → 建/补齐 fork → 把令牌写进共享卷 | fork 是**创建那一刻**的快照。先后顺序错了，"新阶段在 fork 里永远缺席"，症状就是 provision 报 `workspace_ref_missing` |
| `platform` | 后端（`depends_on: track-loader: service_completed_successfully`） | 保证"Gitea 里已经有它要 clone 的阶段分支"再开始接客 |
| `web` | nginx：SPA + REST 反代 | 后端是**纯 REST**，不托管前端；少了它 `up` 完没有浏览器入口 |

## 三个必须知道的约束

1. **`BAGU_PUBLIC_URL` 要写局域网地址，不能写 `127.0.0.1`。** 它决定 Gitea 返回的 `clone_url` 与
   PR 链接；容器里的 `127.0.0.1` 是容器自己。写成 localhost 的症状是 `provision` 时 git clone
   一直超时（loader 启动时会为此打一条 `!!` 警告）。这个地址要**浏览器和容器都连得上**。
2. **内容仓必须有每个阶段的本地分支**（§C.5 一阶段一 ref）。loader 只推它看得见的：`git branch`
   里有几条，Gitea 里就有几条。换机器时用 `git bundle create x.bundle --all` 打包再克隆——
   本地分支才是内容的全部（GitHub 上往往只有 `main`）。
3. **`BAGU_FORK_RESET=1` 是破坏性的**：删掉学员的 fork 再重建，`dev` 分支与历史 PR 一起没。
   它的用处正是"把 dev 上不该有的东西（比如一份参考解）清掉，换一个干净起点"。默认 0。

## 真阅卷（可选）

默认 `GRADING=stub`（预置红绿，零依赖）。要真跑指标测试：

```bash
docker compose build --build-arg WITH_GRADING_TOOLCHAIN=1 platform
docker compose -f docker-compose.yml -f docker-compose.grading.yml up -d
```

这条把 JDK17 + Maven + docker CLI/compose 装进后端镜像，并挂上 `/var/run/docker.sock`——
Track 的基础设施（MySQL/Redis）由 compose 起，而那个守护进程在**宿主机**上。
**socket 等于宿主机的 root 权限**，只在你自己的机器上这么做。还有一个细节：grading 会把
`docker compose -f <工作区>/docker/docker-compose.yml` 交给宿主机守护进程，里面的相对路径
（`./mysql/init`）是按宿主机解析的，所以叠加文件用 `${BAGU_DATA_DIR}` **绑定挂载**而不是命名卷
（命名卷在宿主机的路径是 `/var/lib/docker/volumes/...`，相对路径就找不到了）。

## 一个 Gitea 的怪癖（不影响使用，但解释清楚免得你查半天）

`gitea/gitea:1.22` 会把**程序化建出来的 fork** 标成 `empty: true`，于是它的
`GET /repos/{owner}/{repo}/branches` 对**任何**分支都回 `null` / 404（实测：同一个 commit 上，
`git ls-remote` 列得出全部 14 条分支、网页也能正常浏览文件树，只有这个 API 说"空"）。
上游仓（用 API 建、再 push）不会有这个问题。

所以本仓一律**以 git 为准**判断分支在不在：`track_loader` 用 `git ls-remote` 等 fork 就绪并核对
分支数，`provision` 走的也是 git。别拿 Gitea 的 `/branches` 去判断"阶段分支有没有载入"。

## 常用操作

```bash
docker compose ps                       # 各服务状态（gitea-admin / track-loader 是 0 退出的）
docker compose logs -f track-loader     # 载入了哪些分支、token 是新造还是复用
docker compose exec platform python -c "import json,urllib.request as u;print(u.urlopen('http://127.0.0.1:8000/meta').read().decode())"
docker compose down                     # 停（数据卷保留：进度、Gitea 数据、令牌）
docker compose down -v                  # 连数据一起删（**进度与 Gitea 全没**）
docker compose up -d --force-recreate   # 改了 .env 之后
```

进度库在卷 `bagu-data` 里的 `/var/lib/bagu/progress.sqlite3`；要备份就
`docker compose cp platform:/var/lib/bagu/progress.sqlite3 ./backup.sqlite3`。

## 与其它拓扑的关系

这个栈是 [`../docs/deployment.md`](../docs/deployment.md) 里的**拓扑 D**：适合"整台机器都是我的、
想一条命令起全"。它比拓扑 A（全在宿主机上跑）多了一层容器隔离与自动载入，代价是两点：
真阅卷要挂 docker socket（见上），以及后端容器里的工作区在宿主机 docker 看来必须路径一致。

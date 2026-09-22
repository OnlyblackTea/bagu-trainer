# 部署指引（bagu-trainer）

> 面向**部署者**：这套东西装在哪、各部分跑在哪台机器上、怎么让自己（或别人）打开它。
> 本文只讲部署事实与取舍，不讲设计理由——那在 [`paper-platform-design.md`](./paper-platform-design.md)。

## 0. 先记住两件事实

**事实一：平台是"一个人用的服务"，不是 SaaS。** 没有注册、没有多租户、没有账号体系；部署者
就是唯一用户（`learner=deployer`）。**它默认没有任何认证**——谁能访问到端口，谁就能做题、
看你的进度。所以"怎么对外暴露"是这个文档里最需要认真读的一节。

**事实二：工作区、阅卷工具链、数据库，都必须在**同一台机器**上（也就是跑 `platform_api.serve`
的那台）。** 这三样都有"路径"或"本机进程"的含义：

| 东西 | 在哪 | 为什么 |
|---|---|---|
| **工作区**（你写代码的那份 clone） | 后端那台机器上 | `provision` 是后端在**本机**跑 `git clone`；diff 与阅卷也都读那条路径 |
| **阅卷工具链**（JDK / Maven / Docker） | 后端那台机器上 | `--grading docker` 在后端进程里跑 `mvn test`，并 `docker compose` 起 Track 的基础设施（D3：阅卷在"学员自己的机器"上——自托管时那台就是后端这台） |
| **进度数据库**（SQLite 文件 / PostgreSQL） | 后端那台机器（或网络可达） | 默认就是后端工作目录下的一个文件 |

于是"我想在自己电脑上用 IDE 写代码"这个需求，只有一个正确答案：**把后端跑在你自己电脑上**。
"能否让平台去读我另一台机器上的目录"——不能，它不认得别的机器的文件系统（见 §3 的三种拓扑）。

## 1. 三种拓扑，选一个

### 拓扑 A（推荐）：全部装在你自己的机器上

```
你的机器
├── platform_api.serve        ← 后端（工作区、阅卷、数据库都在这儿）
├── 浏览器 → 127.0.0.1:5173   ← 前端
├── JDK17 + Maven + Docker    ← 阅卷要用的工具链
└── bagu.sqlite3              ← 做题进度
```

```bash
# 1) 后端（零配置即可跑：默认 sqlite + 桩阅卷）
python -m platform_api.serve --content-dir ../seckill-lab

# 2) 前端（开发用 Vite；生产用 nginx/任意静态服务器托管 frontend/dist）
cd frontend && npm install && npm run build
python -m http.server 5173 --directory dist     # 或 nginx / caddy
```

**好处**：工作区就在你本机，IDE 直接打开；没有网络暴露面；阅卷最快。
**代价**：这台机器要有 JDK17 + Maven（要真阅卷的话还要 Docker）。

### 拓扑 B：平台在你机器上，阅卷在另一台（"重机"）

只有在"你的笔记本跑不动 Docker/MySQL"时才值得这么做。此时**阅卷那台机器必须能跑后端**，
所以现实做法是把**整个后端**装到那台机器上（拓扑 C），而不是把阅卷拆出去——平台没有"远程阅卷"
这个能力，硬拆要自己写一个 `GradingRunner` 后端（缝在那儿，`platform_service/grading.py`）。

### 拓扑 C：后端在服务器/虚拟机上，你从别处访问

```
服务器（10.x.x.x）
├── platform_api.serve --host 0.0.0.0 --port 8000
├── JDK17 + Maven + Docker        ← 阅卷在这儿跑
└── bagu.sqlite3（或 PostgreSQL）

你的机器
└── 浏览器 → http://10.x.x.x:8000
```

**代价（必须先接受）**：工作区在**服务器**上，你在自己电脑上写的代码，后端看不见。两条可行路径：

1. **SSH 进服务器写**（`ssh` + 编辑器 / VS Code Remote-SSH），或
2. **在自己机器上 clone，把路径登记给后端**——但只有在后端**能看见那个路径**时才成立，
   也就是说这条只有在"后端与你在同一台机器"时才有意义（拓扑 A）。

> 这就是为什么 §0 的事实二值得先读：**"让平台读我本地目录"不是配置问题，是架构边界。**

## 2. 存储：进度存在哪

默认后端是 **SQLite**（标准库自带，零依赖），启动横幅会打印**绝对路径**：

```
存储: sqlite → /srv/bagu/bagu.sqlite3
```

| `--store` | 落点 | 什么时候用 |
|---|---|---|
| `sqlite`（默认） | `--database-url` 给的路径，缺省 `./bagu.sqlite3` | 单机自托管。**备份就是复制这个文件**（连同 `-wal`/`-shm`，或先停服务） |
| `postgres` | libpq 连接串 | 多实例共库、或你已经有 PostgreSQL 运维 |
| `memory` | 进程内 | 只适合"我就看看界面"；**重启即失忆** |

```bash
python -m platform_api.serve --content-dir ../seckill-lab \
  --store sqlite --database-url /srv/bagu/progress.sqlite3
```

`GET /meta` 会如实回报 `store_backend` 与 `store_target`，前端也据此告诉你"重启会不会丢"。

## 3. 工作区：谁 clone、在哪 clone

默认（**平台替你准备**）：`POST /instances/{id}/provision` 在后端本机 clone 到
`<forge-base>/<track>/<instance-id>/`，并配好 push 凭据（token 在仓库外的 0600 文件里）与 git 身份。

**指引式**（在你的目录里做）：界面同时给出三行命令，你可以在**自己熟悉的地方**拉：

```bash
git clone http://<gitea>/<you>/<repo>.git ~/my-bagu-stage-01
cd ~/my-bagu-stage-01
git checkout -b dev origin/stage-01-product-query-index
```

拉完把那个目录的路径登记回来（界面上的输入框 → `POST /instances/{id}/workspace`），此后 diff 与
阅卷都对着它跑；`{"path": null}` 撤销登记，回到平台准备的目录。路径**必须是后端这台机器上能看到的
目录**（§0 事实二），否则会得到 `422 workspace_path_invalid`。

## 4. 对外暴露：把它放到网络上（请认真读）

**默认什么都不做**：`serve` 绑 `127.0.0.1`，只有本机能访问。这是最安全的默认值。

平台**没有认证**，所以对外暴露时你必须自己在前面加一层。三种常见做法，按推荐度：

### (a) 只给自己用 → SSH 端口转发（不改任何服务配置）

```bash
# 在你自己的机器上跑；把远端后端映射到本地 8000
ssh -N -L 8000:127.0.0.1:8000 you@server
# 然后浏览器打开 http://127.0.0.1:8000
```

好处：服务端仍然只听回环，网络里没人能直连；凭据是你已有的 SSH key。
代价：每次要开着这条连接。（**这是把服务放在远端时的默认建议**，不是针对某台机器的权宜之计。）

### (b) 反向代理 + 认证（要给多台设备/长期访问）

```nginx
server {
  listen 443 ssl;
  server_name bagu.example.com;
  auth_basic "bagu";
  auth_basic_user_file /etc/nginx/.htpasswd;   # ← 不加这行 = 把考场敞开给整个互联网
  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
  }
}
```

`platform_api.serve --host 127.0.0.1`（保持回环）+ nginx 负责 TLS 与认证。**不要把 8000 直接
暴露到公网**：没有认证、没有速率限制，而且它会执行你 Track 里声明的 compose/maven 命令。

### (c) 只在内网用 → 绑内网地址 + 防火墙限制来源

```bash
python -m platform_api.serve --host 10.0.0.5 --port 8000
# 再在防火墙上只放行你自己的机器
```

内网不等于安全：同网段的任何人都能做你的题、看你的历史。

> **无论哪种**：`--host 0.0.0.0` 只应当在"前面确实有代理/防火墙"时使用。

## 5. 前端放哪

两种都行：

* **开发/自用**：`cd frontend && npm run dev`（Vite 起 5173，把 `/tracks` 等前缀代理到
  `BAGU_BACKEND`——默认 `http://127.0.0.1:8000`，可改用环境变量指向别的后端）。
* **对外**：`npm run build` 产出 `frontend/dist/`，交给任意静态服务器（nginx/caddy/`http.server`）。
  前端只调同源相对路径，所以**和后端同源部署最省事**（nginx 里 `location /api` 反代 + 静态目录）。

## 6. 阅卷（真跑测试）

```bash
export BAGU_GRADING=docker      # 或 --grading docker
python -m platform_api.serve --content-dir ../seckill-lab
```

需要 **JDK17 + Maven**，以及 Track 声明的 `runtime.compose` 能在本机跑起来（Docker）。
默认**每次阅卷前重置 Track 的基础设施**（`docker compose down -v` + `up -d --wait`）——这会
**删掉数据卷**：这是必需的，否则"库里有残留索引"能让半道题白送（设计 §15 风险 6）。
不想重置就 `--grading-reset never`，但报告里会如实记 `infra.reset: false`。

## 7. Forge（真 fork / 真 PR）

```bash
export BAGU_GITEA_URL=http://<gitea>:3000
export BAGU_GITEA_TOKEN=<token>            # 需要 write:repository
export BAGU_GITEA_UPSTREAM=<owner>/<repo>  # Track 内容所在的上游仓
export BAGU_GITEA_OWNER=<你的账号>          # fork 落到谁名下
python -m platform_api.serve --content-dir ../seckill-lab --forge gitea
```

* 工作区会被配好 push 凭据（token 在**仓库外**的 0600 文件里，仓库只留 `credential.helper` 路径）；
* **push 由你自己跑**（平台不代跑 git 写操作），界面会给出命令；
* PR 从 `<你>:dev` 到上游的阶段分支，返回真 `html_url`。

## 8. 多实例 / 共库

多条后端进程共用一个 PostgreSQL 时，**必须**给每个实例不同的标识：

```bash
BAGU_INSTANCE_ID=host-a python -m platform_api.serve --store postgres --database-url ...
```

启动时平台会把自己名下"卡在 running"的阅卷记录收成 `interrupted`——不清扫别人的。同名会互相
误伤（读别人的进度）。见 `platform_api/jobs.py` 的模块 docstring。

## 9. 备份、升级、重置

* **备份**：SQLite 就复制那个文件（停服务后复制，或 `.backup` 命令）；PostgreSQL 用你惯用的方式。
* **升级**：`git pull` → `pip install -e .` → `npm run build`（前端）→ 重启后端。数据库是幂等的
  建表（`CREATE TABLE IF NOT EXISTS` + 补列），不需要手工迁移。
* **重置**：删掉 SQLite 文件（或 `TRUNCATE`）就回到"没入过学"的状态。**注意**：Track 的工作区
  目录与 Gitea 上的 fork/dev 分支不会被清掉，按需自己处理。

## 10. 一页速查

```bash
# 最小可用（本机、桩阅卷、进度存 ./bagu.sqlite3）
python -m platform_api.serve --content-dir ../seckill-lab

# 真阅卷 + 真 PR + 进度存指定文件
BAGU_GRADING=docker BAGU_FORGE=gitea \
BAGU_GITEA_URL=http://127.0.0.1:3000 BAGU_GITEA_TOKEN=... \
BAGU_GITEA_UPSTREAM=trackowner/seckill-lab BAGU_GITEA_OWNER=deployer \
python -m platform_api.serve --content-dir ../seckill-lab \
  --store sqlite --database-url /srv/bagu/progress.sqlite3

# 检查现状
curl -s localhost:8000/meta | python -m json.tool
```

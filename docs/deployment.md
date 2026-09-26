# 部署指引（bagu-trainer）

> 面向**部署者**：这套东西装在哪、各部分跑在哪台机器上、怎么让自己（或别人）打开它。
> 本文只讲部署事实与取舍，不讲设计理由——那在 [`paper-platform-design.md`](./paper-platform-design.md)。

## 0. 先记住两件事实

**事实一：平台是"一个人用的服务"，不是 SaaS。** 没有注册、没有多租户、没有账号体系；部署者
就是唯一用户（`learner=deployer`）。**它默认没有任何认证**——谁能访问到端口，谁就能做题、
看你的进度。所以"怎么对外暴露"是这个文档里最需要认真读的一节。

**事实二：只有**阅卷工具链**和**数据库**必须在后端那台机器上；工作区**不必**。** 代码写在哪儿都行
——家里笔记本、公司台式、后端那台服务器——**只要你的 `dev` 分支能 push 到你 fork 的 Gitea，而后端
能从那个 Gitea 读回来**。这条链路成立，工作区在哪就不重要（§3 讲了机制）。

| 东西 | 在哪 | 为什么 |
|---|---|---|
| **工作区**（你写代码的那份 clone） | **任何地方**。后端手里那份只是"要阅卷的那份 checkout" | diff 与阅卷都读后端手里那条路径，但它**能**是平台 clone 的、你在后端机器上登记的、或"你在别处 push 之后由后端 fetch 下来的"（提交时自动做） |
| **阅卷工具链**（JDK / Maven / Docker） | 后端那台机器上 | `--grading docker` 在后端进程里跑 `mvn test`，并 `docker compose` 起 Track 的基础设施（D3：阅卷在"学员自己的机器"上——自托管时那台就是后端这台） |
| **进度数据库**（SQLite 文件 / PostgreSQL） | 后端那台机器（或网络可达） | 默认就是后端工作目录下的一个文件 |

所以"我想在自己电脑上用 IDE 写代码、后端跑在服务器上"是**可以**的（拓扑 C），前提只是后端能读到
你的 Gitea。做不到的只有一件事：**让后端去读你另一台机器上的目录**（它不认得别人的文件系统）——
但那条路你根本不需要，push 就是那条路。

## 1. 三种拓扑，选一个

### 拓扑 A（推荐）：全部装在你自己的机器上

```
你的机器
├── platform_api.serve        ← 后端（要阅的那份 checkout、阅卷工具链、数据库都在这儿）
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

懒得抄这四行就 `bash scripts/quickstart.sh --content-dir ../seckill-lab`（建 venv、装依赖、构建前端、
起后端，最后打印要打开的地址）。它需要 Python 3.11+；若发行版把 venv 拆成了单独的包，装一下
（Debian/Ubuntu：`apt install python3-venv`），或装 [`uv`](https://docs.astral.sh/uv/)——脚本检测到
`uv` 会自动用它建 venv（那条路不需要系统的 ensurepip）。

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

**代价（必须先接受）**：工作区**在后端手里**（后端要能读到你要阅的那份代码），而你在自己电脑上写的
代码后端**看不见**。所以两条可行路径：

1. **在自己机器上写，push 到你的 fork**（推荐）：后端只要网络能到 Gitea 就行。你 push 完点提交，
   后端**会自动**把它的 checkout 快进到你的 `dev`（工作树干净时无损；见 §3）；
2. **SSH 进服务器写**（`ssh` + 编辑器 / VS Code Remote-SSH），或者把后端机器上任何可见的目录登记给
   后端（`POST /instances/{id}/workspace`）——那时 diff 连你**还没 commit** 的改动都看得见。

> 工作区不是架构边界，**"后端能不能读到你的代码"才是**：能 push + 能读回 = 代码写在哪都行。

### 拓扑 D：docker compose 一键起（后端 + Gitea + 前端，Track 自动载入）

**只想要"一条命令起全、别让我手工推分支"的，选这个。** 三个服务进容器，生命周期由 compose 串起来：
`gitea` 健康 → `gitea-admin`（建管理员）→ `track-loader`（把内容仓的分支**载入** Gitea）→ 后端 → 前端。

```bash
cd deploy && cp .env.example .env && vi .env    # 至少改 BAGU_PUBLIC_URL
docker compose up -d
# 浏览器 → http://<BAGU_PUBLIC_URL 的主机>:8080
```

它把"手工做 Gitea 那一串事"（建用户、造令牌、建仓、把每个阶段的分支推上去、让 fork 也有这些分支）
变成了启动步骤——**provision 报 `workspace_ref_missing` 这件事因此从根上消失**。
细节、约束与真阅卷的可选叠加文件见 [`../deploy/README.md`](../deploy/README.md) 与本文 §11。

**代价**：真阅卷要挂 `/var/run/docker.sock`（等于宿主机 root），且工作区在两侧必须路径一致；
`BAGU_PUBLIC_URL` 必须是容器与浏览器都能访问的地址（写 `127.0.0.1` 必踩坑，loader 会为此警告）。

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

工作区就是"后端要拿去阅卷的那份 checkout"。它有三种来源，**互不冲突**：

### (a) 平台替你准备（默认）

`POST /instances/{id}/provision` 在后端本机 clone 到 `<forge-base>/<track>/<instance-id>/`，并配好
push 凭据（token 在仓库外的 0600 文件里）与 git 身份。

### (b) 你在**另一台机器**上写（推荐，只要 push 得到）

在这台机器上 clone、写、commit、push 到你的 fork；后端不需要知道你的路径。机制是**对账**：

* 点「提交」时，后端先 `git fetch` 你的 `dev`，**在无损时自动快进**它的 checkout，然后才阅卷——
  于是"我在笔记本上写完就提交"不需要任何额外动作；
* 界面上的「从远端同步」是同一个动作的手动入口（想先自己看一眼时用）；
* 看 diff 时后端只做**只读**对账（fetch + 报数，绝不动你的分支）。远端比你新时 diff 会直说
  "你多半是在另一台机器上写的"，而不是显示"没有改动"让你以为自己的代码丢了。

"无损"的判据只有三条**同时**成立：工作树干净（没有未提交/未跟踪改动）、本地不领先远端、当前就在
`dev` 分支上。任何一条不成立，后端**一个字都不动**，而是如实报出状态（本地领先几个提交、远端领先
几个提交、工作树脏），由你决定怎么办——平台**不替学员合并、不 stash、不 reset**。

哪一版被阅了是**可审计**的：报告里 `provenance.host_env.revision` 是那次阅卷对着的 commit SHA。

### (c) 你有一个目录就在后端机器上（指引式）

界面给出三行可照抄的命令，你在后端机器上（或它的文件系统里）拉一份，再把路径登记回来
（`POST /instances/{id}/workspace`），此后 diff 与阅卷都对着它跑；`{"path": null}` 撤销登记，
回到平台准备的那份。好处是 diff 能看见你**还没 commit** 的改动；代价是路径必须是后端这台机器上能
看到的目录，否则得到 `422 workspace_path_invalid`（`reason` 说清是"目录不存在"还是"不是 git 仓库"）。

```bash
git clone http://<gitea>/<you>/<repo>.git ~/my-bagu-stage-01
cd ~/my-bagu-stage-01
git checkout -b dev origin/stage-01-product-query-index
```

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
* **升级后一定要重启后端，并核对版本号**（前端与后端不是同一次部署是自托管下最常见的一种坏状态）：

  ```bash
  curl -s localhost:8000/meta | python -m json.tool   # 看 platform_version
  ```

  主页「这台服务」里会把**前端版本**和**后端版本**并排显示，不一致时给一条红框说明。症状通常是
  「后端没有这个接口」（HTTP 404，`detail` 是 Starlette 自己的 `Not Found`，不在平台的短码表里）
  或响应少一个字段——**遇到这种报错先看版本行**，不要从代码里找 bug。
* **重置**：删掉 SQLite 文件（或 `TRUNCATE`）就回到"没入过学"的状态。**注意**：Track 的工作区
  目录与 Gitea 上的 fork/dev 分支不会被清掉，按需自己处理。

## 10. 一页速查

```bash
# 最小可用（本机、桩阅卷、进度存 ./bagu.sqlite3）
bash scripts/quickstart.sh --content-dir ../seckill-lab      # 等价于下面这条 + 建 venv/装依赖/构建前端
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

## 11. docker compose 一键起（拓扑 D 的细节）

`deploy/` 里是一套能 `up` 的栈：**Gitea + 后端 + 前端**，并且**启动时自动把 Track 载入 Gitea**。

```bash
cd deploy
cp .env.example .env && vi .env      # BAGU_PUBLIC_URL 改成这台机器的可达地址（别用 127.0.0.1）
docker compose up -d
docker compose logs -f track-loader  # 看它往 Gitea 里推了哪些阶段分支
```

**启动顺序就是它解决的问题**（每一步都在修一个真实的坑）：

```
gitea(healthy) ──► gitea-admin(跑完)  ──┐
                                       ├──► track-loader(跑完) ──► platform(healthy) ──► web
gitea(healthy) ────────────────────────┘
```

| 服务 | 做什么 | 为什么必须这样 |
|---|---|---|
| `gitea` | 官方 `gitea/gitea:1.22` + sqlite + `INSTALL_LOCK` | —— |
| `gitea-admin` | 一次性 `gitea admin user create`（与 gitea 共用数据卷） | 官方镜像**不认** `GITEA_ADMIN_*`：实测 1.22 带着那些变量启动后容器健康，但 `GET /api/v1/user` 回 401 `user does not exist` |
| `track-loader` | 建用户/仓 → **推全**内容仓的分支 → 建/补齐 fork → 令牌写进共享卷 | Gitea 的 fork 是**创建那一刻**的快照，不会跟着上游长。顺序错了，"新阶段在 fork 里永远缺席"，症状就是 `workspace_ref_missing` |
| `platform` | 后端；`depends_on: track-loader: service_completed_successfully` | 保证"Gitea 里已经有它要 clone 的阶段分支"再开始接客 |
| `web` | nginx：SPA + REST 反代（前缀表与 `frontend/vite.config.ts` 一致） | 后端是**纯 REST**、不托管前端；少了它 `up` 完没有浏览器入口 |

**三条约束**：

1. `BAGU_PUBLIC_URL` 必须**容器与浏览器都连得上**（它决定 `clone_url` 与 PR 链接）。写 `127.0.0.1`
   的话，容器里那是它自己——`provision` 的 clone 会一直超时。loader 启动时会为此打一条 `!!` 警告。
2. 内容仓（`CONTENT_DIR`）**必须有每个阶段的本地分支**（§C.5 一阶段一 ref）。loader 只推它看得见的：
   `git branch` 里有几条，Gitea 里就有几条。换机器用 `git bundle create x.bundle --all` 打包——
   本地分支才是内容的全部（GitHub 上常常只有 `main`）。
3. `BAGU_FORK_RESET=1` 是**破坏性**的：删掉学员 fork 再重建（`dev` 与历史 PR 一起没）。它的用处正是
   "把 dev 上不该有的东西（比如一份参考解）清掉"。

**真阅卷**（可选）：基础 compose 固定桩阅卷，真阅卷由**叠加文件**开（唯一开关，见
[`../deploy/README.md`](../deploy/README.md)）：

```bash
docker compose -f docker-compose.yml -f docker-compose.grading.yml build platform
docker compose -f docker-compose.yml -f docker-compose.grading.yml up -d
```

叠加文件做四件事：镜像换成 `target: grading`（JDK17 + Maven + docker CLI/compose，工具链从官方
镜像拷而不是 apt——包名在各 base 镜像上不一致，实测踩过 `Unable to locate package`）；挂 docker
socket（= 宿主机 root 权限，只在你自己的机器上这么做）；工作区**绑定挂载**成与宿主机一致的路径
（grading 把 `docker compose -f <工作区>/docker/docker-compose.yml` 交给宿主机守护进程，相对路径
`./mysql/init` 按宿主机解析，命名卷的 `/var/lib/docker/volumes/...` 对不上）；把 `DB_HOST`/
`REDIS_HOST` 指到宿主机——maven 在容器里跑而 MySQL/Redis 发布在宿主机上，容器里的 `localhost`
是它自己。

**数据**：卷 `gitea-data`（Gitea）、`secrets`（后端令牌）、宿主目录 `${BAGU_DATA_DIR}`（进度库 +
工作区，绑定挂载）。备份进度就是复制那个目录里的 `progress.sqlite3`；`docker compose down` 保留
数据，`down -v` 连卷一起删（宿主目录要自己清）。

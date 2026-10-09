"""把一份 Track 内容**载入**一个刚起来的 Gitea（compose 栈的启动步骤）。

存在的理由：平台要 `provision` 一个阶段，就得能从 Forge 里 clone 到**那个阶段的分支**（§C.5
一阶段一 ref）。手工做这件事有一串容易漏的步骤——建用户、造令牌、建仓、把内容仓的每条本地分支推
上去、给学员写权限——而**每一步漏掉的症状都是同一个**：provision 回 ``workspace_ref_missing``，
看起来像 Track 写错了。这个模块把这些步骤收成一次幂等的启动动作。

三条设计决定（都是被现实逼出来的）：

* **管理员只能靠 Gitea CLI 建**。官方镜像不认 ``GITEA_ADMIN_*`` 环境变量（实测 1.22：容器健康、
  但 ``GET /api/v1/user`` 401 "user does not exist"），所以 compose 里有一个用同一个镜像与同一个
  数据卷跑 ``gitea admin user create`` 的一次性 sidecar；本模块只假定"管理员已经能用 basic auth
  登录"（并轮询等它），不去碰 CLI。
* **没有 fork**（L3）。早期照搬 GitHub 的 ``fork → PR``，实测是纯负债：fork 是创建那一刻的快照
  （Track 后来长出新阶段，旧 fork 里永远缺席 ⇒ ``workspace_ref_missing``）、Gitea 给程序化 fork
  标 ``empty``（分支 API 全 404）、学员第一次 push 会抢走 fork 的默认分支。现在只有一个仓：内容
  推上去，学员**直接 push ``dev``**——所以必须给他写权限（``BAGU_LEARNER_WRITE``，默认开）。
* **令牌复用**。每次启动都造一个新令牌，几轮下来 Gitea 里会躺一排 `all` scope 的凭据（真发生过：
  13 个）。这里先试 ``secrets`` 里那份，能用就不动它。

只依赖 stdlib + httpx + git CLI；``fetch`` 与 HTTP 都集中在少数几个函数里，便于用 respx 测。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

__all__ = ["LoaderConfig", "LoadResult", "load_track", "local_branches", "push_all_branches"]

#: 学员与内容所有者的默认口令。compose 栈是**单机自用**的：这些账号只在容器网络与 localhost
#: 上可达（Gitea 的 3000 端口是否对外发布由部署者决定），所以默认值刻意简单；要改就改 .env。
DEFAULT_ADMIN_USER = "baguadmin"
DEFAULT_ADMIN_PASSWORD = "baguadmin123"
DEFAULT_TRACK_OWNER = "trackowner"
DEFAULT_TRACK_OWNER_PASSWORD = "trackowner123"
DEFAULT_LEARNER = "deployer"
DEFAULT_LEARNER_PASSWORD = "deployer123"

#: 令牌落点（compose 用一个共享卷把它交给后端容器）。
TOKEN_FILENAME = "gitea-token"
#: 内容所有者那枚令牌的落点。只给 loader 自己用（建仓、推分支），后端不需要它。
OWNER_TOKEN_FILENAME = "gitea-owner-token"


class LoadError(RuntimeError):
    """载入失败。措辞面向**部署者**：说清哪一步、以及他能怎么查。"""


@dataclass(frozen=True)
class LoaderConfig:
    gitea_url: str
    content_dir: Path
    secrets_dir: Path
    upstream_owner: str = DEFAULT_TRACK_OWNER
    upstream_name: str = "track"
    learner: str = DEFAULT_LEARNER
    admin_user: str = DEFAULT_ADMIN_USER
    admin_password: str = DEFAULT_ADMIN_PASSWORD
    owner_password: str = DEFAULT_TRACK_OWNER_PASSWORD
    learner_password: str = DEFAULT_LEARNER_PASSWORD
    #: 给学员内容仓的**写权限**（默认开）。没有 fork 之后这是必须的：学员要把 ``dev`` 直接
    #: push 到这个仓。设 0 只适合"我另有办法让学员 push"的部署——那时 provision 会 clone 到
    #: 一个他推不上去的仓。
    learner_write: bool = True
    token_name: str = "track-loader"
    timeout_s: float = 120.0

    @property
    def upstream(self) -> str:
        return f"{self.upstream_owner}/{self.upstream_name}"

    @property
    def token_path(self) -> Path:
        return self.secrets_dir / TOKEN_FILENAME

    @property
    def owner_token_path(self) -> Path:
        return self.secrets_dir / OWNER_TOKEN_FILENAME

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> LoaderConfig:
        """从环境变量读配置（compose 就是这么传的）。缺关键项直接报错，不猜。"""
        e = os.environ if env is None else env

        def need(name: str) -> str:
            value = e.get(name, "").strip()
            if not value:
                raise LoadError(f"缺少环境变量 {name}")
            return value

        def off(name: str) -> bool:
            """默认**开**的开关：只在显式写了假值时才关。"""
            return e.get(name, "").strip().lower() in ("0", "false", "no", "off")

        content = Path(need("BAGU_CONTENT_DIR"))
        if not (content / ".git").exists() and not (content / "HEAD").exists():
            raise LoadError(f"BAGU_CONTENT_DIR 不是一个 git 仓库（或 bare 仓库）：{content}")
        return cls(
            gitea_url=need("BAGU_GITEA_URL"),
            content_dir=content,
            secrets_dir=Path(e.get("BAGU_SECRETS_DIR", "/run/bagu")).expanduser(),
            upstream_owner=e.get("BAGU_TRACK_OWNER", DEFAULT_TRACK_OWNER).strip(),
            upstream_name=e.get("BAGU_TRACK_NAME", "track").strip(),
            learner=e.get("BAGU_LEARNER", DEFAULT_LEARNER).strip(),
            admin_user=e.get("BAGU_ADMIN_USER", DEFAULT_ADMIN_USER).strip(),
            admin_password=e.get("BAGU_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD),
            owner_password=e.get("BAGU_TRACK_OWNER_PASSWORD", DEFAULT_TRACK_OWNER_PASSWORD),
            learner_password=e.get("BAGU_LEARNER_PASSWORD", DEFAULT_LEARNER_PASSWORD),
            learner_write=not off("BAGU_LEARNER_WRITE"),
        )


@dataclass
class LoadResult:
    upstream: str = ""
    branches: list[str] = field(default_factory=list)
    token_path: Path | None = None
    token_reused: bool = False
    learner_has_write: bool = False
    #: 这次**新加**的分支保护规则名（已经保护过的不重复列）。
    protected_branches: list[str] = field(default_factory=list)

    def render(self) -> str:
        if self.learner_has_write:
            write = "（学员有写权限）"
        else:
            write = "（学员**没有**写权限：他 push 不了）"
        lines = [
            f"内容仓: {self.upstream}" + write,
            f"载入阶段分支（{len(self.branches)}）: " + ", ".join(self.branches),
            f"后端令牌: {self.token_path}"
            + ("（复用已有）" if self.token_reused else "（本次新造）"),
        ]
        return "\n".join(lines)


# --- HTTP 小工具（集中在这里，便于用 respx 覆盖）-----------------------------------


def _client(base_url: str, *, token: str | None = None, auth: tuple[str, str] | None = None,
            timeout_s: float = 30.0) -> httpx.Client:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"token {token}"
    return httpx.Client(base_url=f"{base_url.rstrip('/')}/api/v1", headers=headers, auth=auth,
                        timeout=timeout_s)


def wait_for_healthy(gitea_url: str, *, timeout_s: float) -> None:
    """等 ``/api/healthz`` 通过。compose 的 depends_on 已经等了，这里是第二道保险。"""
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{gitea_url.rstrip('/')}/api/healthz", timeout=5.0)
            if response.status_code == 200 and '"pass"' in response.text:
                return
            last = f"{response.status_code} {response.text[:120]}"
        except httpx.HTTPError as exc:
            last = str(exc)
        time.sleep(2.0)
    raise LoadError(f"Gitea 没在 {timeout_s:.0f}s 内就绪（最后看到：{last}）")


def wait_for_admin(gitea_url: str, user: str, password: str, *, timeout_s: float) -> None:
    """等管理员能用 basic auth 登录——也就是那个 CLI sidecar 真的把用户建出来了。"""
    deadline = time.monotonic() + timeout_s
    last = ""
    with _client(gitea_url, auth=(user, password)) as client:
        while time.monotonic() < deadline:
            response = client.get("/user")
            if response.status_code == 200:
                return
            last = f"{response.status_code} {response.text[:120]}"
            time.sleep(2.0)
    raise LoadError(
        f"管理员 {user!r} 登录不了（最后看到：{last}）。检查 gitea-admin 那个一次性服务是否成功"
        "（它跑的是 `gitea admin user create`），以及 BAGU_ADMIN_* 与 .env 是否一致。"
    )


def ensure_user(gitea_url: str, admin: tuple[str, str], user: str, password: str, *,
                admin_flag: bool = False) -> bool:
    """建用户（已存在 ⇒ 返回 ``False``，不是错误）。返回是否**新建**。"""
    payload = {
        "username": user,
        "password": password,
        "email": f"{user}@example.com",
        "must_change_password": False,
    }
    if admin_flag:
        payload["login_name"] = user
    with _client(gitea_url, auth=admin) as client:
        response = client.post("/admin/users", json=payload)
        if response.status_code in (201, 200):
            return True
        if response.status_code in (409, 422):  # 已存在：幂等路径
            return False
        raise LoadError(
            f"建用户 {user!r} 失败：{response.status_code} {response.text[:200]}"
        )


def valid_token(gitea_url: str, token: str) -> bool:
    """这个令牌现在还能用吗（决定"复用还是新造"）。"""
    if not token:
        return False
    try:
        with _client(gitea_url, token=token) as client:
            return client.get("/user").status_code == 200
    except httpx.HTTPError:
        return False


def mint_token(gitea_url: str, user: str, password: str, name: str) -> str:
    with _client(gitea_url, auth=(user, password)) as client:
        response = client.post(f"/users/{user}/tokens", json={"name": name, "scopes": ["all"]})
        if response.status_code not in (200, 201):
            raise LoadError(
                f"给 {user!r} 造令牌失败：{response.status_code} {response.text[:200]}"
            )
        payload = response.json()
    token = str(payload.get("sha1") or payload.get("token") or "")
    if not token:
        raise LoadError(f"Gitea 没回令牌（响应：{json.dumps(payload)[:200]}）")
    return token


def token_for(
    gitea_url: str, user: str, password: str, name: str, *, path: Path
) -> tuple[str, bool]:
    """拿一枚**能用的**令牌，并把它存在 ``path``；返回 ``(token, 是否复用了旧的)``。

    为什么必须"先试旧的"：Gitea 不允许同名令牌，而 loader 每次 ``compose up`` 都会跑。
    固定名字 + 每次都造 = 第二次启动直接 400 ``access token name has been used already``，
    于是 ``track-loader`` 退出非 0，``platform`` 起不来——**重启整个栈都会失败**。
    （这不是假想：`docker compose up` 在没清卷的栈上就是这么挂的。）

    旧令牌不可用（Gitea 数据被清过）时才造新的，名字带时间戳，免得再撞上一个残留同名令牌。
    令牌越攒越多也是这个道理：能用就复用。
    """
    existing = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if valid_token(gitea_url, existing):
        return existing, True
    token = mint_token(gitea_url, user, password, f"{name}-{int(time.time())}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    path.chmod(0o600)
    return token, False


def ensure_repo(gitea_url: str, token: str, owner: str, name: str) -> bool:
    """确保 ``owner/name`` 存在（用 owner 自己的令牌建）。返回是否**新建**。"""
    with _client(gitea_url, token=token) as client:
        if client.get(f"/repos/{owner}/{name}").status_code == 200:
            return False
        response = client.post(
            "/user/repos", json={"name": name, "auto_init": False, "private": False}
        )
        if response.status_code in (201, 200):
            return True
        if response.status_code in (409, 422):
            return False
        raise LoadError(
            f"建仓 {owner}/{name} 失败：{response.status_code} {response.text[:200]}"
        )


def add_collaborator(
    gitea_url: str, token: str, owner: str, name: str, user: str, *, permission: str = "write"
) -> bool:
    """把 ``user`` 加成 ``owner/name`` 的协作者（默认 write）。

    **没有 fork 之后这条是必需的**：学员要把 ``dev`` 直接 push 到这个仓（后端也从它 clone），
    所以他对内容仓必须有写权限。少这一步的症状很好认——他 push 时被 401/403 拒，而平台侧一切正常。
    单用户自托管下两个账号本来就是同一个人（``BAGU_LEARNER_WRITE=0`` 可关）。
    """
    with _client(gitea_url, token=token) as client:
        response = client.put(
            f"/repos/{owner}/{name}/collaborators/{user}", json={"permission": permission}
        )
        return response.status_code in (200, 201, 204)


def ensure_default_branch(
    gitea_url: str, token: str, owner: str, name: str, branch: str
) -> bool:
    """把默认分支钉在 ``branch`` 上；改了才返回 ``True``。

    为什么必须做：Gitea 会把它认为"空"的仓的**第一条被推上去的分支**设成默认分支。学员的第一次
    ``git push -u origin dev`` 正好满足这个条件——于是 ``dev`` 变成默认分支，此后想删掉它
    （重开一题、或换一条干净的 dev）会被 Gitea 以
    ``Branch: dev is the default branch ... and cannot be deleted`` 拒绝。
    （真踩过：我探权限用的 `_probe` 分支就是这么成了默认分支，删都删不掉。）
    """
    with _client(gitea_url, token=token) as client:
        info = client.get(f"/repos/{owner}/{name}")
        if info.status_code != 200:
            return False
        current = str(info.json().get("default_branch") or "")
        if current == branch:
            return False
        response = client.patch(f"/repos/{owner}/{name}", json={"default_branch": branch})
        if response.status_code not in (200, 201):
            raise LoadError(
                f"把 {owner}/{name} 的默认分支设成 {branch} 失败："
                f"{response.status_code} {response.text[:200]}"
            )
        return True


# --- git ---------------------------------------------------------------------------


def _post_protection(
    gitea_url: str,
    token: str,
    owner: str,
    name: str,
    payload: dict[str, object],
    *,
    log=print,
) -> bool:
    """发一条保护规则：新建返回 ``True``，已存在 / 失败都返回 ``False``。

    两条实测出来的细节：

    * Gitea **1.22 对"同名规则已存在"回的是 403** + ``{"message":"Branch protection already
      exist"}``（不是 409/422）——所以按消息判断，别赌状态码；
    * **保护失败绝不抛错**：它是加固，不是功能。`platform` 依赖 loader "成功退出"，一旦这里把
      loader 弄成 exit 1，整个栈就起不来（真踩过：第二天重跑 loader，403 让 platform 压根没被创建，
      界面全挂）。所以这里只打一条 ``!!`` 警告然后继续。
    """
    with _client(gitea_url, token=token) as client:
        response = client.post(f"/repos/{owner}/{name}/branch_protections", json=payload)
    if response.status_code in (200, 201):
        return True
    if response.status_code in (409, 422) or "already exist" in response.text.lower():
        return False  # 已经保护过了
    log(
        f"   !! 给 {payload.get('branch_name')} 加保护失败（跳过，不影响启动）："
        f"{response.status_code} {response.text[:160]}"
    )
    return False


def content_protection(branch: str, *, allow_push: str | None) -> dict[str, object]:
    """内容分支的规则：只有 ``allow_push`` 能推、能 force（**loader 自己要 force**）。"""
    return {
        "branch_name": branch,
        "enable_push": allow_push is not None,
        "enable_push_whitelist": allow_push is not None,
        "push_whitelist_usernames": [allow_push] if allow_push else [],
        # loader 每次都按内容仓**强制**对齐这些分支（内容会被重写），所以 force 只开给他一个。
        "enable_force_push": allow_push is not None,
        "enable_force_push_whitelist": allow_push is not None,
        "force_push_whitelist_usernames": [allow_push] if allow_push else [],
    }


def dev_protection() -> dict[str, object]:
    """``dev`` 的规则：有写权限的人都能推，但**谁都不能 force**（重写 dev 的代价见 load_track）。"""
    return {
        "branch_name": "dev",
        "enable_push": True,
        "enable_push_whitelist": False,
        "push_whitelist_usernames": [],
        "enable_force_push": False,
    }


def protect_branches(
    gitea_url: str,
    token: str,
    owner: str,
    name: str,
    *,
    content_branches: list[str],
    allow_push: str | None,
    log=print,
) -> list[str]:
    """保护**内容分支**与学员那条 ``dev``，返回本次新加的规则名。

    * 内容分支（``stage-*`` / ``main`` / ``solutions``）：只有 ``allow_push``（内容所有者）能推。
      它们就是平台的**基线**：diff 的基准、判定面、"阶段分支 = 上一阶段 + 本阶段测试"全靠它们。
      学员（有写权限）一旦推上去，平台就会拿他刚写的东西当"内容作者的版本"——真踩过：一次提交被
      推到了 ``stage-02`` 分支上，基线当场变成他自己的代码。
    * ``dev``：**允许推但禁止 force**。重写 ``dev`` 的代价是双份的——平台那份 checkout 与它分叉
      （对账只能如实说"不无损"，不会替你 merge），而学员上一关已经交过卷的提交就从分支上消失了
      （也真踩过，只能靠留档和 fork 里那份捞回来）。

    幂等：重复跑只会看到"已经保护过"。**任何一条失败都只告警不抛错**——`platform` 依赖 loader
    成功退出，把加固步骤做成致命错误等于"一次 API 抖动 = 整栈起不来"。
    """
    added: list[str] = []
    for branch in content_branches:
        if _post_protection(
            gitea_url, token, owner, name, content_protection(branch, allow_push=allow_push),
            log=log,
        ):
            added.append(branch)
    if _post_protection(gitea_url, token, owner, name, dev_protection(), log=log):
        added.append("dev")
    if added:
        log(
            f"   新加保护：{', '.join(added)}"
            f"（内容分支只允许 {allow_push} 推；dev 允许推、禁止 force）"
        )
    else:
        log("   分支保护已就位（内容分支只允许所有者推；dev 允许推、禁止 force）")
    return added


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise LoadError(f"git {' '.join(args)} 失败（在 {repo}）：{proc.stderr.strip()[:300]}")
    return proc


def local_branches(content_dir: Path) -> list[str]:
    """内容仓里的**本地**分支名（排序）。

    这就是"Gitea 里会有什么"的唯一来源：一阶段一 ref 的契约要求每个阶段都有自己的分支，而平台
    只推它看得见的东西。所以"拉不出工作区"的第一件事就是 ``git branch`` 看一眼这里。
    """
    out = _git(content_dir, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout
    return sorted(line.strip() for line in out.splitlines() if line.strip())


def push_all_branches(content_dir: Path, push_url: str, *, force: bool = True) -> list[str]:
    """把内容仓的**所有本地分支**推到 ``push_url``（对着同一个仓的裸 URL）。

    ``--force`` 是必须的：Track 内容会被重写（我们踩过：两条不相交的 lineage，不 force 直接被拒），
    而内容是**权威来源**——它的分支就该是 Forge 里的分支。``safe.directory`` 是给容器里"挂进来的
    目录属主和当前用户不一致"准备的，否则 git 会以 dubious ownership 拒绝操作。
    """
    _git(
        content_dir,
        "config",
        "--global",
        "--add",
        "safe.directory",
        str(content_dir),
        check=False,
    )
    branches = local_branches(content_dir)
    if not branches:
        raise LoadError(f"内容仓没有任何本地分支：{content_dir}")
    refspecs = [f"{'+' if force else ''}refs/heads/{b}:refs/heads/{b}" for b in branches]
    _git(content_dir, "push", "--quiet", push_url, *refspecs)
    return branches


def token_push_url(gitea_url: str, owner: str, name: str, token: str) -> str:
    """带令牌的 HTTP push URL（只在容器内部用一次，不落盘到工作区）。"""
    parsed = httpx.URL(gitea_url)
    host = parsed.netloc.decode() if isinstance(parsed.netloc, bytes) else parsed.netloc
    scheme = parsed.scheme or "http"
    return f"{scheme}://{token}@{host}/{owner}/{name}.git"


def remote_branches(push_url: str) -> list[str]:
    """远端现有分支（``git ls-remote --heads``）。

    **权威来源是 git，不是 Gitea 的 API**：Gitea 对"它认为空"的仓（程序化建出来的 fork 就是
    这样）会一直回 ``empty``，它的 ``/branches`` 对任何分支都 404，而 ``git ls-remote`` 一直是对的
    ——provision 走的也是后者。用 API 判断"分支在不在"会得出与真相相反的结论。
    """
    proc = subprocess.run(
        ["git", "ls-remote", "--heads", push_url],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise LoadError(f"git ls-remote 失败：{proc.stderr.strip()[:200]}")
    names = []
    for line in proc.stdout.splitlines():
        if "\t" in line:
            ref = line.split("\t", 1)[1].strip()
            if ref.startswith("refs/heads/"):
                names.append(ref[len("refs/heads/"):])
    return sorted(names)


def wait_for_remote_branches(
    push_url: str, expected: list[str], *, timeout_s: float = 120.0, log=print
) -> list[str]:
    """等远端长出 ``expected`` 里的全部分支（push 之后 Gitea 处理钩子需要一点时间）。

    超时不抛错：调用方据此决定"补推一次"，比在这里猜更有信息量。
    """
    deadline = time.monotonic() + timeout_s
    remote: list[str] = []
    while time.monotonic() < deadline:
        remote = remote_branches(push_url)
        if set(expected) <= set(remote):
            return remote
        time.sleep(2.0)
    log(f"   等了 {timeout_s:.0f}s，远端仍缺：{sorted(set(expected) - set(remote))}")
    return remote


# --- 编排 --------------------------------------------------------------------------


def load_track(config: LoaderConfig, *, log=print) -> LoadResult:
    """把内容载入 Gitea：建账号 → 建仓 → 推全部分支 → 给学员写权限 → 落令牌。

    幂等：compose 每次 ``up`` 都会再跑一遍，重复执行只该产生"已存在"这种无害结果。
    """
    result = LoadResult(upstream=config.upstream)
    config.secrets_dir.mkdir(parents=True, exist_ok=True)

    # 这个坑值得在**启动时**就喊出来，而不是等 provision 超时：容器里的 127.0.0.1 是容器自己。
    host = (httpx.URL(config.gitea_url).host or "").strip()
    if host in ("127.0.0.1", "localhost", "::1"):
        log(
            f"!! GITEA_URL 指向 {host}：容器里的 127.0.0.1 是**它自己**，后端 clone 会超时。\n"
            "   把 .env 的 BAGU_PUBLIC_URL 换成这台机器在局域网里的地址（hostname -I 的第一个）——\n"
            "   它必须同时是你浏览器和容器都能访问到的地址。"
        )

    log(f"== 等 Gitea 就绪：{config.gitea_url}")
    wait_for_healthy(config.gitea_url, timeout_s=config.timeout_s)
    admin = (config.admin_user, config.admin_password)
    wait_for_admin(config.gitea_url, *admin, timeout_s=config.timeout_s)
    log(f"   管理员 {config.admin_user} 可用")

    log("== 账号 ==")
    for user, password in ((config.upstream_owner, config.owner_password),
                           (config.learner, config.learner_password)):
        created = ensure_user(config.gitea_url, admin, user, password)
        log(f"   {user}: {'新建' if created else '已存在'}")

    owner_token, owner_reused = token_for(
        config.gitea_url,
        config.upstream_owner,
        config.owner_password,
        "track-loader-owner",
        path=config.owner_token_path,
    )
    learner_token, learner_reused = token_for(
        config.gitea_url,
        config.learner,
        config.learner_password,
        config.token_name,
        path=config.token_path,
    )
    log(
        "   令牌：owner "
        f"{'复用' if owner_reused else '新造'} · 学员 {'复用' if learner_reused else '新造'}"
    )

    log("== 上游仓 + 载入阶段分支 ==")
    created = ensure_repo(
        config.gitea_url, owner_token, config.upstream_owner, config.upstream_name
    )
    log(f"   仓 {config.upstream}: {'新建' if created else '已存在'}")
    branches = push_all_branches(
        config.content_dir,
        token_push_url(config.gitea_url, config.upstream_owner, config.upstream_name, owner_token),
    )
    result.branches = branches
    # 推完**用 git 核对**（不靠 Gitea 的 /branches：见 remote_branches 的 docstring）。
    landed = wait_for_remote_branches(
        token_push_url(config.gitea_url, config.upstream_owner, config.upstream_name, owner_token),
        branches,
        timeout_s=config.timeout_s,
        log=log,
    )
    missing = sorted(set(branches) - set(landed))
    if missing:
        raise LoadError(
            f"内容仓里缺这些分支：{missing}（推上去了但 git 读不到——检查 Gitea 日志与磁盘空间）"
        )
    log(f"   推入 {len(branches)} 条分支，git 确认全部就位：{', '.join(branches)}")

    log("== 学员写权限（没有 fork 了：他直接 push 到这个仓）==")
    if config.learner_write:
        add_collaborator(
            config.gitea_url,
            owner_token,
            config.upstream_owner,
            config.upstream_name,
            config.learner,
        )
        result.learner_has_write = True
        log(f"   {config.learner} 已加为 {config.upstream} 的协作者（可 push dev）")
    else:
        log("   BAGU_LEARNER_WRITE=0：没给他写权限（他得另想办法 push）")

    log("== 保护内容分支（学员只能推 dev）==")
    # 学员对内容仓有写权限（他要把 dev 推上来），而 `stage-*` / `main` / `solutions` 是**内容作者的
    # 版本**——平台的基线、判定面、diff 基准全部以它们为准。学员一旦推上去（手滑的
    # `git push origin --all` 就够），基线的语义当场就变了：平台会拿他刚写的东西当"内容作者的版本"，
    # diff 变空、判定面对不上。真踩过：学员把一次提交推到了 stage-02 分支上。
    # 所以：**内容分支只允许内容所有者推**；`dev` 允许推但**不允许 force**（重写 dev 会让平台的
    # checkout 与它分叉，且学员自己上一关的提交就从分支上消失了——也真踩过）。
    protected = protect_branches(
        config.gitea_url,
        owner_token,
        config.upstream_owner,
        config.upstream_name,
        content_branches=branches,
        allow_push=config.upstream_owner,
        log=log,
    )
    result.protected_branches = protected
    result.token_path = config.token_path
    result.token_reused = learner_reused
    log(f"== 后端令牌：{result.token_path}（{'复用已有' if learner_reused else '本次新造'}）")
    return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m platform_api.track_loader",
        description="把一份 Track 内容载入刚起来的 Gitea（compose 栈的启动步骤）。",
    )
    parser.add_argument("--gitea-url", default=None, help="也可用 BAGU_GITEA_URL")
    parser.add_argument("--content-dir", default=None, help="也可用 BAGU_CONTENT_DIR")
    parser.add_argument("--secrets-dir", default=None, help="令牌落点，也可用 BAGU_SECRETS_DIR")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.gitea_url:
        env["BAGU_GITEA_URL"] = args.gitea_url
    if args.content_dir:
        env["BAGU_CONTENT_DIR"] = args.content_dir
    if args.secrets_dir:
        env["BAGU_SECRETS_DIR"] = args.secrets_dir
    try:
        config = LoaderConfig.from_env(env)
    except LoadError as exc:
        print(f"配置有误：{exc}", file=sys.stderr)
        return 2
    try:
        result = load_track(config)
    except LoadError as exc:
        print(f"载入失败：{exc}", file=sys.stderr)
        return 1
    print(result.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

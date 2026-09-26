"""把一份 Track 内容**载入**一个刚起来的 Gitea（compose 栈的启动步骤）。

存在的理由：平台要 `provision` 一个阶段，就得能从 Forge 里 clone 到**那个阶段的分支**（§C.5
一阶段一 ref）。手工做这件事有一串容易漏的步骤——建用户、造令牌、建上游仓、把内容仓的每条本地
分支推上去、确保学员的 fork 里也有这些分支——而**每一步漏掉的症状都是同一个**：provision 回
``workspace_ref_missing``，看起来像 Track 写错了。这个模块把这些步骤收成一次幂等的启动动作。

三条设计决定（都是被现实逼出来的）：

* **管理员只能靠 Gitea CLI 建**。官方镜像不认 ``GITEA_ADMIN_*`` 环境变量（实测 1.22：容器健康、
  但 ``GET /api/v1/user`` 401 "user does not exist"），所以 compose 里有一个用同一个镜像与同一个
  数据卷跑 ``gitea admin user create`` 的一次性 sidecar；本模块只假定"管理员已经能用 basic auth
  登录"（并轮询等它），不去碰 CLI。
* **fork 要在上游分支推完之后才存在**。Gitea 的 fork 是**创建那一刻**的快照，不会自动跟着上游长；
  所以顺序是「推上游 → 建/更新 fork」。已经存在的 fork 也要补推分支（否则新阶段在 fork 里永远缺席，
  而这正是"看得到题、拉不出工作区"的来源）。``fork_reset`` 更进一步：删掉重建，顺带清掉学员那条
  可能装着答案的 ``dev``。
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
    fork_reset: bool = False
    token_name: str = "track-loader"
    timeout_s: float = 120.0

    @property
    def upstream(self) -> str:
        return f"{self.upstream_owner}/{self.upstream_name}"

    @property
    def token_path(self) -> Path:
        return self.secrets_dir / TOKEN_FILENAME

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> LoaderConfig:
        """从环境变量读配置（compose 就是这么传的）。缺关键项直接报错，不猜。"""
        e = os.environ if env is None else env

        def need(name: str) -> str:
            value = e.get(name, "").strip()
            if not value:
                raise LoadError(f"缺少环境变量 {name}")
            return value

        def flag(name: str) -> bool:
            return e.get(name, "").strip().lower() in ("1", "true", "yes", "on")

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
            fork_reset=flag("BAGU_FORK_RESET"),
        )


@dataclass
class LoadResult:
    upstream: str = ""
    fork: str = ""
    branches: list[str] = field(default_factory=list)
    token_path: Path | None = None
    token_reused: bool = False
    fork_recreated: bool = False

    def render(self) -> str:
        lines = [
            f"上游仓: {self.upstream}",
            f"学员 fork: {self.fork}" + ("（本次重建，dev 已清空）" if self.fork_recreated else ""),
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


def ensure_fork(gitea_url: str, token: str, upstream: str, fork_owner: str, name: str) -> bool:
    """确保 fork 存在（已存在 ⇒ ``False``）。fork 是**创建那一刻**的快照，见模块 docstring。

    注意 202：Gitea 对 fork 是**异步**的，202 只说明"接受了"。没同步完的 fork 会被标成
    ``empty: true``，此时它的 ``/branches`` 对**任何**分支都回 404（实测 1.22）——所以既不能拿
    API 判断分支在不在，也不该在这时往里 push。就绪与否一律用 :func:`remote_branches` 问 git。
    """
    owner, name_ = upstream.split("/", 1)
    with _client(gitea_url, token=token) as client:
        if client.get(f"/repos/{fork_owner}/{name_}").status_code == 200:
            return False
        response = client.post(f"/repos/{owner}/{name_}/forks", json={})
        # 202 = Gitea 接受 fork 请求、异步建（真机就是这个码——一开始只认 200/201，于是
        # 明明建成了却报 "建 fork 失败：202"，一个纯粹的误报）。
        if response.status_code in (200, 201, 202, 409, 422):
            return True
        raise LoadError(
            f"建 fork {upstream} → {fork_owner} 失败：{response.status_code} {response.text[:200]}"
        )


def delete_repo(gitea_url: str, token: str, owner: str, name: str) -> None:
    with _client(gitea_url, token=token) as client:
        response = client.delete(f"/repos/{owner}/{name}")
        if response.status_code not in (204, 202, 200, 404):
            raise LoadError(
                f"删仓 {owner}/{name} 失败：{response.status_code} {response.text[:200]}"
            )


def delete_branch(gitea_url: str, token: str, owner: str, name: str, branch: str) -> bool:
    with _client(gitea_url, token=token) as client:
        response = client.delete(f"/repos/{owner}/{name}/branches/{branch}")
        return response.status_code in (204, 200)


# --- git ---------------------------------------------------------------------------


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

    **权威来源是 git，不是 Gitea 的 API**：异步 fork 没同步完时 Gitea 把它标成 ``empty``，
    这时 ``/branches`` 对任何分支都回 404，而 ``git ls-remote`` 一直是对的——provision 走的也是
    后者。用 API 判断"分支在不在"会得出与真相相反的结论。
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
    """等远端长出 ``expected`` 里的全部分支（Gitea 的异步 fork 就靠这个等）。

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
    """把内容载入 Gitea：建用户 → 建上游仓 → 推全部分支 → 建/补齐 fork → 落令牌。

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

    owner_token = mint_token(config.gitea_url, config.upstream_owner,
                             config.owner_password, "track-loader-owner")
    learner_token = mint_token(config.gitea_url, config.learner,
                               config.learner_password, config.token_name)

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
    log(f"   推入 {len(branches)} 条分支：{', '.join(branches)}")

    log("== 学员 fork（必须在推完分支之后）==")
    if config.fork_reset:
        delete_repo(config.gitea_url, learner_token, config.learner, config.upstream_name)
        log("   BAGU_FORK_RESET=1：已删掉旧 fork（它下面的 dev 也一起没了）")
        result.fork_recreated = True
    ensure_fork(config.gitea_url, learner_token, config.upstream, config.learner,
                config.upstream_name)
    fork_url = token_push_url(
        config.gitea_url, config.learner, config.upstream_name, learner_token
    )
    # 等 Gitea 的异步 fork 落地（202 只表示"接受"）。落地的 fork 会自带上游的全部分支，
    # 所以这里的常见结果是"什么都不用推"——这比事后补推更干净（也不会有 empty 标记的怪状态）。
    remote = wait_for_remote_branches(fork_url, branches, timeout_s=config.timeout_s, log=log)
    missing = sorted(set(branches) - set(remote))
    if missing:
        log(f"   fork 里缺 {len(missing)} 条分支（旧 fork 或异步还没同步完），补推一次")
        push_all_branches(config.content_dir, fork_url)
        remote = remote_branches(fork_url)
        missing = sorted(set(branches) - set(remote))
    if missing:
        raise LoadError(
            f"fork {result.fork} 里始终没有这些分支：{missing}。"
            "上游有而 fork 没有，通常是 fork 太旧——用 BAGU_FORK_RESET=1 重建一次。"
        )
    result.fork = f"{config.learner}/{config.upstream_name}"
    log(f"   {result.fork} 已就绪，git 确认有全部 {len(remote)} 条分支")

    result.token_path = config.token_path
    result.token_path.write_text(learner_token + "\n", encoding="utf-8")
    result.token_path.chmod(0o600)
    log(f"== 后端令牌写入 {result.token_path}")
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

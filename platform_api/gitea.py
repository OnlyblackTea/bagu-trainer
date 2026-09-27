"""Gitea REST API 的最小封装（真 Forge 用）。

刻意**不做**通用 Gitea SDK：平台只需要一个动作——**仓库在不在**（拿到它的 clone URL）。多一个方法
就多一份要维护的契约。所有失败都翻成 :class:`~platform_service.forge.ForgeUnavailableError`，
路由据此给 503，而不是把基础设施故障说成"你的代码没过"。

**这里没有 fork，也没有 PR**（L3 之后）：平台只把 Track 仓当"一个读得到的 git 远端"，学员把 ``dev``
直接 push 上去。于是这个客户端只剩"查仓库"这一件事。

**token 不进工作区**：clone 时用带 token 的临时 URL，clone 完把 remote 改回干净 URL
（见 :meth:`authenticated_clone_url` 的用法），免得 token 落进 `.git/config`。要让学员能
``git push``，token 会写进**仓库外**的一个 0600 凭据文件，仓库里只留指向它的
``credential.helper`` 路径（见 :meth:`credential_line` 与
``GiteaForgeClient.install_push_credentials``）。
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from platform_service.forge import ForgeUnavailableError

__all__ = ["GiteaApi"]


class GiteaApi:
    """一个 Gitea 实例的最小客户端（同步 httpx；ForgeClient 缝本身是同步的）。"""

    def __init__(self, *, base_url: str, token: str, timeout_s: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._client = httpx.Client(
            base_url=f"{self._base}/api/v1",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/json",
            },
            timeout=timeout_s,
        )

    # -- 低层 ------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            return self._client.request(method, path, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as exc:  # 连接失败 / 超时 / DNS
            raise ForgeUnavailableError(f"gitea {method} {path} unreachable: {exc}") from exc

    def claim(self, response: httpx.Response, action: str) -> dict:
        """把非 2xx 一律翻成 :class:`ForgeUnavailableError`（带上响应体前 200 字）。"""
        if response.is_success:
            return response.json() if response.content else {}
        raise ForgeUnavailableError(
            f"gitea {action} → {response.status_code}: {response.text[:200]}"
        )

    def authenticated_clone_url(self, url: str) -> str:
        """把 token 注进 http(s) 的 clone URL（caller 用完要把 remote 改回干净 URL）。"""
        parsed = httpx.URL(url)
        if parsed.scheme not in ("http", "https"):
            return url  # ssh 等：交给调用方的凭据配置
        return str(parsed.copy_with(username=self._token, password=None))

    def credential_line(self, url: str) -> str | None:
        """给 ``git credential-store`` 的一行：``<scheme>://<token>:<token>@<host>``。

        用户名与密码**都填 token**：Gitea 的基本认证两条路都认（把 token 当用户名、或把 token
        当密码），两边都填就不用赌它走哪条。ssh/scp 形态返回 ``None``——那种情况该用学员自己的
        ssh key，平台不该把 token 塞进去。
        """
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            return None
        return f"{parsed.scheme}://{self._token}:{self._token}@{parsed.netloc}/"

    # -- 动作 ------------------------------------------------------------------

    def repo(self, owner: str, name: str) -> dict | None:
        """仓库存在返回它的 JSON，不存在返回 ``None``（404 是**正常**答案，不是错误）。

        这是 A′ 唯一需要的查询：``provision`` 从它拿 ``clone_url``。
        """
        response = self._request("GET", f"/repos/{owner}/{name}")
        if response.status_code == 404:
            return None
        return self.claim(response, f"get repo {owner}/{name}")

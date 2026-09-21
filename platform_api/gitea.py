"""Gitea REST API 的最小封装（真 Forge 用）。

刻意**不做**通用 Gitea SDK：平台只需要三个动作（仓库在不在、确保 fork、开 PR），多一个方法
就多一份要维护的契约。所有失败都翻成 :class:`~platform_service.forge.ForgeUnavailableError`
或 :class:`~platform_service.forge.RemoteBranchMissingError`——路由据此给 5xx / 409，
而不是把基础设施故障说成"你的代码没过"。

**token 不进工作区**：clone 时用带 token 的临时 URL，clone 完把 remote 改回干净 URL
（见 :meth:`authenticated_clone_url` 的用法），免得 token 落进 `.git/config`。
"""

from __future__ import annotations

import httpx

from platform_service.forge import ForgeUnavailableError, RemoteBranchMissingError

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

    # -- 动作 ------------------------------------------------------------------

    def repo(self, owner: str, name: str) -> dict | None:
        """仓库存在返回它的 JSON，不存在返回 ``None``（404 是**正常**答案，不是错误）。"""
        response = self._request("GET", f"/repos/{owner}/{name}")
        if response.status_code == 404:
            return None
        return self.claim(response, f"get repo {owner}/{name}")

    def ensure_fork(self, *, upstream: str, fork_owner: str) -> dict:
        """确保 ``fork_owner`` 名下已有上游仓的 fork，返回该 fork 的 JSON。

        幂等：已存在就直接返回（fork 是"发卷"级别的动作，学员会在同一条工单上反复 provision）。
        """
        upstream_owner, upstream_name = upstream.split("/", 1)
        existing = self.repo(fork_owner, upstream_name)
        if existing is not None:
            return existing
        response = self._request(
            "POST", f"/repos/{upstream_owner}/{upstream_name}/forks", json={}
        )
        if response.status_code == 409:  # 并发下别人刚建好
            again = self.repo(fork_owner, upstream_name)
            if again is not None:
                return again
        return self.claim(response, f"fork {upstream} → {fork_owner}")

    def branch_exists(self, owner: str, name: str, branch: str) -> bool:
        response = self._request("GET", f"/repos/{owner}/{name}/branches/{branch}")
        if response.status_code == 404:
            return False
        self.claim(response, f"get branch {owner}/{name}:{branch}")
        return True

    def create_pull(
        self, *, upstream: str, fork_owner: str, head_branch: str, base_branch: str,
        title: str, body: str,
    ) -> str:
        """在**上游仓**开一个从 ``fork_owner:head_branch`` 到 ``base_branch`` 的 PR。

        返回 PR 的 ``html_url``。远端还没有那个 head 分支时抛
        :class:`RemoteBranchMissingError`（学员还没 push——**预期内**，409 而不是 500）。
        """
        upstream_owner, upstream_name = upstream.split("/", 1)
        response = self._request(
            "POST",
            f"/repos/{upstream_owner}/{upstream_name}/pulls",
            json={
                "head": f"{fork_owner}:{head_branch}",
                "base": base_branch,
                "title": title,
                "body": body,
            },
        )
        if response.status_code in (404, 422) and not self.branch_exists(
            fork_owner, upstream_name, head_branch
        ):
            raise RemoteBranchMissingError(
                repo=f"{fork_owner}/{upstream_name}", branch=head_branch
            )
        # 已经开过同一个 PR：Gitea 会给 409/422，直接找回来当成功（幂等）。
        if response.status_code in (409, 422):
            for pull in self.list_pulls(upstream=upstream, head=f"{fork_owner}:{head_branch}"):
                return str(pull["html_url"])
        payload = self.claim(response, f"open PR {upstream} <- {fork_owner}:{head_branch}")
        return str(payload["html_url"])

    def list_pulls(self, *, upstream: str, head: str) -> list[dict]:
        owner, name = upstream.split("/", 1)
        response = self._request(
            "GET", f"/repos/{owner}/{name}/pulls", params={"state": "open", "limit": 50}
        )
        pulls = self.claim(response, f"list pulls {upstream}")
        return [p for p in pulls if p.get("head", {}).get("label") == head]

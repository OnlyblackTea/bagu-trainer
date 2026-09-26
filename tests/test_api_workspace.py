"""provision 返回工作区、diff 真本地 git 非空、pr 占位 URL；未知实例 → 404。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _enroll_start(client: Any) -> str:
    client.post("/enrollments", json={"learner": "deployer"})
    inst = client.get("/instances", params={"learner": "deployer"}).json()
    stage00 = next(i for i in inst if i["ticket_id"] == "db-stage-00")
    client.post(f"/instances/{stage00['id']}/start")
    return stage00["id"]


def test_provision_returns_workspace(client: Any) -> None:
    iid = _enroll_start(client)
    r = client.post(f"/instances/{iid}/provision")
    assert r.status_code == 200
    body = r.json()
    assert body["branch"] == "dev" and body["simulated"] is True
    # §C.5：base 是该 ticket 所属**阶段**的分支，不是 ticket id。
    assert body["base_ref"] == "origin/stage-00-onboarding"
    # 桩没有人真的写代码 ⇒ "工作区改动"永远是空的，预览只能比对内容预置的解法 ref。
    assert body["diff_head_ref"] == body["head_ref"]
    # 桩没有可推的远端，所以不给 push 命令（给了会把人骗去跑一条注定失败的命令）。
    assert body["push_hint"] is None


def test_diff_is_real_local_git(client: Any) -> None:
    iid = _enroll_start(client)
    client.post(f"/instances/{iid}/provision")
    r = client.get(f"/instances/{iid}/diff")
    assert r.status_code == 200
    body = r.json()
    assert "diff --git" in body["diff"] and body["total_chars"] > 0
    # 桩的 diff 是**装出来的**（比对预置解法 ref），所以如实标 simulated 且不是工作区比对。
    assert body["simulated"] is True and body["worktree"] is False


def test_real_forge_diff_compares_the_learners_worktree(tmp_path: Path) -> None:
    """真 Forge 下 diff 比的是**工作区**（含未提交改动），而不是某条预置 ref。

    这是"diff 不再只是模拟"的核心：学员改完文件还没 commit 就想看 diff，拿 ``dev``（只含已提交）
    去比会显示"没有改动"——比不给看更糟。这条用假 Forge 把契约钉住（真 Gitea 的验证在 VM 上做）。
    """
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from platform_api.app import create_app
    from platform_api.demo_repo import build_demo_track_repo
    from platform_service.forge import Workspace

    repo = build_demo_track_repo(tmp_path / "demo")
    app = create_app(content_dir=repo, forge_base=tmp_path / "ws")
    client = TestClient(app)
    iid = _enroll_start(client)
    real_ws = Workspace(
        path=tmp_path / "ws-real",
        branch="dev",
        base_ref="origin/stage-00-onboarding",
        head_ref="dev",
        simulated=False,
        diff_head_ref=None,
        push_hint="git add -A && git commit -m x && git push -u origin dev",
    )

    class _RealForge:
        def provision(self, instance: Any) -> Workspace:
            # 真的做一次 clone，这样 diff 有真东西可比（假 Forge，真 git）。
            from platform_service.forge import prepare_workspace

            return replace(
                real_ws,
                path=prepare_workspace(
                    dest=tmp_path / "ws-real",
                    clone_url=str(repo),
                    base_ref=real_ws.base_ref,
                    make_dev_branch=True,
                ),
            )

        def create_pr(self, instance: Any, *, base: str, head: str) -> str:
            return "http://gitea.local/trackowner/track/pulls/7"

    bundle = app.state.tracks["database-bagu"]
    app.state.tracks["database-bagu"] = replace(bundle, forge=_RealForge())

    prov = client.post(f"/instances/{iid}/provision").json()
    assert prov["simulated"] is False
    assert prov["diff_head_ref"] is None
    assert prov["head_ref"] == "dev"
    assert "git push -u origin dev" in prov["push_hint"]

    # 学员改了一个文件（未提交）——diff 必须看得见
    target = tmp_path / "ws-real" / "src" / "db-stage-00" / "Solution.java"
    target.write_text("// 学员手写的改动（未提交）\n", encoding="utf-8")

    body = client.get(f"/instances/{iid}/diff").json()
    assert body["worktree"] is True
    assert body["diff_head_ref"] is None
    assert body["simulated"] is False
    assert "学员手写的改动" in body["diff"]

    # PR 给的是真 URL，且不再标 simulated
    pr = client.post(f"/instances/{iid}/pr").json()
    assert pr["pr_url"] == "http://gitea.local/trackowner/track/pulls/7"
    assert pr["simulated"] is False


def test_pr_is_placeholder(client: Any) -> None:
    iid = _enroll_start(client)
    r = client.post(f"/instances/{iid}/pr")
    assert r.status_code == 200
    assert r.json()["pr_url"].startswith("https://") and r.json()["simulated"] is True


def test_provision_unknown_instance_404(client: Any) -> None:
    assert client.post("/instances/nope/provision").status_code == 404


# --- 容器化部署：把容器内的路径翻成宿主机上那条 ---------------------------------


def test_host_path_is_null_unless_the_deployment_declares_it(
    client: Any, monkeypatch: Any
) -> None:
    """非容器部署里 ``path`` 本身就是真的，所以不给 ``host_path``（而不是给个假的）。"""
    monkeypatch.delenv("BAGU_HOST_DATA_DIR", raising=False)
    iid = _enroll_start(client)
    assert client.post(f"/instances/{iid}/provision").json()["host_path"] is None


def test_host_path_translates_the_container_data_root(client: Any, monkeypatch: Any) -> None:
    """compose 栈把宿主机目录绑定挂到 /var/lib/bagu；界面要显示**宿主机**那条路径。

    用户的原话："通过 docker 启动的服务，拉取本地工作区显示的地址是 docker 内的地址"——
    容器里的 /var/lib/bagu/ws/... 对在自己电脑上写代码的人毫无用处。
    """
    monkeypatch.setenv("BAGU_HOST_DATA_DIR", "/home/lqz/bagu-trainer/deploy/data")
    iid = _enroll_start(client)
    body = client.post(f"/instances/{iid}/provision").json()
    # 演示 app 的工作区不在数据根下（它在 tmp_path），映射不出来 ⇒ 仍然是 None，
    # 但**真的**在数据根下时必须翻出来：
    assert body["host_path"] is None

    from platform_api.deps import host_visible_path

    assert (
        host_visible_path("/var/lib/bagu/ws/database-bagu/ti-1")
        == "/home/lqz/bagu-trainer/deploy/data/ws/database-bagu/ti-1"
    )
    assert host_visible_path("/var/lib/bagu") == "/home/lqz/bagu-trainer/deploy/data"
    assert host_visible_path("/somewhere/else") is None


# --- 指引式 provision：登记学员自己的目录 -------------------------------------


def test_registering_your_own_workspace_changes_where_diff_runs(
    client: Any, tmp_path: Path
) -> None:
    """学员在自己熟悉的地方 clone，把路径登记回来，此后 diff/阅卷都对着**那个**目录。

    这是"拉取本地工作区"从"平台替你 clone"变成"平台给指引 + 你说是哪儿"的落点。
    """
    import subprocess

    iid = _enroll_start(client)
    own = tmp_path / "my-own-workspace"
    own.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=own, check=True)
    (own / "notes.md").write_text("我的工作区\n", encoding="utf-8")

    r = client.post(f"/instances/{iid}/workspace", json={"path": str(own)})
    assert r.status_code == 200
    assert r.json()["workspace_path"] == str(own)

    # 实例上记下来了（不是只活在这次响应里）
    assert client.get(f"/instances/{iid}").json()["workspace_path"] == str(own)

    # 撤销登记：回到平台准备的目录
    r = client.post(f"/instances/{iid}/workspace", json={"path": None})
    assert r.status_code == 200 and r.json()["workspace_path"] is None


def test_registering_a_bad_path_is_422_with_a_reason(client: Any, tmp_path: Path) -> None:
    """路径用不了要**说清是哪一种**：目录不存在 / 不是 git 仓库。两者要做的事不同。"""
    iid = _enroll_start(client)

    missing = client.post(
        f"/instances/{iid}/workspace", json={"path": str(tmp_path / "nope")}
    )
    assert missing.status_code == 422
    assert missing.json()["detail"] == "workspace_path_invalid"
    assert missing.json()["reason"] == "not_a_directory"

    plain = tmp_path / "plain"
    plain.mkdir()
    not_repo = client.post(f"/instances/{iid}/workspace", json={"path": str(plain)})
    assert not_repo.status_code == 422
    assert not_repo.json()["reason"] == "not_a_repo"


def test_diff_unknown_instance_404(client: Any) -> None:
    assert client.get("/instances/nope/diff").status_code == 404


def test_missing_stage_branch_maps_to_short_code_without_leaking_a_traceback(
    tmp_path: Path,
) -> None:
    """§C.8：错误一律 ``{"detail": "<短码>"}``，不泄露堆栈。

    这里模拟内容侧的配置错误：Track 声明的阶段分支在 track-repo 里不存在（真 Track 样板
    把答案都塞在单一 ``solutions`` 分支上就是这个症状，不符合 §C.5）。应当在 provision
    就返回 ``workspace_ref_missing`` 并带上缺失的 ref，而不是等到 diff 抛非零退出。
    """
    import subprocess

    from fastapi.testclient import TestClient

    from platform_api.app import create_app
    from platform_api.demo_repo import build_demo_track_repo

    repo = build_demo_track_repo(tmp_path / "demo")
    subprocess.run(
        ["git", "branch", "-D", "stage-00-onboarding-sol"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    app = create_app(content_dir=repo, forge_base=tmp_path / "ws")
    c = TestClient(app, raise_server_exceptions=False)
    c.post("/enrollments", json={"learner": "deployer"})
    iid = next(
        i["id"]
        for i in c.get("/instances", params={"learner": "deployer"}).json()
        if i["ticket_id"] == "db-stage-00"
    )
    c.post(f"/instances/{iid}/start")

    r = c.post(f"/instances/{iid}/provision")
    assert r.status_code == 500
    assert r.json()["detail"] == "workspace_ref_missing"
    assert r.json()["ref"] == "origin/stage-00-onboarding-sol"
    assert "origin/stage-00-onboarding" in r.json()["available"]

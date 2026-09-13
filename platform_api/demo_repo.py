"""A′ demo track-repo 构建器（诚实占位内容）。

建一个本地 git 仓库，既是发卷内容源（track.yaml + stages/<dir>/ticket.yaml），又是
StubForgeClient 的 clone 源：每张 ticket 一个骨架分支 ``<ticket_id>`` 与一个解法分支
``<ticket_id>-sol``，diff 两端模拟"学员把骨架改成解法"。真实 Gitea/内容仓库落地后本模块
整体删除。

**刻意镜像真 Track（seckill-lab）的形状**（见 Plan 1 的 Task 0）：

* stage 目录名是描述式的（``stage-01-product-query-index``），而 ``identity.id`` 是
  ``db-stage-01``——目录名永远不是身份，身份是 ``identity.id``；
* ``manifest.stages`` 是**看板清单**，可以包含"只上榜、不出卷"的 stage
  （``stage-03-deadlock-optional``），照真 Track 的 ``stage-00-onboarding``；
* ``skeleton_repo`` 是 ``"."``，与真 Track 一样"本仓根就是骨架仓"。

若把三者弄成同名，这个 fixture 就会**掩盖**解锁门静默失效的缺陷而不是暴露它——所以
``tests/test_demo_repo.py`` 专门断言了这种差异。内容 dict 对齐
``content/schema/*.schema.json``，故能被 content_loader 与准入门同时接受。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

DEMO_TRACK_ID = "database-bagu"

# 看板清单：包含一个只上榜、不出卷的 stage（镜像真 Track 的 stage-00-onboarding）。
BOARD_ONLY_STAGES = ("stage-03-deadlock-optional",)

DEMO_STAGES = (
    "stage-00-onboarding",
    "stage-01-product-query-index",
    "stage-02-order-transaction",
    *BOARD_ONLY_STAGES,
)

# (stage 目录名, ticket.identity.id, stage 序号, 标题, prerequisites)
_TICKETS: tuple[tuple[str, str, int, str, tuple[str, ...]], ...] = (
    ("stage-00-onboarding", "db-stage-00", 0, "环境入职：把骨架跑起来", ()),
    (
        "stage-01-product-query-index",
        "db-stage-01",
        1,
        "商品搜索查询慢：索引与执行计划",
        ("db-stage-00",),
    ),
    (
        "stage-02-order-transaction",
        "db-stage-02",
        2,
        "订单事务：超卖与隔离级别",
        ("db-stage-01",),
    ),
)

#: ticket.identity.id → 该 ticket 的 stage 目录名（供调用方定位内容）。
TICKET_DIRS: dict[str, str] = {ticket_id: dirname for dirname, ticket_id, *_ in _TICKETS}

_BAGU_MAP_REF = "bagu-map.md#stage"
_BAGU_MAP = "# 八股地图\n\n## Stage\n\n占位内容：最左前缀、覆盖索引、EXPLAIN 读法。\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _track_dict() -> dict[str, Any]:
    entries = {
        "compose": "./docker/docker-compose.yml",
        "build": "./scripts/build.sh",
        "tests": "./stages/*/tests/",
    }
    digest_pin = {name: f"sha256:{name}0d1e2f" for name in entries}
    digest_pin["mysql"] = "sha256:0d1e2f3a4b5c"
    digest_pin["redis"] = "sha256:9a8b7c6d5e4f"
    runtime: dict[str, Any] = {"digest_pin": digest_pin}
    runtime.update(entries)
    return {
        "track": {
            "id": DEMO_TRACK_ID,
            "name": "数据库八股实战",
            "language": "zh-CN",
            "trust_level": "self",
            "manifest": {
                "requires": {"java": "17", "build": "maven"},
                # 真 Track 也是"本仓根即骨架仓"，故 files_to_change 相对仓根解析。
                "skeleton_repo": ".",
                "bagu_map": "./bagu-map.md",
                "stages": list(DEMO_STAGES),
            },
            "runtime": runtime,
            "contribute": {
                "license": "Apache-2.0",
                "maintainers": ["@onlyblacktea"],
                "review": {"ci": "platform validate-track", "signed_by": []},
            },
        }
    }


def _ticket_dict(
    ticket_id: str, stage: int, title: str, prerequisites: tuple[str, ...]
) -> dict[str, Any]:
    return {
        "identity": {
            "id": ticket_id,
            "schema_version": 1,
            "ticket_version": 1,
            "stage": stage,
            "title": title,
        },
        "brief": {
            "problem": f"{title}：按工单完成本阶段任务（占位内容）。",
            "concepts": ["最左前缀", "覆盖索引", "EXPLAIN"],
        },
        "scope": {"files_to_change": [f"src/{ticket_id}/Solution.java"]},
        "grader": {
            "correctness": {
                "test_group": ticket_id,
                "test_version": 1,
                "canonical_test": f"tests/{ticket_id}/AcceptanceTest.java",
                "expect": "初始红 → 改对后绿",
            },
            "performance": {"type": "explain", "assert": "type != 'ALL'"},
            "concurrency": None,
        },
        "hints": [{"id": "H1", "text": "先看 EXPLAIN，再决定索引列序。"}],
        "submission": {
            "template": {"require": ["改后的代码", "一句话理由"]},
            "bagu_map_ref": _BAGU_MAP_REF,
        },
        "mentor_policy": {"mode": "pure_mentor", "may_write": []},
        "progression": {"prerequisites": list(prerequisites)},
    }


def build_demo_track_repo(dest: Path) -> Path:
    """在 ``dest`` 建好 demo track-repo（内容 + 阶段分支），返回 ``dest``。"""
    dest.mkdir(parents=True, exist_ok=True)
    _git(dest, "init", "-q", "-b", "main")
    _git(dest, "config", "user.email", "demo@example.com")
    _git(dest, "config", "user.name", "Demo")
    _git(dest, "config", "commit.gpgsign", "false")

    (dest / "bagu-map.md").write_text(_BAGU_MAP, encoding="utf-8")
    (dest / "track.yaml").write_text(
        yaml.safe_dump(_track_dict(), allow_unicode=True), encoding="utf-8"
    )
    for dirname, ticket_id, stage, title, prerequisites in _TICKETS:
        stage_dir = dest / "stages" / dirname
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "ticket.yaml").write_text(
            yaml.safe_dump(
                _ticket_dict(ticket_id, stage, title, prerequisites), allow_unicode=True
            ),
            encoding="utf-8",
        )
        skeleton = dest / "src" / ticket_id
        skeleton.mkdir(parents=True, exist_ok=True)
        (skeleton / "Solution.java").write_text(
            f"// {ticket_id} skeleton: TODO\n", encoding="utf-8"
        )
    _git(dest, "add", "-A")
    _git(dest, "commit", "-qm", "track content + skeleton")

    for _, ticket_id, *_ in _TICKETS:
        _git(dest, "checkout", "-q", "-b", ticket_id, "main")
        _git(dest, "checkout", "-q", "-b", f"{ticket_id}-sol", ticket_id)
        (dest / "src" / ticket_id / "Solution.java").write_text(
            f"// {ticket_id} solution: 模拟学员解法\npublic class Solution {{}}\n",
            encoding="utf-8",
        )
        _git(dest, "add", "-A")
        _git(dest, "commit", "-qm", f"{ticket_id} solution")
    _git(dest, "checkout", "-q", "main")
    return dest

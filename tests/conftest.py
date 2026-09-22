"""Shared fixtures for the platform_service unit tests.

Model-agnostic on purpose: this module hands out *raw dicts* that validate against
``content/schema/*.schema.json`` (design §4.2 track / §4.3 ticket) plus a builder
that lays out a temporary track directory tree. Typed Pydantic fixtures live next
to the models they wrap (see ``test_models.py``) so that conftest never imports
``platform_service`` at collection time — keeping the scaffold importable before
the modules under test exist.

The samples carry NO ``parametric`` / ``fixture_seed`` (removed in design v0.4).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

# A schema-valid digest only needs the ``sha256:`` prefix (track.schema.json
# pattern), so the samples use short fakes to stay well under the line length.
_FAKE_DIGEST_MYSQL = "sha256:0d1e2f3a4b5c"
_FAKE_DIGEST_REDIS = "sha256:9a8b7c6d5e4f"

_DEFAULT_BAGU_MAP = (
    "# 八股地图\n\n"
    "## Stage 1 索引 & 执行计划\n\n"
    "最左前缀、覆盖索引、EXPLAIN 读法。\n"
)


def make_ticket(
    *,
    ticket_id: str = "db-stage-01",
    stage: int = 1,
    title: str = "商品搜索查询慢",
    prerequisites: list[str] | None = None,
    files_to_change: list[str] | None = None,
    test_group: str = "stage01",
    bagu_map_ref: str = "bagu-map.md#stage-1-索引--执行计划",
) -> dict[str, Any]:
    """A schema-valid ticket dict (design §4.3); override any concern as needed."""
    return {
        "identity": {
            "id": ticket_id,
            "schema_version": 1,
            "ticket_version": 1,
            "stage": stage,
            "title": title,
        },
        "brief": {
            "problem": "运营反馈商品列表页搜索越来越慢，product 表 5 万行全表扫描。",
            "concepts": ["最左前缀", "覆盖索引", "EXPLAIN", "索引失效"],
        },
        "scope": {
            "files_to_change": files_to_change
            or [
                "src/main/resources/mapper/ProductMapper.xml",
                "src/main/resources/db/migration/V1__stage01_index.sql",
            ]
        },
        "grader": {
            "correctness": {
                "test_group": test_group,
                "test_version": 1,
                "canonical_test": "tests/ProductQueryIndexAcceptanceTest.java",
                "expect": "初始红 → 改对后绿",
            },
            "performance": {"type": "explain", "assert": "type != 'ALL' && key != null"},
            "concurrency": None,
        },
        "hints": [
            {"id": "H1", "text": "@Sql 每次测试方法前自动执行 V1 脚本，脚本幂等。"},
            {"id": "H2", "text": "EXPLAIN 断言的查询形状与 searchProducts 一致。"},
        ],
        "submission": {
            "template": {
                "require": [
                    "改后的 searchProducts SQL",
                    "索引列序 + 一句话理由",
                    "建索引前后两次 EXPLAIN 输出对比",
                ]
            },
            "bagu_map_ref": bagu_map_ref,
        },
        "mentor_policy": {"mode": "pure_mentor", "may_write": []},
        "progression": {
            "prerequisites": ["db-stage-00"] if prerequisites is None else prerequisites
        },
    }


def make_track(
    *,
    track_id: str = "database-bagu",
    trust_level: str = "self",
    signed_by: list[str] | None = None,
    stages: list[str] | None = None,
    digest_pin: dict[str, str] | None = None,
    runtime_entries: dict[str, str] | None = None,
    protected_paths: list[str] | None = None,
) -> dict[str, Any]:
    """A schema-valid track manifest dict (design §4.2); override as needed."""
    review: dict[str, Any] = {"ci": "paper validate-track"}
    if signed_by is not None:
        review["signed_by"] = signed_by
    entries = (
        runtime_entries
        if runtime_entries is not None
        else {
            "compose": "./docker/docker-compose.yml",
            "build": "./scripts/build.sh",
            "tests": "./stages/*/tests/",
        }
    )
    if digest_pin is None:
        # A valid track digest-pins every executable entry (§4.6) plus its image deps.
        digest_pin = {name: f"sha256:{name}0d1e2f" for name in entries}
        digest_pin["mysql"] = _FAKE_DIGEST_MYSQL
        digest_pin["redis"] = _FAKE_DIGEST_REDIS
    runtime: dict[str, Any] = {"digest_pin": digest_pin}
    runtime.update(entries)
    # The judged surface (§11.3) is REQUIRED: a track that does not declare what its
    # verdict depends on cannot be protected from "edit the test" while §7.3 is pending.
    runtime["protected_paths"] = (
        protected_paths if protected_paths is not None else ["./src/test/", "./pom.xml"]
    )
    return {
        "track": {
            "id": track_id,
            "name": "数据库八股实战",
            "language": "zh-CN",
            "trust_level": trust_level,
            "manifest": {
                "requires": {"java": "17", "build": "maven"},
                "skeleton_repo": "./skeleton",
                "bagu_map": "./bagu-map.md",
                "stages": stages
                or [
                    "stage-00-onboarding",
                    "stage-01-product-query-index",
                    "stage-02-order-transaction",
                ],
            },
            "runtime": runtime,
            "contribute": {
                "license": "MIT",
                "maintainers": ["@onlyblacktea"],
                "review": review,
            },
        }
    }


def make_grading_report(
    *,
    submission_id: str = "sub-1",
    ticket: str = "db-stage-01",
    ticket_version: int = 1,
    test_version: int = 1,
    engine_version: str = "v0.1.0",
    learner: str = "deployer",
    passed: bool = True,
    runner_trust: str = "self",
    files_changed: list[str] | None = None,
    out_of_scope_edits: list[str] | None = None,
) -> dict[str, Any]:
    """A grading-report claim dict (design §7.4) — an UNTRUSTED self-report.

    Carries provenance + ``runner_trust``; NO ``signature`` and NO ``fixture_seed``
    (both removed in v0.4). Shaped to validate against ``GradingReport``.
    """
    return {
        "submission_id": submission_id,
        "ticket": ticket,
        "ticket_version": ticket_version,
        "test_version": test_version,
        "engine_version": engine_version,
        "learner": learner,
        "provenance": {
            "generated_at": "2026-09-07T10:00:00+00:00",
            "generated_by": "paper-cli/0.1.0",
            "host_env": {"os": "linux", "java": "17"},
            "runner_trust": runner_trust,
        },
        "correctness": {
            "test_group": "stage01",
            "passed": passed,
            "tests": [{"name": "usesIndex", "status": "pass" if passed else "fail"}],
        },
        "performance": {"type": "explain", "passed": passed},
        "concurrency": None,
        "lint": {"passed": True},
        "files_changed": files_changed or [],
        "out_of_scope_edits": out_of_scope_edits or [],
    }


def write_track_tree(
    root: Path,
    *,
    track: dict[str, Any],
    stages: list[tuple[str, dict[str, Any]]],
    skeleton_files: dict[str, str] | None = None,
    bagu_map: str = _DEFAULT_BAGU_MAP,
    solutions: dict[str, str] | None = None,
) -> Path:
    """Lay out a track directory tree under ``root`` and return ``root``.

    ``stages`` is a list of ``(stage_dirname, ticket_dict)``; each ticket is written
    to ``stages/<dirname>/ticket.yaml``. ``skeleton_files`` / ``solutions`` map a
    relative path to file body, written under ``skeleton/`` and ``solutions/``.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "track.yaml").write_text(yaml.safe_dump(track, allow_unicode=True), encoding="utf-8")
    (root / "bagu-map.md").write_text(bagu_map, encoding="utf-8")
    for dirname, ticket in stages:
        stage_dir = root / "stages" / dirname
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "ticket.yaml").write_text(
            yaml.safe_dump(ticket, allow_unicode=True), encoding="utf-8"
        )
    for rel, body in (skeleton_files or {}).items():
        path = root / "skeleton" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    for rel, body in (solutions or {}).items():
        path = root / "solutions" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


@pytest.fixture
def sample_ticket() -> dict[str, Any]:
    return make_ticket()


@pytest.fixture
def sample_track() -> dict[str, Any]:
    return make_track()


@pytest.fixture
def ticket_factory() -> Any:
    return make_ticket


@pytest.fixture
def track_factory() -> Any:
    return make_track


@pytest.fixture
def track_tree_builder() -> Any:
    return write_track_tree


@pytest.fixture
def grading_report_factory() -> Any:
    return make_grading_report


@pytest.fixture
def sample_grading_report() -> dict[str, Any]:
    return make_grading_report()


# --- A′ 组合根（Plan 1）--------------------------------------------------------
# lazy import：保持 collection 期不依赖 fastapi/platform_api。


@pytest.fixture
def demo_app(tmp_path: Path) -> Any:
    """建 demo track-repo 并组装 FastAPI app（每个测试独立 tmp）。"""
    from platform_api.app import create_app
    from platform_api.demo_repo import build_demo_track_repo

    repo = build_demo_track_repo(tmp_path / "demo")
    return create_app(content_dir=repo, forge_base=tmp_path / "ws")


@pytest.fixture
def client(demo_app: Any) -> Any:
    from fastapi.testclient import TestClient

    return TestClient(demo_app)


# --- 持久化（PostgreSQL）-------------------------------------------------------
# 需要一台真库。没有 DSN 就 **skip**，不静默通过：内存那一半证明不了任何 SQL。

POSTGRES_DSN_ENV = "BAGU_TEST_POSTGRES_DSN"


@pytest.fixture
def postgres_dsn() -> str:
    """libpq 连接串；用 ``BAGU_TEST_POSTGRES_DSN`` 指定（建议指向一个可随便清的库）。"""
    dsn = os.environ.get(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} 未设置：跳过 PostgreSQL 相关验证")
    return dsn


@pytest.fixture
def clean_postgres(postgres_dsn: str) -> str:
    """建表 → 清空 → 交出 DSN。**它会 TRUNCATE** —— 别指向真实进度库。

    建表必须在这里发生：清空一个还不存在的表会以 ``UndefinedTable`` 炸掉每一条测试，
    而不是给出"库是空的"。
    """
    import psycopg

    from platform_api.store_postgres import PostgresStore

    PostgresStore(postgres_dsn)  # 幂等建表（`_ensure_schema`）
    with psycopg.connect(postgres_dsn) as conn:
        conn.execute("TRUNCATE bagu_documents, bagu_id_seq")
    return postgres_dsn


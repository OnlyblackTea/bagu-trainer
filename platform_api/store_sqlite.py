"""SQLite 版 :class:`~platform_service.store.Store`（设计 spec §9.1）。

**为什么要有它**：`InMemoryStore` 是"重启即失忆"，而 PostgreSQL 要求用户先跑一个数据库服务。
对"一个人自托管、刷自己的八股"这个主场景，两者都不是好默认值——前者会让攒了一周的进度消失，
后者会把人挡在安装步骤上。SQLite 是标准库自带（`sqlite3`，零额外依赖）、单文件、真的持久：
**默认就该是它**。

**表形状与 PostgreSQL 后端刻意保持一致**（`documents` + `id_seq`，被查询的键列抽出来建索引，
实体整体进 `payload`）：两个实现跑**同一套**一致性测试，形状一致时"换个后端"才是真的只换一行
配置，而不是换一套语义。

**并发**：每次操作单开一条连接（`check_same_thread` 的坑直接绕开），并开 WAL —— 阅卷跑在后台
线程里，读请求在别的线程上，WAL 让"一边写一边读"不互相阻塞；写之间由 SQLite 自己的文件锁 +
``busy_timeout`` 串行化。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from platform_service.models import (
    AcceptanceDecision,
    Enrollment,
    GradingReport,
    GradingRun,
    Review,
    Submission,
    TicketInstance,
    User,
)
from platform_service.store import Store

#: 实体种类 → 模型（与 ``store_postgres.MODELS`` 同名同序）。
MODELS: dict[str, type[BaseModel]] = {
    "users": User,
    "enrollments": Enrollment,
    "instances": TicketInstance,
    "submissions": Submission,
    "reports": GradingReport,
    "reviews": Review,
    "decisions": AcceptanceDecision,
    "grading_runs": GradingRun,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    kind        TEXT NOT NULL,
    id          TEXT NOT NULL,
    learner     TEXT,
    track       TEXT,
    ticket_id   TEXT,
    instance_id TEXT,
    state       TEXT,
    seq         INTEGER,
    created_at  TEXT,
    payload     TEXT NOT NULL,
    PRIMARY KEY (kind, id)
);
CREATE INDEX IF NOT EXISTS documents_learner_idx ON documents (kind, learner);
CREATE INDEX IF NOT EXISTS documents_track_idx   ON documents (kind, learner, track);
CREATE INDEX IF NOT EXISTS documents_ticket_idx  ON documents (kind, track, ticket_id);
CREATE INDEX IF NOT EXISTS documents_instance_idx ON documents (kind, instance_id, seq);

CREATE TABLE IF NOT EXISTS id_seq (
    prefix TEXT PRIMARY KEY,
    value  INTEGER NOT NULL
);
"""

#: 序列表被清掉/漏建而文档还在时，从既有 id 的数字后缀把水位线顶回去（否则会重新发号并覆盖旧行）。
_SEED_SEQ = """
INSERT INTO id_seq (prefix, value)
SELECT prefix, MAX(n) FROM (
    SELECT substr(id, 1, instr(id, '-') - 1) AS prefix,
           CAST(substr(id, instr(id, '-') + 1) AS INTEGER) AS n
    FROM documents
    WHERE instr(id, '-') > 0
) GROUP BY prefix
HAVING n IS NOT NULL
ON CONFLICT (prefix) DO NOTHING;
"""

_SAVE_SQL = """
INSERT INTO documents
    (kind, id, learner, track, ticket_id, instance_id, state, seq, created_at, payload)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (kind, id) DO UPDATE SET
    learner     = excluded.learner,
    track       = excluded.track,
    ticket_id   = excluded.ticket_id,
    instance_id = excluded.instance_id,
    state       = excluded.state,
    seq         = excluded.seq,
    created_at  = excluded.created_at,
    payload     = excluded.payload
"""

_NEXT_ID_SQL = """
INSERT INTO id_seq (prefix, value) VALUES (?, 1)
ON CONFLICT (prefix) DO UPDATE SET value = value + 1
RETURNING value
"""


class SqliteStore(Store):
    """单文件、真持久的 :class:`Store`（**默认后端**）。"""

    def __init__(self, path: str | Path) -> None:
        raw = str(path).strip()
        if not raw:
            raise ValueError("SqliteStore 需要数据库文件路径")
        # 兼容 `sqlite:///path` 这种写成 URL 的形式（部署时容易顺手写成那样）。
        for prefix in ("sqlite:///", "sqlite://", "sqlite:"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
                break
        self.path = Path(raw).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.executescript(_SEED_SEQ)

    # --- plumbing ------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """单开一条连接：绕开 ``check_same_thread``，WAL 让读写不互相阻塞。"""
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _write(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self._connect() as conn:
            conn.execute(sql, params)

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(conn.execute(sql, params))

    def _save(
        self, kind: str, entity: BaseModel, *, key: str | None = None, **columns: Any
    ) -> None:
        """Upsert by identity。``key`` 只覆盖**行键**（``GradingReport`` 没有自己的 ``id``，
        它按 ``submission_id`` 存）；payload 始终是模型自己的 dump，读回来才能原样验证通过。"""
        payload = entity.model_dump(mode="json")
        created = getattr(entity, "created_at", None)
        self._write(
            _SAVE_SQL,
            (
                kind,
                key or payload["id"],
                columns.get("learner"),
                columns.get("track"),
                columns.get("ticket_id"),
                columns.get("instance_id"),
                columns.get("state"),
                columns.get("seq"),
                created.isoformat() if created is not None else None,
                json.dumps(payload, ensure_ascii=False),
            ),
        )

    def _get(self, kind: str, entity_id: str, model: type[BaseModel]) -> Any:
        rows = self._rows(
            "SELECT payload FROM documents WHERE kind = ? AND id = ?", (kind, entity_id)
        )
        return model.model_validate(json.loads(rows[0]["payload"])) if rows else None

    def _list(
        self,
        kind: str,
        model: type[BaseModel],
        *,
        order_by: str = "created_at, id",
        states: tuple[str, ...] | None = None,
        **filters: Any,
    ) -> list[Any]:
        where = ["kind = ?"]
        params: list[Any] = [kind]
        for column in ("learner", "track", "ticket_id", "instance_id"):
            value = filters.get(column)
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        if states:
            where.append(f"state IN ({', '.join('?' * len(states))})")
            params.extend(states)
        sql = f"SELECT payload FROM documents WHERE {' AND '.join(where)} ORDER BY {order_by}"
        rows = self._rows(sql, tuple(params))
        return [model.model_validate(json.loads(r["payload"])) for r in rows]

    # --- identity ------------------------------------------------------------

    def next_id(self, prefix: str) -> str:
        """原子取号：``INSERT … ON CONFLICT DO UPDATE … RETURNING`` 一条语句搞定。

        ``RETURNING`` 要 SQLite ≥3.35（Python 3.11 起随附的都满足；`sqlite3.sqlite_version` 可查）。
        """
        rows = self._rows(_NEXT_ID_SQL, (prefix,))
        return f"{prefix}-{rows[0][0]}"

    # --- entities ------------------------------------------------------------

    def save_user(self, user: User) -> None:
        self._save("users", user)

    def get_user(self, user_id: str) -> User | None:
        return self._get("users", user_id, User)

    def save_enrollment(self, enrollment: Enrollment) -> None:
        self._save("enrollments", enrollment, learner=enrollment.learner)

    def get_enrollment(self, enrollment_id: str) -> Enrollment | None:
        return self._get("enrollments", enrollment_id, Enrollment)

    def save_instance(self, instance: TicketInstance) -> None:
        self._save(
            "instances",
            instance,
            learner=instance.learner,
            track=instance.track,
            ticket_id=instance.ticket_id,
            state=instance.state.value,
        )

    def get_instance(self, instance_id: str) -> TicketInstance | None:
        return self._get("instances", instance_id, TicketInstance)

    def list_instances(
        self,
        *,
        learner: str | None = None,
        ticket_id: str | None = None,
        track: str | None = None,
    ) -> list[TicketInstance]:
        return self._list(
            "instances", TicketInstance, learner=learner, ticket_id=ticket_id, track=track
        )

    def save_submission(self, submission: Submission) -> None:
        self._save(
            "submissions", submission, instance_id=submission.instance_id, seq=submission.seq
        )

    def get_submission(self, submission_id: str) -> Submission | None:
        return self._get("submissions", submission_id, Submission)

    def list_submissions(self, instance_id: str) -> list[Submission]:
        """按 ``seq`` 排：重试是**新的** Submission（§9.2），历史要按时间读得出来。"""
        return self._list("submissions", Submission, order_by="seq", instance_id=instance_id)

    def save_report(self, report: GradingReport) -> None:
        self._save("reports", report, key=report.submission_id)

    def get_report(self, submission_id: str) -> GradingReport | None:
        return self._get("reports", submission_id, GradingReport)

    def save_review(self, review: Review) -> None:
        self._save("reviews", review, key=review.submission_id)

    def get_review(self, submission_id: str) -> Review | None:
        return self._get("reviews", submission_id, Review)

    def save_decision(self, decision: AcceptanceDecision) -> None:
        self._save("decisions", decision, key=decision.submission_id)

    def get_decision(self, submission_id: str) -> AcceptanceDecision | None:
        return self._get("decisions", submission_id, AcceptanceDecision)

    def save_grading_run(self, run: GradingRun) -> None:
        self._save(
            "grading_runs",
            run,
            key=run.submission_id,
            learner=run.learner,
            track=run.track,
            ticket_id=run.ticket_id,
            instance_id=run.instance_id,
            state=run.status.value,
        )

    def get_grading_run(self, submission_id: str) -> GradingRun | None:
        return self._get("grading_runs", submission_id, GradingRun)

    def list_grading_runs(
        self, *, instance_id: str | None = None, active_only: bool = False
    ) -> list[GradingRun]:
        return self._list(
            "grading_runs",
            GradingRun,
            instance_id=instance_id,
            states=("queued", "running") if active_only else None,
        )

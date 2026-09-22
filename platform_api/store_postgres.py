"""PostgreSQL 版 :class:`~platform_service.store.Store`（设计 spec §9.1）。

**为什么在适配层**：纯域层不导入驱动（D4）。``platform_service.store`` 只定缝，
这里实现它，``PlatformService``、路由、模型一行都不用改——这正是"缝"要证明的事。

**为什么不是 ORM**：整个控制面只有 7 种实体、一次查询最多过滤两个维度。SQLAlchemy 那套
映射/会话/懒加载是为"关系复杂 + 多人协作"付的钱，这里只会再加一层要理解的东西。用
psycopg3 直接写 SQL，读的人能一眼看到"到底查了什么"。

**表形状**：一张带**显式索引列**的文档表 + 一张身份序列。实体本身是 Pydantic 模型（§9.1），
它的 ``model_dump(mode="json")`` 就是权威表示，塞进 ``payload jsonb``；只有**查询真正会用到**
的列才抽出来建索引。这样加一个字段不用写迁移，而按 learner/ticket/instance 的列表查询仍然走索引。

**同步驱动**：服务层是同步的（``PlatformService`` 全同步方法），所以这里也同步。用 psycopg3 的
``asyncpg`` 反而要在中间架一层 ``asyncio`` 桥。每次操作单开一条连接：无共享可变状态，因此天然
线程安全（阅卷线程池会并发读 store），代价是每请求一次 TCP+auth——单机自托管的量级下不值一提。
"""

from __future__ import annotations

from collections.abc import Sequence
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

#: 实体种类 → 模型。kind 就是这张表的"表名"。
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

#: 文档表 + 身份序列。``created_at`` 可空：只有带 ``created_at`` 的实体（见 models）会填，
#: 它存在的唯一理由是给列表查询一个**确定**的顺序——没有它，PostgreSQL 返回的行序是任意的，
#: 前端看板每重启一次就洗一次牌，而 ``reissue`` 取 ``live[-1]`` 也跟着变得不可复现。
#: ``state`` 不建索引（没有查询用它），留着是为了运维直接 ``psql`` 时能看懂这行是什么。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS bagu_documents (
    kind        text        NOT NULL,
    id          text        NOT NULL,
    learner     text,
    track       text,
    ticket_id   text,
    instance_id text,
    state       text,
    seq         integer,
    created_at  timestamptz,
    payload     jsonb       NOT NULL,
    PRIMARY KEY (kind, id)
);
-- 已存在的库补列（`CREATE TABLE IF NOT EXISTS` 不会加列）。
ALTER TABLE bagu_documents ADD COLUMN IF NOT EXISTS track text;
CREATE INDEX IF NOT EXISTS bagu_documents_learner_idx
    ON bagu_documents (kind, learner);
CREATE INDEX IF NOT EXISTS bagu_documents_track_idx
    ON bagu_documents (kind, learner, track);
CREATE INDEX IF NOT EXISTS bagu_documents_ticket_idx
    ON bagu_documents (kind, track, ticket_id);
CREATE INDEX IF NOT EXISTS bagu_documents_instance_idx
    ON bagu_documents (kind, instance_id, seq);

CREATE TABLE IF NOT EXISTS bagu_id_seq (
    prefix text   PRIMARY KEY,
    value  bigint NOT NULL
);
"""

#: 序列表的**补种**：万一 ``bagu_id_seq`` 被清掉/漏建而库里还有文档，从既有 id 数字后缀把
#: 水位线顶回去，否则 ``next_id`` 会从 1 开始重新发号并覆盖已有行（正是要把这条堵死）。
#: 已经有行的 prefix 一律不动（``DO NOTHING``），因为那水位线比任何推算都准。
_SEED_SEQ = """
INSERT INTO bagu_id_seq (prefix, value)
SELECT prefix, MAX(n) FROM (
    SELECT split_part(id, '-', 1) AS prefix,
           substring(id from '([0-9]+)$')::bigint AS n
    FROM bagu_documents
) seeded
WHERE n IS NOT NULL
GROUP BY prefix
ON CONFLICT (prefix) DO NOTHING;
"""

_SAVE_SQL = """
INSERT INTO bagu_documents
    (kind, id, learner, track, ticket_id, instance_id, state, seq, created_at, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (kind, id) DO UPDATE SET
    learner     = EXCLUDED.learner,
    track       = EXCLUDED.track,
    ticket_id   = EXCLUDED.ticket_id,
    instance_id = EXCLUDED.instance_id,
    state       = EXCLUDED.state,
    seq         = EXCLUDED.seq,
    created_at  = EXCLUDED.created_at,
    payload     = EXCLUDED.payload
"""

_NEXT_ID_SQL = """
INSERT INTO bagu_id_seq (prefix, value) VALUES (%s, 1)
ON CONFLICT (prefix) DO UPDATE SET value = bagu_id_seq.value + 1
RETURNING value
"""


class StoreUnavailableError(RuntimeError):
    """存储后端配不起来（缺驱动 / DSN 不通）。

    在**启动时**抛：与阅卷/Forge 后端一个规矩——配置错要立刻炸，而不是等学员交卷那一刻
    才发现进度根本没地方存。
    """


def _import_psycopg() -> tuple[Any, Any, Any]:
    """惰性导入 psycopg3（可选依赖）。缺了就给出**可照做**的安装命令。"""
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
    except ModuleNotFoundError as exc:  # pragma: no cover - 取决于环境是否装了 extra
        raise StoreUnavailableError(
            "PostgreSQL 存储需要 psycopg3：pip install 'bagu-trainer[postgres]'"
        ) from exc
    return psycopg, dict_row, Jsonb


class PostgresStore(Store):
    """``bagu_documents`` 文档表 + ``bagu_id_seq`` 序列上的 :class:`Store`。

    ``isinstance(x, Store)`` 成立（运行时 Protocol），且与 :class:`InMemoryStore` 跑**同一套**
    一致性测试（``tests/test_store.py`` 参数化）——缝的价值就在于两者可换。
    """

    def __init__(self, dsn: str) -> None:
        if not dsn.strip():
            raise StoreUnavailableError("PostgreSQL 存储需要 DSN（BAGU_DATABASE_URL）")
        self._dsn = dsn
        self._psycopg, self._dict_row, self._jsonb = _import_psycopg()
        self._ensure_schema()

    # --- plumbing ------------------------------------------------------------

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """执行一条 SQL 并取回全部行；每次单开连接（见模块 docstring）。"""
        with self._psycopg.connect(self._dsn, row_factory=self._dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if cur.description is not None else []

    def _ensure_schema(self) -> None:
        try:
            self._query(_SCHEMA)
            self._query(_SEED_SEQ)
        except self._psycopg.OperationalError as exc:
            raise StoreUnavailableError(f"连不上 PostgreSQL: {exc}") from exc

    def _save(
        self, kind: str, entity: BaseModel, *, key: str | None = None, **columns: Any
    ) -> None:
        """Upsert by identity. ``key`` overrides the payload's ``id`` for the one
        entity that has none of its own: ``GradingReport`` is keyed by
        ``submission_id`` (§9.1). The override only affects the ROW KEY — the stored
        payload stays the model's own dump, so reading it back re-validates cleanly
        (every model forbids extra keys, so a synthetic ``id`` in the payload would
        make every report unreadable)."""
        payload = entity.model_dump(mode="json")
        self._query(
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
                getattr(entity, "created_at", None),
                self._jsonb(payload),
            ),
        )

    def _get(self, kind: str, entity_id: str, model: type[BaseModel]) -> Any:
        rows = self._query(
            "SELECT payload FROM bagu_documents WHERE kind = %s AND id = %s",
            (kind, entity_id),
        )
        return model.model_validate(rows[0]["payload"]) if rows else None

    def _list(
        self,
        kind: str,
        model: type[BaseModel],
        *,
        order_by: str = "created_at, id",
        states: Sequence[str] | None = None,
        **filters: Any,
    ) -> list[Any]:
        where = ["kind = %s"]
        params: list[Any] = [kind]
        for column in ("learner", "track", "ticket_id", "instance_id"):
            value = filters.get(column)
            if value is not None:
                where.append(f"{column} = %s")
                params.append(value)
        if states:
            where.append("state = ANY(%s)")
            params.append(list(states))
        sql = (
            f"SELECT payload FROM bagu_documents WHERE {' AND '.join(where)} "
            f"ORDER BY {order_by}"
        )
        return [model.model_validate(row["payload"]) for row in self._query(sql, tuple(params))]

    # --- identity ------------------------------------------------------------

    def next_id(self, prefix: str) -> str:
        """原子取号：``INSERT ... ON CONFLICT DO UPDATE ... RETURNING`` 一条语句搞定。

        并发调用各自拿到不同值（行锁串行化），且水位线落库，进程重启不影响。
        """
        rows = self._query(_NEXT_ID_SQL, (prefix,))
        return f"{prefix}-{rows[0]['value']}"

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
            "instances",
            TicketInstance,
            learner=learner,
            ticket_id=ticket_id,
            track=track,
        )

    def save_submission(self, submission: Submission) -> None:
        self._save(
            "submissions", submission, instance_id=submission.instance_id, seq=submission.seq
        )

    def get_submission(self, submission_id: str) -> Submission | None:
        return self._get("submissions", submission_id, Submission)

    def list_submissions(self, instance_id: str) -> list[Submission]:
        """Ordered by ``seq``, not by insertion — a retry is a NEW Submission (§9.2)
        and the history must read chronologically regardless of save order."""
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

    # --- 阅卷运行记录 ---------------------------------------------------------
    # 复用同一张文档表：``state`` 放 run 状态，``created_at`` 放 started_at（列表就按它排序）。
    # 于是**不需要任何迁移**——新增一种 kind 就够了。

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

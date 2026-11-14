from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import UUID, uuid4

from .migration import migrate
from .models import (
    Assignment,
    Phase,
    ResearchSession,
    ResearchTask,
    SessionEvent,
    SessionStatus,
    SessionSummary,
    TaskStatus,
    utcnow,
)
from .prompts import PromptComposer
from .workflow import (
    ResearchDomainError,
    RoundManager,
    StaleRevisionError,
    next_phase,
    require_session_transition,
    require_task_transition,
)


def _timestamp(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def _request_hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ResearchRepository:
    """SQLite persistence boundary for research sessions only."""

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5_000):
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connection(configure_journal=True)) as connection:
            migrate(connection)

    def _connection(self, *, configure_journal: bool = False) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        connection.execute("PRAGMA foreign_keys = ON")
        if configure_journal:
            deadline = time.monotonic() + self.busy_timeout_ms / 1000
            while True:
                try:
                    connection.execute("PRAGMA journal_mode = WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                        connection.close()
                        raise
                    time.sleep(0.01)
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def create_session(
        self,
        *,
        question: str,
        title: str | None,
        idempotency_key: str,
    ) -> ResearchSession:
        now = _timestamp()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM research_sessions WHERE create_idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if existing["question"] != question or existing["title"] != title:
                    raise ResearchDomainError("BAD_REQUEST", "Idempotency key was used with different arguments")
                return self._session(existing)
            session_id = str(uuid4())
            connection.execute(
                """INSERT INTO research_sessions(
                    session_id, question, title, phase, status, revision, gap_reason,
                    create_idempotency_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?, ?)""",
                (session_id, question, title, Phase.DRAFT.value, SessionStatus.ACTIVE.value, idempotency_key, now, now),
            )
            self._event(connection, session_id, "session.created", None, 0, now)
            row = connection.execute("SELECT * FROM research_sessions WHERE session_id = ?", (session_id,)).fetchone()
            return self._session(row)

    def get_session(self, session_id: UUID | str) -> ResearchSession:
        with closing(self._connection()) as connection:
            row = connection.execute(
                "SELECT * FROM research_sessions WHERE session_id = ?", (str(session_id),)
            ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "Research session was not found")
        return self._session(row)

    def list_sessions(self, *, limit: int = 50, offset: int = 0) -> list[SessionSummary]:
        with closing(self._connection()) as connection:
            rows = connection.execute(
                """SELECT session_id, title, phase, status, revision, updated_at
                   FROM research_sessions ORDER BY updated_at DESC, session_id LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [
            SessionSummary(
                session_id=row["session_id"], title=row["title"], phase=row["phase"],
                status=row["status"], revision=row["revision"], updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def list_tasks(self, session_id: UUID | str, *, phase: Phase | None = None) -> list[ResearchTask]:
        sql = "SELECT * FROM research_tasks WHERE session_id = ?"
        parameters: list[object] = [str(session_id)]
        if phase is not None:
            sql += " AND phase = ?"
            parameters.append(phase.value)
        sql += " ORDER BY phase, ordinal, task_id"
        with closing(self._connection()) as connection:
            if not connection.execute(
                "SELECT 1 FROM research_sessions WHERE session_id = ?", (str(session_id),)
            ).fetchone():
                raise ResearchDomainError("NOT_FOUND", "Research session was not found")
            rows = connection.execute(sql, parameters).fetchall()
        return [self._task(row) for row in rows]

    def list_events(self, session_id: UUID | str) -> list[SessionEvent]:
        with closing(self._connection()) as connection:
            rows = connection.execute(
                "SELECT * FROM session_events WHERE session_id = ? ORDER BY created_at, event_id",
                (str(session_id),),
            ).fetchall()
        return [
            SessionEvent(
                event_id=row["event_id"], session_id=row["session_id"], event_type=row["event_type"],
                entity_id=row["entity_id"], revision=row["revision"], created_at=row["created_at"],
            )
            for row in rows
        ]

    def start(
        self,
        session_id: UUID | str,
        *,
        expected_revision: int,
        idempotency_key: str,
        assignments: Sequence[Assignment],
    ) -> ResearchSession:
        payload = {
            "session_id": str(session_id), "expected_revision": expected_revision,
            "assignments": [item.model_dump(mode="json") for item in assignments],
        }
        with self._transaction() as connection:
            repeated = self._idempotent_result(connection, "session.start", idempotency_key, payload)
            if repeated:
                return repeated
            row = self._locked_session(connection, session_id, expected_revision)
            if Phase(row["phase"]) != Phase.DRAFT or SessionStatus(row["status"]) != SessionStatus.ACTIVE:
                raise ResearchDomainError("INVALID_PHASE", "Only an active draft session can be started")
            now = _timestamp()
            round_id = str(uuid4())
            connection.execute(
                "INSERT INTO research_rounds VALUES (?, ?, ?, 1, ?, ?)",
                (round_id, str(session_id), Phase.INDEPENDENT.value, "ACTIVE", now),
            )
            for ordinal, assignment in enumerate(assignments):
                connection.execute(
                    "INSERT INTO session_participants VALUES (?, ?, ?, ?, ?, 1)",
                    (str(uuid4()), str(session_id), assignment.provider_id, assignment.role, ordinal),
                )
                task_id = str(uuid4())
                connection.execute(
                    """INSERT INTO research_tasks VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?
                    )""",
                    (task_id, str(session_id), round_id, Phase.INDEPENDENT.value, "INDEPENDENT_RESPONSE",
                     assignment.provider_id, assignment.role, ordinal, TaskStatus.PROMPT_READY.value, now, now),
                )
                artifact = PromptComposer.independent(UUID(task_id), row["question"], assignment.role)
                connection.execute(
                    "INSERT INTO prompt_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(artifact.prompt_id), task_id, artifact.version, artifact.content,
                     artifact.size_bytes, artifact.sha256, artifact.created_at.isoformat()),
                )
            updated = self._update_session(
                connection, row, phase=Phase.INDEPENDENT, status=SessionStatus.ACTIVE, gap_reason=None, now=now
            )
            self._event(connection, str(session_id), "session.started", round_id, updated.revision, now)
            self._store_idempotency(connection, "session.start", idempotency_key, payload, updated)
            return updated

    def change_status(
        self,
        session_id: UUID | str,
        *,
        operation: str,
        target: SessionStatus,
        expected_revision: int,
        idempotency_key: str,
    ) -> ResearchSession:
        payload = {"session_id": str(session_id), "expected_revision": expected_revision, "target": target.value}
        with self._transaction() as connection:
            repeated = self._idempotent_result(connection, operation, idempotency_key, payload)
            if repeated:
                return repeated
            row = self._locked_session(connection, session_id, expected_revision)
            require_session_transition(SessionStatus(row["status"]), target)
            now = _timestamp()
            updated = self._update_session(
                connection, row, phase=Phase(row["phase"]), status=target,
                gap_reason=row["gap_reason"], now=now,
            )
            self._event(connection, str(session_id), operation, None, updated.revision, now)
            self._store_idempotency(connection, operation, idempotency_key, payload, updated)
            return updated

    def advance(
        self,
        session_id: UUID | str,
        *,
        expected_revision: int,
        idempotency_key: str,
        gap_reason: str | None,
    ) -> ResearchSession:
        payload = {
            "session_id": str(session_id), "expected_revision": expected_revision,
            "gap_reason": gap_reason,
        }
        with self._transaction() as connection:
            repeated = self._idempotent_result(connection, "session.advance", idempotency_key, payload)
            if repeated:
                return repeated
            row = self._locked_session(connection, session_id, expected_revision)
            if SessionStatus(row["status"]) != SessionStatus.ACTIVE:
                raise ResearchDomainError("INVALID_PHASE", "Only an active session can advance")
            phase = Phase(row["phase"])
            if phase == Phase.DRAFT:
                raise ResearchDomainError("INVALID_PHASE", "Start the session before advancing")
            task_rows = connection.execute(
                "SELECT status FROM research_tasks WHERE session_id = ? AND phase = ? ORDER BY ordinal",
                (str(session_id), phase.value),
            ).fetchall()
            has_gap = RoundManager.validate_gate(
                phase, [TaskStatus(item["status"]) for item in task_rows], gap_reason
            )
            target = next_phase(phase)
            combined_gap = gap_reason.strip() if has_gap and gap_reason else row["gap_reason"]
            if row["gap_reason"] and combined_gap and combined_gap != row["gap_reason"]:
                combined_gap = f"{row['gap_reason']}; {combined_gap}"
            now = _timestamp()
            target_status = SessionStatus.ACTIVE
            round_id: str | None = None
            if target == Phase.DONE:
                target_status = SessionStatus.COMPLETED_WITH_GAPS if combined_gap else SessionStatus.COMPLETED
            else:
                ordinal = connection.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM research_rounds WHERE session_id = ?",
                    (str(session_id),),
                ).fetchone()[0]
                round_id = str(uuid4())
                connection.execute(
                    "INSERT INTO research_rounds VALUES (?, ?, ?, ?, ?, ?)",
                    (round_id, str(session_id), target.value, ordinal, "ACTIVE", now),
                )
                for task_ordinal, template in enumerate(RoundManager.templates_for(target)):
                    task_id = str(uuid4())
                    connection.execute(
                        """INSERT INTO research_tasks VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?
                        )""",
                        (task_id, str(session_id), round_id, target.value, template.kind.value,
                         template.provider_id, template.role, task_ordinal, TaskStatus.PROMPT_READY.value, now, now),
                    )
                    artifact = PromptComposer.phase(UUID(task_id), target, template.role)
                    connection.execute(
                        "INSERT INTO prompt_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (str(artifact.prompt_id), task_id, artifact.version, artifact.content,
                         artifact.size_bytes, artifact.sha256, artifact.created_at.isoformat()),
                    )
            updated = self._update_session(
                connection, row, phase=target, status=target_status, gap_reason=combined_gap, now=now
            )
            self._event(connection, str(session_id), "session.advanced", round_id, updated.revision, now)
            self._store_idempotency(connection, "session.advance", idempotency_key, payload, updated)
            return updated

    def update_task_status(
        self,
        session_id: UUID | str,
        task_id: UUID | str,
        *,
        status: TaskStatus,
        expected_revision: int,
        idempotency_key: str,
        gap_reason: str | None = None,
    ) -> ResearchTask:
        payload = {
            "session_id": str(session_id), "task_id": str(task_id), "status": status.value,
            "expected_revision": expected_revision, "gap_reason": gap_reason,
        }
        operation = "task.status"
        with self._transaction() as connection:
            prior = connection.execute(
                "SELECT request_hash FROM idempotency_operations WHERE operation = ? AND idempotency_key = ?",
                (operation, idempotency_key),
            ).fetchone()
            digest = _request_hash(payload)
            if prior:
                if prior["request_hash"] != digest:
                    raise ResearchDomainError("BAD_REQUEST", "Idempotency key was used with different arguments")
                task = connection.execute("SELECT * FROM research_tasks WHERE task_id = ?", (str(task_id),)).fetchone()
                if not task:
                    raise ResearchDomainError("NOT_FOUND", "Research task was not found")
                return self._task(task)
            session_row = self._locked_session(connection, session_id, expected_revision)
            task_row = connection.execute(
                "SELECT * FROM research_tasks WHERE task_id = ? AND session_id = ?",
                (str(task_id), str(session_id)),
            ).fetchone()
            if not task_row:
                raise ResearchDomainError("NOT_FOUND", "Research task was not found")
            require_task_transition(TaskStatus(task_row["status"]), status, gap_reason)
            now = _timestamp()
            connection.execute(
                """UPDATE research_tasks SET status = ?, gap_reason = ?, revision = revision + 1, updated_at = ?
                   WHERE task_id = ?""",
                (status.value, gap_reason, now, str(task_id)),
            )
            updated_session = self._update_session(
                connection, session_row, phase=Phase(session_row["phase"]),
                status=SessionStatus(session_row["status"]), gap_reason=session_row["gap_reason"], now=now,
            )
            self._event(connection, str(session_id), "task.status_changed", str(task_id), updated_session.revision, now)
            self._store_idempotency(connection, operation, idempotency_key, payload, updated_session)
            task = connection.execute("SELECT * FROM research_tasks WHERE task_id = ?", (str(task_id),)).fetchone()
            return self._task(task)

    def _locked_session(
        self, connection: sqlite3.Connection, session_id: UUID | str, expected_revision: int
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM research_sessions WHERE session_id = ?", (str(session_id),)
        ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "Research session was not found")
        if row["revision"] != expected_revision:
            raise StaleRevisionError()
        return row

    def _update_session(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        phase: Phase,
        status: SessionStatus,
        gap_reason: str | None,
        now: str,
    ) -> ResearchSession:
        cursor = connection.execute(
            """UPDATE research_sessions SET phase = ?, status = ?, gap_reason = ?,
               revision = revision + 1, updated_at = ? WHERE session_id = ? AND revision = ?""",
            (phase.value, status.value, gap_reason, now, row["session_id"], row["revision"]),
        )
        if cursor.rowcount != 1:
            raise StaleRevisionError()
        updated = connection.execute(
            "SELECT * FROM research_sessions WHERE session_id = ?", (row["session_id"],)
        ).fetchone()
        return self._session(updated)

    def _idempotent_result(
        self, connection: sqlite3.Connection, operation: str, key: str, payload: object
    ) -> ResearchSession | None:
        row = connection.execute(
            "SELECT * FROM idempotency_operations WHERE operation = ? AND idempotency_key = ?",
            (operation, key),
        ).fetchone()
        if not row:
            return None
        if row["request_hash"] != _request_hash(payload):
            raise ResearchDomainError("BAD_REQUEST", "Idempotency key was used with different arguments")
        session = connection.execute(
            "SELECT * FROM research_sessions WHERE session_id = ?", (row["session_id"],)
        ).fetchone()
        return self._session(session)

    def _store_idempotency(
        self,
        connection: sqlite3.Connection,
        operation: str,
        key: str,
        payload: object,
        session: ResearchSession,
    ) -> None:
        connection.execute(
            "INSERT INTO idempotency_operations VALUES (?, ?, ?, ?, ?, ?)",
            (operation, key, str(session.session_id), _request_hash(payload), session.revision, _timestamp()),
        )

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        session_id: str,
        event_type: str,
        entity_id: str | None,
        revision: int,
        created_at: str,
    ) -> None:
        connection.execute(
            "INSERT INTO session_events VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid4()), session_id, event_type, entity_id, revision, created_at),
        )

    @staticmethod
    def _session(row: sqlite3.Row) -> ResearchSession:
        return ResearchSession(
            session_id=row["session_id"], question=row["question"], title=row["title"],
            phase=row["phase"], status=row["status"], revision=row["revision"],
            gap_reason=row["gap_reason"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _task(row: sqlite3.Row) -> ResearchTask:
        return ResearchTask(
            task_id=row["task_id"], session_id=row["session_id"], round_id=row["round_id"],
            phase=row["phase"], kind=row["kind"], provider_id=row["provider_id"], role=row["role"],
            ordinal=row["ordinal"], status=row["status"], gap_reason=row["gap_reason"],
            revision=row["revision"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

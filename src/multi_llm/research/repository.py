from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
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
    PromptMetadata,
    ResearchSession,
    ResearchTask,
    ResponseMetadata,
    ResponseReceipt,
    SessionEvent,
    SessionStatus,
    SessionSummary,
    TaskKind,
    TaskStatus,
    UploadPartReceipt,
    UploadReservation,
    utcnow,
)
from .prompts import PromptComposer
from .provider_catalog import PERPLEXITY_SEARCH_ASSIGNMENT, R0_ASSIGNMENTS, is_approved_provider
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


MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_PROMPT_BYTES = 2 * 1024 * 1024
MAX_CHUNK_BYTES = 32_768
MAX_OPEN_UPLOADS = 8
MAX_STAGING_BYTES = 16 * 1024 * 1024
UPLOAD_TTL = timedelta(hours=1)


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
        perplexity_prompt: str | None = None,
    ) -> ResearchSession:
        now = _timestamp()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM research_sessions WHERE create_idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if (existing["question"] != question or existing["title"] != title
                        or existing["perplexity_prompt"] != perplexity_prompt):
                    raise ResearchDomainError("BAD_REQUEST", "Idempotency key was used with different arguments")
                return self._session(existing)
            session_id = str(uuid4())
            connection.execute(
                """INSERT INTO research_sessions(
                    session_id, question, title, phase, status, revision, gap_reason,
                    create_idempotency_key, created_at, updated_at, perplexity_prompt, intake_version
                ) VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?, 1)""",
                (session_id, question, title, Phase.DRAFT.value, SessionStatus.ACTIVE.value,
                 idempotency_key, now, now, perplexity_prompt),
            )
            self._event(connection, session_id, "session.created", None, 0, now)
            row = connection.execute("SELECT * FROM research_sessions WHERE session_id = ?", (session_id,)).fetchone()
            return self._session(row)

    def update_intake(
        self,
        session_id: UUID | str,
        *,
        question: str,
        title: str | None,
        perplexity_prompt: str | None,
        expected_revision: int,
        idempotency_key: str,
    ) -> ResearchSession:
        payload = {
            "session_id": str(session_id), "question": question, "title": title,
            "perplexity_prompt": perplexity_prompt, "expected_revision": expected_revision,
        }
        with self._transaction() as connection:
            repeated = self._idempotent_result(connection, "session.update_intake", idempotency_key, payload)
            if repeated:
                return repeated
            row = self._locked_session(connection, session_id, expected_revision)
            if Phase(row["phase"]) != Phase.DRAFT or SessionStatus(row["status"]) != SessionStatus.ACTIVE:
                raise ResearchDomainError("INVALID_PHASE", "Intake can only be edited before research starts")
            now = _timestamp()
            cursor = connection.execute(
                """UPDATE research_sessions SET question = ?, title = ?, perplexity_prompt = ?,
                   intake_version = intake_version + 1, revision = revision + 1, updated_at = ?
                   WHERE session_id = ? AND revision = ?""",
                (question, title, perplexity_prompt, now, str(session_id), expected_revision),
            )
            if cursor.rowcount != 1:
                raise StaleRevisionError()
            updated = self._session(connection.execute(
                "SELECT * FROM research_sessions WHERE session_id = ?", (str(session_id),)
            ).fetchone())
            self._event(connection, str(session_id), "session.intake_updated", None, updated.revision, now)
            self._store_idempotency(connection, "session.update_intake", idempotency_key, payload, updated)
            return updated

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
        assignments: Sequence[Assignment] = R0_ASSIGNMENTS,
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
                artifact = PromptComposer.verbatim(UUID(task_id), row["question"])
                if artifact.size_bytes > MAX_PROMPT_BYTES:
                    raise ResearchDomainError("TOO_LARGE", "Prompt artifact exceeds the size limit")
                connection.execute(
                    "INSERT INTO prompt_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(artifact.prompt_id), task_id, artifact.version, artifact.content,
                     artifact.size_bytes, artifact.sha256, artifact.created_at.isoformat()),
                )
            if row["perplexity_prompt"]:
                assignment = PERPLEXITY_SEARCH_ASSIGNMENT
                ordinal = len(assignments)
                connection.execute(
                    "INSERT INTO session_participants VALUES (?, ?, ?, ?, ?, 1)",
                    (str(uuid4()), str(session_id), assignment.provider_id, assignment.role, ordinal),
                )
                task_id = str(uuid4())
                connection.execute(
                    """INSERT INTO research_tasks VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?
                    )""",
                    (task_id, str(session_id), round_id, Phase.INDEPENDENT.value,
                     TaskKind.SEARCH_EVIDENCE.value, assignment.provider_id, assignment.role,
                     ordinal, TaskStatus.PROMPT_READY.value, now, now),
                )
                artifact = PromptComposer.verbatim(UUID(task_id), row["perplexity_prompt"])
                if artifact.size_bytes > MAX_PROMPT_BYTES:
                    raise ResearchDomainError("TOO_LARGE", "Perplexity prompt exceeds the size limit")
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
            task_sql = "SELECT status, gap_reason FROM research_tasks WHERE session_id = ? AND phase = ?"
            task_parameters: list[object] = [str(session_id), phase.value]
            if phase == Phase.INDEPENDENT:
                task_sql += " AND kind = ?"
                task_parameters.append(TaskKind.INDEPENDENT_RESPONSE.value)
            task_rows = connection.execute(task_sql + " ORDER BY ordinal", task_parameters).fetchall()
            has_gap = RoundManager.validate_gate(
                phase,
                [TaskStatus(item["status"]) for item in task_rows],
                gap_reason,
                [item["gap_reason"] for item in task_rows],
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
        require_active_independent: bool = False,
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
            if require_active_independent and (
                Phase(session_row["phase"]) != Phase.INDEPENDENT
                or SessionStatus(session_row["status"]) != SessionStatus.ACTIVE
            ):
                raise ResearchDomainError("INVALID_PHASE", "Task operation requires an active independent phase")
            task_row = connection.execute(
                "SELECT * FROM research_tasks WHERE task_id = ? AND session_id = ?",
                (str(task_id), str(session_id)),
            ).fetchone()
            if not task_row:
                raise ResearchDomainError("NOT_FOUND", "Research task was not found")
            if require_active_independent and Phase(task_row["phase"]) != Phase.INDEPENDENT:
                raise ResearchDomainError("INVALID_PHASE", "Task is not part of the independent phase")
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

    def get_prompt_metadata(self, session_id: UUID | str, task_id: UUID | str) -> PromptMetadata:
        with closing(self._connection()) as connection:
            row = connection.execute(
                """SELECT p.prompt_id, p.task_id, p.version, p.size_bytes, p.sha256
                   FROM prompt_artifacts p JOIN research_tasks t ON t.task_id = p.task_id
                   WHERE p.task_id = ? AND t.session_id = ?
                   ORDER BY p.version DESC LIMIT 1""",
                (str(task_id), str(session_id)),
            ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "Prompt artifact was not found")
        return PromptMetadata(**dict(row))

    def get_prompt_chunk(
        self, session_id: UUID | str, task_id: UUID | str, *, offset: int, limit: int
    ) -> tuple[bytes, int]:
        if offset < 0 or not 1 <= limit <= MAX_CHUNK_BYTES:
            raise ResearchDomainError("BAD_REQUEST", "Invalid prompt chunk range")
        with closing(self._connection()) as connection:
            row = connection.execute(
                """SELECT substr(CAST(p.prompt_text AS BLOB), ? + 1, ?) AS content,
                          length(CAST(p.prompt_text AS BLOB)) AS total_bytes
                   FROM prompt_artifacts p
                   JOIN research_tasks t ON t.task_id = p.task_id
                   WHERE p.task_id = ? AND t.session_id = ?
                   ORDER BY p.version DESC LIMIT 1""",
                (offset, limit, str(task_id), str(session_id)),
            ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "Prompt artifact was not found")
        total = row["total_bytes"]
        if offset > total:
            raise ResearchDomainError("BAD_REQUEST", "Prompt chunk offset exceeds content size")
        return bytes(row["content"]), total

    def get_prompt_text(self, session_id: UUID | str, task_id: UUID | str) -> str:
        with closing(self._connection()) as connection:
            row = connection.execute(
                """SELECT p.prompt_text FROM prompt_artifacts p
                   JOIN research_tasks t ON t.task_id = p.task_id
                   WHERE p.task_id = ? AND t.session_id = ?
                   ORDER BY p.version DESC LIMIT 1""",
                (str(task_id), str(session_id)),
            ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "Prompt artifact was not found")
        return str(row["prompt_text"])

    def begin_upload(
        self,
        session_id: UUID | str,
        task_id: UUID | str,
        *,
        mode: str,
        size_bytes: int,
        sha256: str,
        expected_revision: int,
        expected_result_version: int | None,
        idempotency_key: str,
    ) -> UploadReservation:
        if mode not in {"create", "revise"} or not 1 <= size_bytes <= MAX_RESPONSE_BYTES:
            raise ResearchDomainError("BAD_REQUEST", "Invalid response upload metadata")
        payload = {
            "session_id": str(session_id), "task_id": str(task_id), "mode": mode,
            "size_bytes": size_bytes, "sha256": sha256,
            "expected_revision": expected_revision,
            "expected_result_version": expected_result_version,
        }
        digest = _request_hash(payload)
        with self._transaction() as connection:
            now_dt = utcnow()
            now = _timestamp(now_dt)
            connection.execute("DELETE FROM response_uploads WHERE expires_at <= ?", (now,))
            existing = connection.execute(
                "SELECT * FROM response_uploads WHERE mode = ? AND idempotency_key = ?",
                (mode, idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != digest:
                    raise ResearchDomainError("BAD_REQUEST", "Idempotency key was used with different arguments")
                next_index = self._next_part_index(connection, existing["upload_id"])
                return self._upload(existing, next_index)
            session = self._locked_session(connection, session_id, expected_revision)
            task = self._active_independent_task(connection, session, task_id)
            result = connection.execute(
                "SELECT current_revision FROM research_results WHERE task_id = ?", (str(task_id),)
            ).fetchone()
            if mode == "create":
                if result or TaskStatus(task["status"]) not in {TaskStatus.PROMPT_READY, TaskStatus.AWAITING_USER}:
                    raise ResearchDomainError("INVALID_PHASE", "Task cannot accept an initial response")
            elif not result or TaskStatus(task["status"]) != TaskStatus.RECEIVED:
                raise ResearchDomainError("INVALID_PHASE", "Task has no response to revise")
            elif expected_result_version != result["current_revision"]:
                raise ResearchDomainError("STALE_REVISION", "Response changed in another window", retryable=True)
            open_count, staged = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM response_uploads WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
            if open_count >= MAX_OPEN_UPLOADS or staged + size_bytes > MAX_STAGING_BYTES:
                raise ResearchDomainError("TOO_LARGE", "Response staging quota exceeded")
            upload_id = str(uuid4())
            expires = _timestamp(now_dt + UPLOAD_TTL)
            connection.execute(
                """INSERT INTO response_uploads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (upload_id, str(session_id), str(task_id), mode, size_bytes, sha256,
                 expected_revision, expected_result_version, idempotency_key, digest, now, expires),
            )
            row = connection.execute("SELECT * FROM response_uploads WHERE upload_id = ?", (upload_id,)).fetchone()
            return self._upload(row, 0)

    def put_upload_part(
        self, upload_id: UUID | str, *, part_index: int, chunk: bytes, sha256: str
    ) -> UploadPartReceipt:
        if part_index < 0 or not 1 <= len(chunk) <= MAX_CHUNK_BYTES:
            raise ResearchDomainError("BAD_REQUEST", "Invalid response chunk")
        digest = hashlib.sha256(chunk).hexdigest()
        if digest != sha256:
            raise ResearchDomainError("BAD_REQUEST", "Response chunk hash mismatch")
        with self._transaction() as connection:
            upload = connection.execute("SELECT * FROM response_uploads WHERE upload_id = ?", (str(upload_id),)).fetchone()
            if not upload:
                raise ResearchDomainError("NOT_FOUND", "Response upload was not found or expired")
            if upload["expires_at"] <= _timestamp():
                connection.execute("DELETE FROM response_uploads WHERE upload_id = ?", (str(upload_id),))
                raise ResearchDomainError("NOT_FOUND", "Response upload was not found or expired")
            max_parts = (upload["size_bytes"] + MAX_CHUNK_BYTES - 1) // MAX_CHUNK_BYTES
            if part_index >= max_parts:
                raise ResearchDomainError("BAD_REQUEST", "Response chunk index exceeds the declared upload")
            existing = connection.execute(
                "SELECT size_bytes, sha256, chunk_blob FROM response_upload_parts WHERE upload_id = ? AND part_index = ?",
                (str(upload_id), part_index),
            ).fetchone()
            if existing:
                if existing["sha256"] != digest or bytes(existing["chunk_blob"]) != chunk:
                    raise ResearchDomainError("CHUNK_CONFLICT", "Chunk index already contains different data")
            else:
                staged_bytes = connection.execute(
                    "SELECT COALESCE(SUM(size_bytes), 0) FROM response_upload_parts WHERE upload_id = ?",
                    (str(upload_id),),
                ).fetchone()[0]
                if staged_bytes + len(chunk) > upload["size_bytes"]:
                    raise ResearchDomainError("TOO_LARGE", "Response chunks exceed the declared upload size")
                connection.execute(
                    "INSERT INTO response_upload_parts VALUES (?, ?, ?, ?, ?)",
                    (str(upload_id), part_index, chunk, len(chunk), digest),
                )
            return UploadPartReceipt(
                upload_id=upload_id, part_index=part_index,
                next_index=self._next_part_index(connection, str(upload_id)),
            )

    def abort_upload(self, upload_id: UUID | str) -> bool:
        with self._transaction() as connection:
            return connection.execute(
                "DELETE FROM response_uploads WHERE upload_id = ?", (str(upload_id),)
            ).rowcount > 0

    def commit_upload(
        self,
        upload_id: UUID | str,
        *,
        session_id: UUID | str,
        task_id: UUID | str,
        expected_revision: int,
        idempotency_key: str,
        source_type: str,
        reported_model: str | None,
    ) -> ResponseReceipt:
        payload = {
            "upload_id": str(upload_id), "session_id": str(session_id), "task_id": str(task_id),
            "expected_revision": expected_revision, "source_type": source_type,
            "reported_model": reported_model,
        }
        digest = _request_hash(payload)
        operation = "response.upload.commit"
        with self._transaction() as connection:
            receipt = connection.execute(
                "SELECT request_hash, receipt_json FROM operation_receipts WHERE operation = ? AND idempotency_key = ?",
                (operation, idempotency_key),
            ).fetchone()
            if receipt:
                if receipt["request_hash"] != digest:
                    raise ResearchDomainError("BAD_REQUEST", "Idempotency key was used with different arguments")
                return ResponseReceipt.model_validate_json(receipt["receipt_json"])
            upload = connection.execute("SELECT * FROM response_uploads WHERE upload_id = ?", (str(upload_id),)).fetchone()
            if not upload:
                raise ResearchDomainError("NOT_FOUND", "Response upload was not found or expired")
            if upload["expires_at"] <= _timestamp():
                connection.execute("DELETE FROM response_uploads WHERE upload_id = ?", (str(upload_id),))
                raise ResearchDomainError("NOT_FOUND", "Response upload was not found or expired")
            if upload["idempotency_key"] != idempotency_key:
                raise ResearchDomainError("BAD_REQUEST", "Upload idempotency key does not match")
            if (upload["session_id"] != str(session_id) or upload["task_id"] != str(task_id)
                    or upload["expected_revision"] != expected_revision):
                raise ResearchDomainError("BAD_REQUEST", "Upload does not match the requested task or revision")
            session = self._locked_session(connection, upload["session_id"], upload["expected_revision"])
            task = self._active_independent_task(connection, session, upload["task_id"])
            parts = connection.execute(
                "SELECT * FROM response_upload_parts WHERE upload_id = ? ORDER BY part_index", (str(upload_id),)
            ).fetchall()
            if [row["part_index"] for row in parts] != list(range(len(parts))):
                raise ResearchDomainError("BAD_REQUEST", "Response upload has missing chunks")
            content = b"".join(bytes(row["chunk_blob"]) for row in parts)
            if len(content) != upload["size_bytes"] or hashlib.sha256(content).hexdigest() != upload["sha256"]:
                raise ResearchDomainError("BAD_REQUEST", "Response upload failed integrity validation")
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ResearchDomainError("BAD_REQUEST", "Response is not valid UTF-8") from exc
            if not text.strip():
                raise ResearchDomainError("BAD_REQUEST", "Response text must not be empty")
            now = _timestamp()
            result = connection.execute("SELECT * FROM research_results WHERE task_id = ?", (upload["task_id"],)).fetchone()
            if upload["mode"] == "create":
                if result:
                    raise ResearchDomainError("INVALID_PHASE", "Task already has a response")
                result_id = str(uuid4())
                version = 1
                connection.execute(
                    "INSERT INTO research_results VALUES (?, ?, ?, ?, ?, ?)",
                    (result_id, upload["task_id"], source_type, reported_model, version, now),
                )
            else:
                if not result or result["current_revision"] != upload["expected_result_version"]:
                    raise ResearchDomainError("STALE_REVISION", "Response changed in another window", retryable=True)
                result_id = result["result_id"]
                version = result["current_revision"] + 1
                connection.execute(
                    "UPDATE research_results SET current_revision = ?, source_type = ?, reported_model = ? WHERE result_id = ?",
                    (version, source_type, reported_model, result_id),
                )
            revision_id = str(uuid4())
            connection.execute(
                "INSERT INTO response_revisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (revision_id, result_id, version, text, len(content), upload["sha256"], now),
            )
            if upload["mode"] == "create":
                current = TaskStatus(task["status"])
                if current == TaskStatus.PROMPT_READY:
                    require_task_transition(current, TaskStatus.AWAITING_USER)
                    current = TaskStatus.AWAITING_USER
                require_task_transition(current, TaskStatus.RECEIVED)
                connection.execute(
                    "UPDATE research_tasks SET status = ?, gap_reason = NULL, revision = revision + 1, updated_at = ? WHERE task_id = ?",
                    (TaskStatus.RECEIVED.value, now, upload["task_id"]),
                )
            updated = self._update_session(
                connection, session, phase=Phase(session["phase"]), status=SessionStatus(session["status"]),
                gap_reason=session["gap_reason"], now=now,
            )
            self._event(connection, session["session_id"], "response.committed", result_id, updated.revision, now)
            task_row = connection.execute("SELECT * FROM research_tasks WHERE task_id = ?", (upload["task_id"],)).fetchone()
            response = ResponseMetadata(
                result_id=result_id, task_id=upload["task_id"], version=version,
                source_type=source_type, reported_model=reported_model,
                size_bytes=len(content), sha256=upload["sha256"], created_at=now,
            )
            result_receipt = ResponseReceipt(response=response, task=self._task(task_row), session_revision=updated.revision)
            connection.execute(
                "INSERT INTO operation_receipts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (operation, idempotency_key, session["session_id"], upload["task_id"], digest,
                 result_receipt.model_dump_json(), now),
            )
            connection.execute("DELETE FROM response_uploads WHERE upload_id = ?", (str(upload_id),))
            return result_receipt

    def list_responses(self, session_id: UUID | str, task_id: UUID | str | None = None) -> list[ResponseMetadata]:
        sql = """SELECT r.result_id, r.task_id, r.current_revision AS version, r.source_type,
                        r.reported_model, v.size_bytes, v.sha256, v.created_at
                 FROM research_results r
                 JOIN research_tasks t ON t.task_id = r.task_id
                 JOIN response_revisions v ON v.result_id = r.result_id AND v.version = r.current_revision
                 WHERE t.session_id = ?"""
        params: list[object] = [str(session_id)]
        if task_id is not None:
            sql += " AND r.task_id = ?"
            params.append(str(task_id))
        sql += " ORDER BY t.ordinal"
        with closing(self._connection()) as connection:
            rows = connection.execute(sql, params).fetchall()
        return [ResponseMetadata(**dict(row)) for row in rows]

    def get_response_chunk(
        self, session_id: UUID | str, result_id: UUID | str, *, version: int, offset: int, limit: int
    ) -> tuple[bytes, int]:
        if version < 1 or offset < 0 or not 1 <= limit <= MAX_CHUNK_BYTES:
            raise ResearchDomainError("BAD_REQUEST", "Invalid response chunk range")
        with closing(self._connection()) as connection:
            row = connection.execute(
                """SELECT substr(CAST(v.response_text AS BLOB), ? + 1, ?) AS content,
                          length(CAST(v.response_text AS BLOB)) AS total_bytes
                   FROM response_revisions v
                   JOIN research_results r ON r.result_id = v.result_id
                   JOIN research_tasks t ON t.task_id = r.task_id
                   WHERE v.result_id = ? AND v.version = ? AND t.session_id = ?""",
                (offset, limit, str(result_id), version, str(session_id)),
            ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "Response revision was not found")
        total = row["total_bytes"]
        if offset > total:
            raise ResearchDomainError("BAD_REQUEST", "Response chunk offset exceeds content size")
        return bytes(row["content"]), total

    def reassign_task(
        self,
        session_id: UUID | str,
        task_id: UUID | str,
        *,
        provider_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> ResearchTask:
        if not is_approved_provider(provider_id):
            raise ResearchDomainError("BAD_REQUEST", "Provider is not approved")
        payload = {"session_id": str(session_id), "task_id": str(task_id), "provider_id": provider_id,
                   "expected_revision": expected_revision}
        operation = "task.reassign"
        with self._transaction() as connection:
            prior = connection.execute(
                "SELECT request_hash FROM idempotency_operations WHERE operation = ? AND idempotency_key = ?",
                (operation, idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_hash"] != _request_hash(payload):
                    raise ResearchDomainError("BAD_REQUEST", "Idempotency key was used with different arguments")
                row = connection.execute("SELECT * FROM research_tasks WHERE task_id = ?", (str(task_id),)).fetchone()
                return self._task(row)
            session = self._locked_session(connection, session_id, expected_revision)
            task = self._active_independent_task(connection, session, task_id)
            if TaskStatus(task["status"]) not in {
                TaskStatus.PENDING, TaskStatus.PROMPT_READY, TaskStatus.AWAITING_USER,
                TaskStatus.UNAVAILABLE, TaskStatus.FAILED,
            } or connection.execute(
                "SELECT 1 FROM research_results WHERE task_id = ?", (str(task_id),)
            ).fetchone():
                raise ResearchDomainError("INVALID_PHASE", "Received task cannot be reassigned")
            duplicate = connection.execute(
                "SELECT 1 FROM research_tasks WHERE session_id = ? AND phase = ? AND provider_id = ? AND task_id <> ?",
                (str(session_id), Phase.INDEPENDENT.value, provider_id, str(task_id)),
            ).fetchone()
            if duplicate:
                raise ResearchDomainError("BAD_REQUEST", "Provider is already assigned in this round")
            now = _timestamp()
            connection.execute(
                "UPDATE research_tasks SET provider_id = ?, status = ?, gap_reason = NULL, revision = revision + 1, updated_at = ? WHERE task_id = ?",
                (provider_id, TaskStatus.PROMPT_READY.value, now, str(task_id)),
            )
            connection.execute(
                "UPDATE session_participants SET provider_id = ? WHERE session_id = ? AND ordinal = ?",
                (provider_id, str(session_id), task["ordinal"]),
            )
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM prompt_artifacts WHERE task_id = ?", (str(task_id),)
            ).fetchone()[0]
            artifact = PromptComposer.verbatim(UUID(str(task_id)), session["question"], version=version)
            if artifact.size_bytes > MAX_PROMPT_BYTES:
                raise ResearchDomainError("TOO_LARGE", "Prompt artifact exceeds the size limit")
            connection.execute(
                "INSERT INTO prompt_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(artifact.prompt_id), str(task_id), artifact.version, artifact.content,
                 artifact.size_bytes, artifact.sha256, artifact.created_at.isoformat()),
            )
            updated = self._update_session(
                connection, session, phase=Phase(session["phase"]), status=SessionStatus(session["status"]),
                gap_reason=session["gap_reason"], now=now,
            )
            self._event(connection, str(session_id), "task.reassigned", str(task_id), updated.revision, now)
            self._store_idempotency(connection, operation, idempotency_key, payload, updated)
            row = connection.execute("SELECT * FROM research_tasks WHERE task_id = ?", (str(task_id),)).fetchone()
            return self._task(row)

    @staticmethod
    def _active_independent_task(
        connection: sqlite3.Connection, session: sqlite3.Row, task_id: UUID | str
    ) -> sqlite3.Row:
        if Phase(session["phase"]) != Phase.INDEPENDENT or SessionStatus(session["status"]) != SessionStatus.ACTIVE:
            raise ResearchDomainError("INVALID_PHASE", "Operation requires an active independent phase")
        task = connection.execute(
            "SELECT * FROM research_tasks WHERE task_id = ? AND session_id = ? AND phase = ?",
            (str(task_id), session["session_id"], Phase.INDEPENDENT.value),
        ).fetchone()
        if not task:
            raise ResearchDomainError("NOT_FOUND", "Independent research task was not found")
        return task

    @staticmethod
    def _next_part_index(connection: sqlite3.Connection, upload_id: str) -> int:
        indexes = {row[0] for row in connection.execute(
            "SELECT part_index FROM response_upload_parts WHERE upload_id = ?", (upload_id,)
        )}
        index = 0
        while index in indexes:
            index += 1
        return index

    @staticmethod
    def _upload(row: sqlite3.Row, next_index: int) -> UploadReservation:
        return UploadReservation(
            upload_id=row["upload_id"], task_id=row["task_id"], mode=row["mode"],
            size_bytes=row["size_bytes"], sha256=row["sha256"], next_index=next_index,
            expires_at=row["expires_at"],
        )

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
            perplexity_prompt=row["perplexity_prompt"], intake_version=row["intake_version"],
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

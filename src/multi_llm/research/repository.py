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
    ReviewerPackageMetadata,
    ReviewerPrepareReceipt,
    S2PackageMetadata,
    S2PrepareReceipt,
    S2ReviewReference,
    S1InputReference,
    S1PackageMetadata,
    S1PackagePartMetadata,
    S1PrepareReceipt,
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
from .synthesis import S1_TEMPLATE_VERSION, SynthesisInput, build_s1_package
from .reviewer import (
    REVIEWER_PROVIDERS,
    REVIEWER_TEMPLATE_VERSION,
    ReviewerS1Reference,
    build_reviewer_package,
)
from .finalization import (
    S2_CONTEXT_WARNING_BYTES,
    S2_REVIEW_PROVIDERS,
    S2_TEMPLATE_VERSION,
    S2ReviewInput,
    build_s2_package,
)
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
                (round_id, str(session_id), Phase.R0.value, "ACTIVE", now),
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
                    (task_id, str(session_id), round_id, Phase.R0.value, "R0_RESPONSE",
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
                    (task_id, str(session_id), round_id, Phase.R0.value,
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
                connection, row, phase=Phase.R0, status=SessionStatus.ACTIVE, gap_reason=None, now=now
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
            if phase == Phase.S2:
                raise ResearchDomainError(
                    "INVALID_PHASE", "Import a valid S2 to complete the research"
                )
            task_sql = "SELECT status, gap_reason FROM research_tasks WHERE session_id = ? AND phase = ?"
            task_parameters: list[object] = [str(session_id), phase.value]
            if phase == Phase.R0:
                task_sql += " AND kind = ?"
                task_parameters.append(TaskKind.R0_RESPONSE.value)
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
                if target == Phase.S1:
                    self._create_s1_task_and_package(
                        connection, row, round_id, now, gap_reason=combined_gap
                    )
                elif target == Phase.S1_REVIEW:
                    self._create_reviewer_tasks_and_packages(connection, row, round_id, now)
                elif target == Phase.S2:
                    self._create_s2_task_and_package(connection, row, round_id, now)
            updated = self._update_session(
                connection, row, phase=target, status=target_status, gap_reason=combined_gap, now=now
            )
            self._event(connection, str(session_id), "session.advanced", round_id, updated.revision, now)
            self._store_idempotency(connection, "session.advance", idempotency_key, payload, updated)
            return updated

    def prepare_s1(
        self,
        session_id: UUID | str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> S1PrepareReceipt:
        payload = {"session_id": str(session_id), "expected_revision": expected_revision}
        with self._transaction() as connection:
            repeated = self._idempotent_result(
                connection, "s1.package.prepare", idempotency_key, payload
            )
            if repeated:
                return S1PrepareReceipt(
                    package=self._get_s1_package(connection, session_id), session=repeated
                )
            session = self._locked_session(connection, session_id, expected_revision)
            if (Phase(session["phase"]) != Phase.S1
                    or SessionStatus(session["status"]) != SessionStatus.ACTIVE):
                raise ResearchDomainError(
                    "INVALID_PHASE", "S1 can only be prepared in the active S1 phase"
                )
            existing = connection.execute(
                "SELECT 1 FROM s1_packages WHERE session_id = ?", (str(session_id),)
            ).fetchone()
            if existing:
                current = self._session(session)
                package = self._get_s1_package(connection, session_id)
                self._store_idempotency(
                    connection, "s1.package.prepare", idempotency_key, payload, current
                )
                return S1PrepareReceipt(package=package, session=current)
            round_row = connection.execute(
                """SELECT round_id FROM research_rounds
                   WHERE session_id = ? AND phase = ? ORDER BY ordinal DESC LIMIT 1""",
                (str(session_id), Phase.S1.value),
            ).fetchone()
            if not round_row:
                raise ResearchDomainError("NOT_FOUND", "S1 round was not found")
            self._create_s1_task_and_package(
                connection, session, round_row["round_id"], _timestamp(),
                gap_reason=session["gap_reason"],
            )
            now = _timestamp()
            updated = self._update_session(
                connection, session, phase=Phase.S1,
                status=SessionStatus.ACTIVE, gap_reason=session["gap_reason"], now=now,
            )
            package = self._get_s1_package(connection, session_id)
            self._event(
                connection, str(session_id), "s1.package_prepared",
                str(package.package_id), updated.revision, now,
            )
            self._store_idempotency(
                connection, "s1.package.prepare", idempotency_key, payload, updated
            )
            return S1PrepareReceipt(package=package, session=updated)

    def get_s1_package(self, session_id: UUID | str) -> S1PackageMetadata:
        with closing(self._connection()) as connection:
            return self._get_s1_package(connection, session_id)

    def get_s1_part_chunk(
        self,
        session_id: UUID | str,
        package_id: UUID | str,
        *,
        part_index: int,
        offset: int,
        limit: int,
    ) -> tuple[bytes, int]:
        if part_index < 0 or offset < 0 or not 1 <= limit <= MAX_CHUNK_BYTES:
            raise ResearchDomainError("BAD_REQUEST", "Invalid S1 package part range")
        with closing(self._connection()) as connection:
            row = connection.execute(
                """SELECT substr(CAST(p.prompt_text AS BLOB), ? + 1, ?) AS content,
                          length(CAST(p.prompt_text AS BLOB)) AS total_bytes
                   FROM s1_package_parts sp
                   JOIN s1_packages s ON s.package_id = sp.package_id
                   JOIN prompt_artifacts p ON p.prompt_id = sp.prompt_id
                   WHERE sp.package_id = ? AND sp.part_index = ? AND s.session_id = ?""",
                (offset, limit, str(package_id), part_index, str(session_id)),
            ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "S1 package part was not found")
        total = row["total_bytes"]
        if offset > total:
            raise ResearchDomainError("BAD_REQUEST", "S1 package part offset exceeds content size")
        return bytes(row["content"]), total

    def get_s1_part_text(
        self, session_id: UUID | str, package_id: UUID | str, part_index: int
    ) -> str:
        with closing(self._connection()) as connection:
            row = connection.execute(
                """SELECT p.prompt_text FROM s1_package_parts sp
                   JOIN s1_packages s ON s.package_id = sp.package_id
                   JOIN prompt_artifacts p ON p.prompt_id = sp.prompt_id
                   WHERE sp.package_id = ? AND sp.part_index = ? AND s.session_id = ?""",
                (str(package_id), part_index, str(session_id)),
            ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "S1 package part was not found")
        return str(row["prompt_text"])

    def prepare_reviewers(
        self,
        session_id: UUID | str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> ReviewerPrepareReceipt:
        payload = {"session_id": str(session_id), "expected_revision": expected_revision}
        with self._transaction() as connection:
            repeated = self._idempotent_result(
                connection, "review.package.prepare", idempotency_key, payload
            )
            if repeated:
                return ReviewerPrepareReceipt(
                    packages=self._current_reviewer_packages(connection, session_id),
                    session=repeated,
                )
            session = self._locked_session(connection, session_id, expected_revision)
            if (Phase(session["phase"]) not in {Phase.S1_REVIEW, Phase.S2}
                    or SessionStatus(session["status"]) != SessionStatus.ACTIVE):
                raise ResearchDomainError(
                    "INVALID_PHASE", "Reviewer packages require an active review or S2 recovery phase"
                )
            s1 = self._current_s1_reference(connection, session)
            existing = self._reviewer_packages_for_s1(
                connection, session_id, s1.result_id, s1.result_version
            )
            if len(existing) == len(REVIEWER_PROVIDERS):
                current = self._session(session)
                self._store_idempotency(
                    connection, "review.package.prepare", idempotency_key, payload, current
                )
                return ReviewerPrepareReceipt(packages=existing, session=current)
            now = _timestamp()
            connection.execute(
                """UPDATE research_tasks SET status = ?, revision = revision + 1, updated_at = ?
                   WHERE session_id = ? AND phase = ? AND kind = ? AND status != ?""",
                (TaskStatus.STALE.value, now, str(session_id), Phase.S1_REVIEW.value,
                 TaskKind.S1_REVIEW.value, TaskStatus.STALE.value),
            )
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM research_rounds WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()[0]
            round_id = str(uuid4())
            connection.execute(
                "INSERT INTO research_rounds VALUES (?, ?, ?, ?, ?, ?)",
                (round_id, str(session_id), Phase.S1_REVIEW.value, ordinal, "ACTIVE", now),
            )
            self._create_reviewer_tasks_and_packages(connection, session, round_id, now, s1=s1)
            updated = self._update_session(
                connection, session, phase=Phase.S1_REVIEW, status=SessionStatus.ACTIVE,
                gap_reason=session["gap_reason"], now=now,
            )
            packages = self._reviewer_packages_for_s1(
                connection, session_id, s1.result_id, s1.result_version
            )
            self._event(
                connection, str(session_id), "review.packages_prepared", round_id,
                updated.revision, now,
            )
            self._store_idempotency(
                connection, "review.package.prepare", idempotency_key, payload, updated
            )
            return ReviewerPrepareReceipt(packages=packages, session=updated)

    def get_reviewer_packages(self, session_id: UUID | str) -> list[ReviewerPackageMetadata]:
        with closing(self._connection()) as connection:
            rows = connection.execute(
                """SELECT package_id FROM reviewer_packages WHERE session_id = ?
                   ORDER BY created_at,
                     CASE provider_id WHEN 'deepseek' THEN 0 WHEN 'grok' THEN 1 ELSE 2 END""",
                (str(session_id),),
            ).fetchall()
            return [self._get_reviewer_package(connection, row["package_id"]) for row in rows]

    def get_reviewer_part_chunk(
        self,
        session_id: UUID | str,
        package_id: UUID | str,
        *,
        part_index: int,
        offset: int,
        limit: int,
    ) -> tuple[bytes, int]:
        if part_index < 0 or offset < 0 or not 1 <= limit <= MAX_CHUNK_BYTES:
            raise ResearchDomainError("BAD_REQUEST", "Invalid reviewer package part range")
        with closing(self._connection()) as connection:
            row = connection.execute(
                """SELECT substr(CAST(p.prompt_text AS BLOB), ? + 1, ?) AS content,
                          length(CAST(p.prompt_text AS BLOB)) AS total_bytes
                   FROM reviewer_package_parts rp
                   JOIN reviewer_packages r ON r.package_id = rp.package_id
                   JOIN prompt_artifacts p ON p.prompt_id = rp.prompt_id
                   WHERE rp.package_id = ? AND rp.part_index = ? AND r.session_id = ?""",
                (offset, limit, str(package_id), part_index, str(session_id)),
            ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "Reviewer package part was not found")
        total = row["total_bytes"]
        if offset > total:
            raise ResearchDomainError("BAD_REQUEST", "Reviewer package part offset exceeds content size")
        return bytes(row["content"]), total

    def get_reviewer_part_text(
        self, session_id: UUID | str, package_id: UUID | str, part_index: int
    ) -> str:
        with closing(self._connection()) as connection:
            row = connection.execute(
                """SELECT p.prompt_text FROM reviewer_package_parts rp
                   JOIN reviewer_packages r ON r.package_id = rp.package_id
                   JOIN prompt_artifacts p ON p.prompt_id = rp.prompt_id
                   WHERE rp.package_id = ? AND rp.part_index = ? AND r.session_id = ?""",
                (str(package_id), part_index, str(session_id)),
            ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "Reviewer package part was not found")
        return str(row["prompt_text"])

    def _create_s1_task_and_package(
        self,
        connection: sqlite3.Connection,
        session: sqlite3.Row,
        round_id: str,
        now: str,
        *,
        gap_reason: str | None,
    ) -> None:
        existing = connection.execute(
            "SELECT 1 FROM s1_packages WHERE session_id = ?", (session["session_id"],)
        ).fetchone()
        if existing:
            return
        inputs = self._collect_s1_inputs(connection, session, gap_reason=gap_reason)
        package = build_s1_package(session["question"], inputs)
        task_ordinal = connection.execute(
            "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM research_tasks WHERE round_id = ?",
            (round_id,),
        ).fetchone()[0]
        task_id = str(uuid4())
        connection.execute(
            """INSERT INTO research_tasks VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?
            )""",
            (task_id, session["session_id"], round_id, Phase.S1.value,
             TaskKind.S1_SYNTHESIS.value, "chatgpt", "s1_synthesizer", task_ordinal,
             TaskStatus.PROMPT_READY.value, now, now),
        )
        package_id = str(uuid4())
        connection.execute(
            "INSERT INTO s1_packages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (package_id, session["session_id"], task_id, S1_TEMPLATE_VERSION,
             package.input_fingerprint,
             package.package_sha256, package.total_bytes, len(package.parts),
             int(package.r0_complete), now),
        )
        for item in inputs:
            connection.execute(
                "INSERT INTO s1_package_inputs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (package_id, item.ordinal, item.provider_id, item.reported_model,
                 item.task_id, item.status,
                 item.result_id, item.result_version, item.sha256, item.gap_reason),
            )
        for index, content in enumerate(package.parts):
            artifact = PromptComposer.verbatim(UUID(task_id), content, version=index + 1)
            connection.execute(
                "INSERT INTO prompt_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(artifact.prompt_id), task_id, artifact.version, artifact.content,
                 artifact.size_bytes, artifact.sha256, artifact.created_at.isoformat()),
            )
            connection.execute(
                "INSERT INTO s1_package_parts VALUES (?, ?, ?, ?, ?)",
                (package_id, index, str(artifact.prompt_id), artifact.size_bytes, artifact.sha256),
            )

    @staticmethod
    def _collect_s1_inputs(
        connection: sqlite3.Connection, session: sqlite3.Row, *, gap_reason: str | None = None
    ) -> list[SynthesisInput]:
        rows = connection.execute(
            """SELECT t.ordinal, t.provider_id, t.task_id, t.status, t.gap_reason,
                      r.reported_model,
                      r.result_id, r.current_revision AS result_version, v.sha256, v.response_text
               FROM research_tasks t
               LEFT JOIN research_results r ON r.task_id = t.task_id
               LEFT JOIN response_revisions v
                 ON v.result_id = r.result_id AND v.version = r.current_revision
               WHERE t.session_id = ? AND t.phase = ? AND t.kind = ?
               ORDER BY t.ordinal""",
            (session["session_id"], Phase.R0.value,
             TaskKind.R0_RESPONSE.value),
        ).fetchall()
        expected = [item.provider_id for item in R0_ASSIGNMENTS]
        if [row["provider_id"] for row in rows] != expected:
            raise ResearchDomainError(
                "INVALID_PHASE", "S1 requires the fixed ordered set of eight R0 providers"
            )
        RoundManager.validate_gate(
            Phase.R0,
            [TaskStatus(row["status"]) for row in rows],
            gap_reason if gap_reason is not None else session["gap_reason"],
            [row["gap_reason"] for row in rows],
        )
        if any(
            TaskStatus(row["status"]) == TaskStatus.RECEIVED
            and (not row["result_id"] or row["response_text"] is None)
            for row in rows
        ):
            raise ResearchDomainError(
                "INVALID_PHASE", "Every received R0 task must have a saved response revision"
            )
        inputs = [
            SynthesisInput(
                ordinal=index, provider_id=row["provider_id"],
                reported_model=row["reported_model"], task_id=row["task_id"],
                status=row["status"], result_id=row["result_id"],
                result_version=row["result_version"], sha256=row["sha256"],
                gap_reason=row["gap_reason"], text=row["response_text"],
            )
            for index, row in enumerate(rows)
        ]
        search = connection.execute(
            """SELECT t.provider_id, t.task_id, t.status, t.gap_reason, r.reported_model,
                      r.result_id, r.current_revision AS result_version, v.sha256, v.response_text
               FROM research_tasks t
               LEFT JOIN research_results r ON r.task_id = t.task_id
               LEFT JOIN response_revisions v
                 ON v.result_id = r.result_id AND v.version = r.current_revision
               WHERE t.session_id = ? AND t.phase = ? AND t.kind = ? LIMIT 1""",
            (session["session_id"], Phase.R0.value, TaskKind.SEARCH_EVIDENCE.value),
        ).fetchone()
        if search:
            inputs.append(SynthesisInput(
                ordinal=len(inputs), provider_id="perplexity",
                reported_model=search["reported_model"], task_id=search["task_id"],
                status=search["status"], result_id=search["result_id"],
                result_version=search["result_version"], sha256=search["sha256"],
                gap_reason=search["gap_reason"], text=search["response_text"],
            ))
        return inputs

    def _get_s1_package(
        self, connection: sqlite3.Connection, session_id: UUID | str
    ) -> S1PackageMetadata:
        row = connection.execute(
            "SELECT * FROM s1_packages WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (str(session_id),),
        ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "S1 package has not been prepared")
        input_rows = connection.execute(
            "SELECT * FROM s1_package_inputs WHERE package_id = ? ORDER BY ordinal",
            (row["package_id"],),
        ).fetchall()
        part_rows = connection.execute(
            "SELECT * FROM s1_package_parts WHERE package_id = ? ORDER BY part_index",
            (row["package_id"],),
        ).fetchall()
        current = connection.execute(
            "SELECT * FROM research_sessions WHERE session_id = ?", (str(session_id),)
        ).fetchone()
        stale = False
        if current:
            fresh = build_s1_package(current["question"], self._collect_s1_inputs(connection, current))
            stale = fresh.input_fingerprint != row["input_fingerprint"]
        return S1PackageMetadata(
            package_id=row["package_id"], session_id=row["session_id"], task_id=row["task_id"],
            template_version=row["template_version"],
            input_fingerprint=row["input_fingerprint"], package_sha256=row["package_sha256"],
            total_bytes=row["total_bytes"], part_count=row["part_count"],
            r0_complete=bool(row["r0_complete"]), stale=stale,
            inputs=[S1InputReference(
                ordinal=item["ordinal"], provider_id=item["provider_id"],
                reported_model=item["reported_model"], task_id=item["task_id"],
                status=item["status"], result_id=item["result_id"],
                result_version=item["result_version"], sha256=item["sha256"],
                gap_reason=item["gap_reason"],
            ) for item in input_rows],
            parts=[S1PackagePartMetadata(
                part_index=item["part_index"], prompt_id=item["prompt_id"],
                size_bytes=item["size_bytes"], sha256=item["sha256"],
            ) for item in part_rows],
            created_at=row["created_at"],
        )

    def _current_s1_reference(
        self, connection: sqlite3.Connection, session: sqlite3.Row
    ) -> ReviewerS1Reference:
        row = connection.execute(
            """SELECT r.result_id, r.current_revision, v.sha256, v.response_text,
                      l.package_id, p.input_fingerprint, t.status
               FROM research_tasks t
               JOIN research_results r ON r.task_id = t.task_id
               JOIN response_revisions v
                 ON v.result_id = r.result_id AND v.version = r.current_revision
               JOIN s1_result_links l ON l.result_id = r.result_id
               JOIN s1_packages p ON p.package_id = l.package_id
               WHERE t.session_id = ? AND t.kind = ?
               ORDER BY t.created_at DESC LIMIT 1""",
            (session["session_id"], TaskKind.S1_SYNTHESIS.value),
        ).fetchone()
        if not row or TaskStatus(row["status"]) != TaskStatus.RECEIVED:
            raise ResearchDomainError(
                "INVALID_PHASE", "A saved S1 revision linked to its package is required"
            )
        package = self._get_s1_package(connection, session["session_id"])
        if str(package.package_id) != row["package_id"] or package.stale:
            raise ResearchDomainError(
                "STALE_REVISION", "S1 or its source package is stale; prepare a current S1 first",
                retryable=True,
            )
        return ReviewerS1Reference(
            result_id=row["result_id"], result_version=row["current_revision"],
            sha256=row["sha256"], package_id=row["package_id"],
            package_fingerprint=row["input_fingerprint"], text=row["response_text"],
        )

    @staticmethod
    def _reviewer_inputs(
        connection: sqlite3.Connection, s1_package_id: str
    ) -> list[SynthesisInput]:
        rows = connection.execute(
            """SELECT i.*, v.response_text
               FROM s1_package_inputs i
               LEFT JOIN response_revisions v
                 ON v.result_id = i.result_id AND v.version = i.result_version
               WHERE i.package_id = ? ORDER BY i.ordinal""",
            (s1_package_id,),
        ).fetchall()
        if not rows:
            raise ResearchDomainError("INVALID_PHASE", "S1 package has no immutable source manifest")
        if any(row["result_id"] and row["response_text"] is None for row in rows):
            raise ResearchDomainError(
                "INVALID_PHASE", "An immutable S1 source revision is unavailable"
            )
        return [SynthesisInput(
            ordinal=row["ordinal"], provider_id=row["provider_id"],
            reported_model=row["reported_model"], task_id=row["task_id"],
            status=row["status"], result_id=row["result_id"],
            result_version=row["result_version"], sha256=row["sha256"],
            gap_reason=row["gap_reason"], text=row["response_text"],
        ) for row in rows]

    def _create_reviewer_tasks_and_packages(
        self,
        connection: sqlite3.Connection,
        session: sqlite3.Row,
        round_id: str,
        now: str,
        *,
        s1: ReviewerS1Reference | None = None,
    ) -> None:
        s1 = s1 or self._current_s1_reference(connection, session)
        existing = self._reviewer_packages_for_s1(
            connection, session["session_id"], s1.result_id, s1.result_version
        )
        if existing:
            if len(existing) != len(REVIEWER_PROVIDERS):
                raise ResearchDomainError("DATABASE_ERROR", "Reviewer package set is incomplete")
            return
        inputs = self._reviewer_inputs(connection, s1.package_id)
        package = build_reviewer_package(session["question"], s1, inputs)
        for ordinal, provider_id in enumerate(REVIEWER_PROVIDERS):
            task_id = str(uuid4())
            connection.execute(
                """INSERT INTO research_tasks VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?
                )""",
                (task_id, session["session_id"], round_id, Phase.S1_REVIEW.value,
                 TaskKind.S1_REVIEW.value, provider_id, "s1_reviewer", ordinal,
                 TaskStatus.PROMPT_READY.value, now, now),
            )
            package_id = str(uuid4())
            connection.execute(
                """INSERT INTO reviewer_packages VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )""",
                (package_id, session["session_id"], task_id, provider_id,
                 REVIEWER_TEMPLATE_VERSION, s1.result_id, s1.result_version,
                 s1.package_id, s1.package_fingerprint, package.input_fingerprint,
                 package.package_sha256, package.total_bytes, len(package.parts),
                 int(package.r0_complete), now),
            )
            for item in inputs:
                connection.execute(
                    "INSERT INTO reviewer_package_inputs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (package_id, item.ordinal, item.provider_id, item.reported_model,
                     item.task_id, item.status, item.result_id, item.result_version,
                     item.sha256, item.gap_reason),
                )
            for part_index, content in enumerate(package.parts):
                artifact = PromptComposer.verbatim(UUID(task_id), content, version=part_index + 1)
                connection.execute(
                    "INSERT INTO prompt_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(artifact.prompt_id), task_id, artifact.version, artifact.content,
                     artifact.size_bytes, artifact.sha256, artifact.created_at.isoformat()),
                )
                connection.execute(
                    "INSERT INTO reviewer_package_parts VALUES (?, ?, ?, ?, ?)",
                    (package_id, part_index, str(artifact.prompt_id),
                     artifact.size_bytes, artifact.sha256),
                )

    def _reviewer_packages_for_s1(
        self,
        connection: sqlite3.Connection,
        session_id: UUID | str,
        result_id: str,
        result_version: int,
    ) -> list[ReviewerPackageMetadata]:
        rows = connection.execute(
            """SELECT package_id FROM reviewer_packages
               WHERE session_id = ? AND s1_result_id = ? AND s1_result_version = ?
               ORDER BY CASE provider_id WHEN 'deepseek' THEN 0 WHEN 'grok' THEN 1 ELSE 2 END""",
            (str(session_id), result_id, result_version),
        ).fetchall()
        return [self._get_reviewer_package(connection, row["package_id"]) for row in rows]

    def _current_reviewer_packages(
        self, connection: sqlite3.Connection, session_id: UUID | str
    ) -> list[ReviewerPackageMetadata]:
        session = connection.execute(
            "SELECT * FROM research_sessions WHERE session_id = ?", (str(session_id),)
        ).fetchone()
        if not session:
            raise ResearchDomainError("NOT_FOUND", "Research session was not found")
        s1 = self._current_s1_reference(connection, session)
        return self._reviewer_packages_for_s1(
            connection, session_id, s1.result_id, s1.result_version
        )

    def _get_reviewer_package(
        self, connection: sqlite3.Connection, package_id: UUID | str
    ) -> ReviewerPackageMetadata:
        row = connection.execute(
            "SELECT * FROM reviewer_packages WHERE package_id = ?", (str(package_id),)
        ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "Reviewer package was not found")
        inputs = connection.execute(
            "SELECT * FROM reviewer_package_inputs WHERE package_id = ? ORDER BY ordinal",
            (str(package_id),),
        ).fetchall()
        parts = connection.execute(
            "SELECT * FROM reviewer_package_parts WHERE package_id = ? ORDER BY part_index",
            (str(package_id),),
        ).fetchall()
        current = connection.execute(
            "SELECT current_revision FROM research_results WHERE result_id = ?",
            (row["s1_result_id"],),
        ).fetchone()
        stale = not current or current["current_revision"] != row["s1_result_version"]
        if not stale:
            s1_package = self._get_s1_package(connection, row["session_id"])
            stale = str(s1_package.package_id) != row["s1_package_id"] or s1_package.stale
        return ReviewerPackageMetadata(
            package_id=row["package_id"], session_id=row["session_id"],
            task_id=row["task_id"], provider_id=row["provider_id"],
            template_version=row["template_version"], s1_result_id=row["s1_result_id"],
            s1_result_version=row["s1_result_version"], s1_package_id=row["s1_package_id"],
            s1_package_fingerprint=row["s1_package_fingerprint"],
            input_fingerprint=row["input_fingerprint"], package_sha256=row["package_sha256"],
            total_bytes=row["total_bytes"], part_count=row["part_count"],
            r0_complete=bool(row["r0_complete"]), stale=stale,
            inputs=[S1InputReference(
                ordinal=item["ordinal"], provider_id=item["provider_id"],
                reported_model=item["reported_model"], task_id=item["task_id"],
                status=item["status"], result_id=item["result_id"],
                result_version=item["result_version"], sha256=item["sha256"],
                gap_reason=item["gap_reason"],
            ) for item in inputs],
            parts=[S1PackagePartMetadata(
                part_index=item["part_index"], prompt_id=item["prompt_id"],
                size_bytes=item["size_bytes"], sha256=item["sha256"],
            ) for item in parts],
            created_at=row["created_at"],
        )

    def prepare_s2(self, session_id: UUID | str, *, expected_revision: int, idempotency_key: str) -> S2PrepareReceipt:
        payload = {"session_id": str(session_id), "expected_revision": expected_revision}
        with self._transaction() as connection:
            repeated = self._idempotent_result(connection, "s2.package.prepare", idempotency_key, payload)
            if repeated:
                return S2PrepareReceipt(package=self._get_s2_package(connection, session_id), session=repeated)
            session = self._locked_session(connection, session_id, expected_revision)
            if Phase(session["phase"]) != Phase.S2 or SessionStatus(session["status"]) != SessionStatus.ACTIVE:
                raise ResearchDomainError("INVALID_PHASE", "S2 package requires the active S2 phase")
            package_row = connection.execute("SELECT package_id FROM s2_packages WHERE session_id = ? ORDER BY created_at DESC LIMIT 1", (str(session_id),)).fetchone()
            current_package = self._get_s2_package(connection, session_id) if package_row else None
            if not current_package or current_package.stale:
                if current_package:
                    connection.execute("UPDATE research_tasks SET status = ?, revision = revision + 1, updated_at = ? WHERE task_id = ?",
                                       (TaskStatus.STALE.value, _timestamp(), str(current_package.task_id)))
                ordinal = connection.execute("SELECT COALESCE(MAX(ordinal), 0) + 1 FROM research_rounds WHERE session_id = ?", (str(session_id),)).fetchone()[0]
                round_id = str(uuid4())
                connection.execute("INSERT INTO research_rounds VALUES (?, ?, ?, ?, ?, ?)",
                                   (round_id, str(session_id), Phase.S2.value, ordinal, "ACTIVE", _timestamp()))
                self._create_s2_task_and_package(connection, session, round_id, _timestamp())
            current = self._session(session)
            self._store_idempotency(connection, "s2.package.prepare", idempotency_key, payload, current)
            return S2PrepareReceipt(package=self._get_s2_package(connection, session_id), session=current)

    def get_s2_package(self, session_id: UUID | str) -> S2PackageMetadata:
        with closing(self._connection()) as connection:
            return self._get_s2_package(connection, session_id)

    def get_s2_part_chunk(self, session_id: UUID | str, package_id: UUID | str, *, part_index: int, offset: int, limit: int) -> tuple[bytes, int]:
        if part_index < 0 or offset < 0 or not 1 <= limit <= MAX_CHUNK_BYTES:
            raise ResearchDomainError("BAD_REQUEST", "Invalid S2 package part range")
        with closing(self._connection()) as connection:
            row = connection.execute(
                """SELECT substr(CAST(p.prompt_text AS BLOB), ? + 1, ?) content,
                          length(CAST(p.prompt_text AS BLOB)) total_bytes
                   FROM s2_package_parts sp JOIN s2_packages s ON s.package_id = sp.package_id
                   JOIN prompt_artifacts p ON p.prompt_id = sp.prompt_id
                   WHERE sp.package_id = ? AND sp.part_index = ? AND s.session_id = ?""",
                (offset, limit, str(package_id), part_index, str(session_id)),
            ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "S2 package part was not found")
        if offset > row["total_bytes"]:
            raise ResearchDomainError("BAD_REQUEST", "S2 package part offset exceeds content size")
        return bytes(row["content"]), row["total_bytes"]

    def get_s2_part_text(self, session_id: UUID | str, package_id: UUID | str, part_index: int) -> str:
        with closing(self._connection()) as connection:
            row = connection.execute(
                """SELECT p.prompt_text FROM s2_package_parts sp
                   JOIN s2_packages s ON s.package_id = sp.package_id
                   JOIN prompt_artifacts p ON p.prompt_id = sp.prompt_id
                   WHERE sp.package_id = ? AND sp.part_index = ? AND s.session_id = ?""",
                (str(package_id), part_index, str(session_id)),
            ).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "S2 package part was not found")
        return str(row["prompt_text"])

    def _current_review_inputs(self, connection: sqlite3.Connection, session: sqlite3.Row, s1: ReviewerS1Reference) -> list[S2ReviewInput]:
        rows = connection.execute(
            """SELECT t.ordinal, t.provider_id, t.task_id, t.status, r.result_id,
                      r.current_revision result_version, v.sha256, v.response_text,
                      l.package_id reviewer_package_id
               FROM research_tasks t LEFT JOIN research_results r ON r.task_id = t.task_id
               LEFT JOIN response_revisions v ON v.result_id = r.result_id AND v.version = r.current_revision
               LEFT JOIN reviewer_result_links l ON l.result_id = r.result_id
               WHERE t.session_id = ? AND t.phase = ? AND t.kind = ? AND t.status != ?
               ORDER BY t.ordinal""",
            (session["session_id"], Phase.S1_REVIEW.value, TaskKind.S1_REVIEW.value, TaskStatus.STALE.value),
        ).fetchall()
        if [row["provider_id"] for row in rows] != list(S2_REVIEW_PROVIDERS) or any(
            TaskStatus(row["status"]) != TaskStatus.RECEIVED or not row["result_id"] or not row["response_text"] or not row["reviewer_package_id"] for row in rows
        ):
            raise ResearchDomainError("INVALID_PHASE", "S2 requires three current received reviews")
        for row in rows:
            package = self._get_reviewer_package(connection, row["reviewer_package_id"])
            if package.stale or str(package.s1_result_id) != s1.result_id or package.s1_result_version != s1.result_version:
                raise ResearchDomainError("STALE_REVISION", "A reviewer result does not match the current S1", retryable=True)
        return [S2ReviewInput(row["ordinal"], row["provider_id"], row["task_id"], row["result_id"],
                              row["result_version"], row["sha256"], row["reviewer_package_id"], row["response_text"])
                for row in rows]

    def _create_s2_task_and_package(self, connection: sqlite3.Connection, session: sqlite3.Row, round_id: str, now: str) -> None:
        s1 = self._current_s1_reference(connection, session)
        reviews = self._current_review_inputs(connection, session, s1)
        inputs = self._reviewer_inputs(connection, s1.package_id)
        package = build_s2_package(session["question"], s1, reviews, inputs)
        task_id, package_id = str(uuid4()), str(uuid4())
        connection.execute("""INSERT INTO research_tasks VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, 0, ?, ?)""",
            (task_id, session["session_id"], round_id, Phase.S2.value, TaskKind.S2_SYNTHESIS.value,
             "chatgpt", "s2_finalizer", TaskStatus.PROMPT_READY.value, now, now))
        connection.execute("INSERT INTO s2_packages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (package_id, session["session_id"], task_id, S2_TEMPLATE_VERSION, s1.result_id, s1.result_version,
             s1.package_id, package.input_fingerprint, package.package_sha256, package.total_bytes,
             len(package.parts), int(package.r0_complete), now))
        for item in reviews:
            connection.execute("INSERT INTO s2_package_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (package_id, item.ordinal, item.provider_id, item.task_id, item.result_id,
                 item.result_version, item.sha256, item.reviewer_package_id))
        for item in inputs:
            connection.execute("INSERT INTO s2_package_inputs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (package_id, item.ordinal, item.provider_id, item.reported_model, item.task_id, item.status,
                 item.result_id, item.result_version, item.sha256, item.gap_reason))
        for index, content in enumerate(package.parts):
            artifact = PromptComposer.verbatim(UUID(task_id), content, version=index + 1)
            connection.execute("INSERT INTO prompt_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(artifact.prompt_id), task_id, artifact.version, artifact.content,
                 artifact.size_bytes, artifact.sha256, artifact.created_at.isoformat()))
            connection.execute("INSERT INTO s2_package_parts VALUES (?, ?, ?, ?, ?)",
                (package_id, index, str(artifact.prompt_id), artifact.size_bytes, artifact.sha256))

    def _get_s2_package(self, connection: sqlite3.Connection, session_id: UUID | str) -> S2PackageMetadata:
        row = connection.execute("SELECT * FROM s2_packages WHERE session_id = ? ORDER BY created_at DESC LIMIT 1", (str(session_id),)).fetchone()
        if not row:
            raise ResearchDomainError("NOT_FOUND", "S2 package has not been prepared")
        review_rows = connection.execute("SELECT * FROM s2_package_reviews WHERE package_id = ? ORDER BY ordinal", (row["package_id"],)).fetchall()
        input_rows = connection.execute("SELECT * FROM s2_package_inputs WHERE package_id = ? ORDER BY ordinal", (row["package_id"],)).fetchall()
        part_rows = connection.execute("SELECT * FROM s2_package_parts WHERE package_id = ? ORDER BY part_index", (row["package_id"],)).fetchall()
        stale = False
        current_s1 = connection.execute("SELECT current_revision FROM research_results WHERE result_id = ?", (row["s1_result_id"],)).fetchone()
        stale = not current_s1 or current_s1[0] != row["s1_result_version"]
        for review in review_rows:
            current = connection.execute("SELECT current_revision FROM research_results WHERE result_id = ?", (review["result_id"],)).fetchone()
            stale = stale or not current or current[0] != review["result_version"]
        if not stale:
            session = connection.execute(
                "SELECT * FROM research_sessions WHERE session_id = ?", (str(session_id),)
            ).fetchone()
            try:
                s1 = self._current_s1_reference(connection, session)
                current_reviews = self._current_review_inputs(connection, session, s1)
                fresh = build_s2_package(
                    session["question"], s1, current_reviews,
                    self._reviewer_inputs(connection, s1.package_id),
                )
                stale = (fresh.input_fingerprint != row["input_fingerprint"]
                         or fresh.package_sha256 != row["package_sha256"]
                         or len(fresh.parts) != row["part_count"])
            except ResearchDomainError:
                stale = True
        return S2PackageMetadata(
            package_id=row["package_id"], session_id=row["session_id"], task_id=row["task_id"],
            template_version=row["template_version"], s1_result_id=row["s1_result_id"],
            s1_result_version=row["s1_result_version"], s1_package_id=row["s1_package_id"],
            input_fingerprint=row["input_fingerprint"], package_sha256=row["package_sha256"],
            total_bytes=row["total_bytes"], part_count=row["part_count"], r0_complete=bool(row["r0_complete"]),
            stale=stale, context_warning=row["total_bytes"] >= S2_CONTEXT_WARNING_BYTES,
            reviews=[S2ReviewReference(**{key: item[key] for key in (
                "ordinal", "provider_id", "task_id", "result_id", "result_version",
                "sha256", "reviewer_package_id",
            )}) for item in review_rows],
            inputs=[S1InputReference(**{key: item[key] for key in ("ordinal","provider_id","reported_model","task_id","status","result_id","result_version","sha256","gap_reason")}) for item in input_rows],
            parts=[S1PackagePartMetadata(part_index=item["part_index"], prompt_id=item["prompt_id"], size_bytes=item["size_bytes"], sha256=item["sha256"]) for item in part_rows],
            created_at=row["created_at"],
        )

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
            if require_active_independent and SessionStatus(session_row["status"]) != SessionStatus.ACTIVE:
                raise ResearchDomainError("INVALID_PHASE", "Task operation requires an active session")
            task_row = connection.execute(
                "SELECT * FROM research_tasks WHERE task_id = ? AND session_id = ?",
                (str(task_id), str(session_id)),
            ).fetchone()
            if not task_row:
                raise ResearchDomainError("NOT_FOUND", "Research task was not found")
            if require_active_independent and Phase(task_row["phase"]) != Phase(session_row["phase"]):
                raise ResearchDomainError("INVALID_PHASE", "Task is not part of the active phase")
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
            task = self._active_response_task(connection, session, task_id)
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
            task = self._active_response_task(connection, session, upload["task_id"])
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
            if TaskKind(task["kind"]) == TaskKind.S1_SYNTHESIS:
                package = connection.execute(
                    "SELECT package_id FROM s1_packages WHERE task_id = ?", (upload["task_id"],)
                ).fetchone()
                if not package:
                    raise ResearchDomainError("INVALID_PHASE", "S1 task has no prepared package")
                connection.execute(
                    "INSERT OR IGNORE INTO s1_result_links VALUES (?, ?)",
                    (result_id, package["package_id"]),
                )
                if upload["mode"] == "revise" and Phase(session["phase"]) in {Phase.S1_REVIEW, Phase.S2}:
                    connection.execute(
                        """UPDATE research_tasks
                           SET status = ?, revision = revision + 1, updated_at = ?
                           WHERE session_id = ? AND phase = ? AND kind = ? AND status != ?""",
                        (TaskStatus.STALE.value, now, session["session_id"], Phase.S1_REVIEW.value,
                         TaskKind.S1_REVIEW.value, TaskStatus.STALE.value),
                    )
                    connection.execute(
                        "UPDATE research_tasks SET status = ?, revision = revision + 1, updated_at = ? WHERE session_id = ? AND kind = ? AND status != ?",
                        (TaskStatus.STALE.value, now, session["session_id"], TaskKind.S2_SYNTHESIS.value, TaskStatus.STALE.value),
                    )
            elif TaskKind(task["kind"]) == TaskKind.S1_REVIEW:
                package = connection.execute(
                    "SELECT package_id FROM reviewer_packages WHERE task_id = ?",
                    (upload["task_id"],),
                ).fetchone()
                if not package:
                    raise ResearchDomainError("INVALID_PHASE", "Reviewer task has no prepared package")
                connection.execute(
                    "INSERT OR IGNORE INTO reviewer_result_links VALUES (?, ?)",
                    (result_id, package["package_id"]),
                )
                if upload["mode"] == "revise" and Phase(session["phase"]) == Phase.S2:
                    connection.execute(
                        "UPDATE research_tasks SET status = ?, revision = revision + 1, updated_at = ? WHERE session_id = ? AND kind = ? AND status != ?",
                        (TaskStatus.STALE.value, now, session["session_id"], TaskKind.S2_SYNTHESIS.value, TaskStatus.STALE.value),
                    )
            elif TaskKind(task["kind"]) == TaskKind.S2_SYNTHESIS:
                package = connection.execute("SELECT package_id FROM s2_packages WHERE task_id = ?", (upload["task_id"],)).fetchone()
                if not package or self._get_s2_package(connection, session["session_id"]).stale:
                    raise ResearchDomainError("STALE_REVISION", "S2 package inputs changed", retryable=True)
                connection.execute("INSERT OR IGNORE INTO s2_result_links VALUES (?, ?)", (result_id, package["package_id"]))
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
            completing_s2 = TaskKind(task["kind"]) == TaskKind.S2_SYNTHESIS and upload["mode"] == "create"
            updated = self._update_session(
                connection, session, phase=Phase.DONE if completing_s2 else Phase(session["phase"]),
                status=(SessionStatus.COMPLETED_WITH_GAPS if session["gap_reason"] else SessionStatus.COMPLETED)
                    if completing_s2 else SessionStatus(session["status"]),
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
                (str(session_id), Phase.R0.value, provider_id, str(task_id)),
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
        if Phase(session["phase"]) != Phase.R0 or SessionStatus(session["status"]) != SessionStatus.ACTIVE:
            raise ResearchDomainError("INVALID_PHASE", "Operation requires an active independent phase")
        task = connection.execute(
            "SELECT * FROM research_tasks WHERE task_id = ? AND session_id = ? AND phase = ?",
            (str(task_id), session["session_id"], Phase.R0.value),
        ).fetchone()
        if not task:
            raise ResearchDomainError("NOT_FOUND", "Independent research task was not found")
        return task

    def _active_response_task(
        self,
        connection: sqlite3.Connection, session: sqlite3.Row, task_id: UUID | str
    ) -> sqlite3.Row:
        phase = Phase(session["phase"])
        task = connection.execute(
            "SELECT * FROM research_tasks WHERE task_id = ? AND session_id = ?",
            (str(task_id), session["session_id"]),
        ).fetchone()
        if not task:
            raise ResearchDomainError("NOT_FOUND", "Active research task was not found")
        kind = TaskKind(task["kind"])
        completed_s2_revision = phase == Phase.DONE and kind == TaskKind.S2_SYNTHESIS
        if SessionStatus(session["status"]) != SessionStatus.ACTIVE and not completed_s2_revision:
            raise ResearchDomainError("INVALID_PHASE", "Response import requires an active session")
        prior_s1_revision = (
            phase in {Phase.S1_REVIEW, Phase.S2} and kind == TaskKind.S1_SYNTHESIS
            and Phase(task["phase"]) == Phase.S1
        )
        prior_review_revision = phase == Phase.S2 and kind == TaskKind.S1_REVIEW and Phase(task["phase"]) == Phase.S1_REVIEW
        if Phase(task["phase"]) != phase and not prior_s1_revision and not prior_review_revision and not completed_s2_revision:
            raise ResearchDomainError("INVALID_PHASE", "Task is not part of the active phase")
        allowed = {
            Phase.R0: {TaskKind.R0_RESPONSE, TaskKind.SEARCH_EVIDENCE},
            Phase.S1: {TaskKind.S1_SYNTHESIS},
            Phase.S1_REVIEW: {TaskKind.S1_SYNTHESIS, TaskKind.S1_REVIEW},
            Phase.S2: {TaskKind.S1_SYNTHESIS, TaskKind.S1_REVIEW, TaskKind.S2_SYNTHESIS},
            Phase.DONE: {TaskKind.S2_SYNTHESIS},
        }
        if kind not in allowed.get(phase, set()):
            raise ResearchDomainError("INVALID_PHASE", "Task cannot accept a manual response")
        if kind == TaskKind.S1_REVIEW:
            package = connection.execute(
                "SELECT package_id FROM reviewer_packages WHERE task_id = ?", (str(task_id),)
            ).fetchone()
            if not package or self._get_reviewer_package(connection, package["package_id"]).stale:
                raise ResearchDomainError(
                    "STALE_REVISION", "Reviewer package is stale; prepare packages for the current S1",
                    retryable=True,
                )
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

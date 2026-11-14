from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from .models import Assignment, ResearchSession, ResearchTask, SessionStatus, SessionSummary, TaskStatus
from .repository import ResearchRepository
from .workflow import ResearchDomainError


class ResearchSessionService:
    def __init__(self, repository: ResearchRepository | str | Path):
        self.repository = (
            repository if isinstance(repository, ResearchRepository) else ResearchRepository(repository)
        )

    def create(
        self, *, question: str, idempotency_key: str, title: str | None = None
    ) -> ResearchSession:
        if not question.strip():
            raise ResearchDomainError("BAD_REQUEST", "Question must not be empty")
        if not idempotency_key.strip():
            raise ResearchDomainError("BAD_REQUEST", "An idempotency key is required")
        return self.repository.create_session(
            question=question, title=title, idempotency_key=idempotency_key
        )

    def get(self, session_id: UUID | str) -> ResearchSession:
        return self.repository.get_session(session_id)

    def list(self, *, limit: int = 50, offset: int = 0) -> list[SessionSummary]:
        if not 1 <= limit <= 50 or offset < 0:
            raise ResearchDomainError("BAD_REQUEST", "Invalid pagination")
        return self.repository.list_sessions(limit=limit, offset=offset)

    def start(
        self,
        session_id: UUID | str,
        *,
        expected_revision: int,
        idempotency_key: str,
        assignments: Sequence[Assignment],
    ) -> ResearchSession:
        if len(assignments) != 4:
            raise ResearchDomainError("BAD_REQUEST", "Exactly four independent assignments are required")
        providers = [item.provider_id for item in assignments]
        roles = [item.role for item in assignments]
        if len(set(providers)) != 4 or len(set(roles)) != 4:
            raise ResearchDomainError("BAD_REQUEST", "Independent providers and roles must be unique")
        return self.repository.start(
            session_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            assignments=assignments,
        )

    def pause(
        self, session_id: UUID | str, *, expected_revision: int, idempotency_key: str
    ) -> ResearchSession:
        return self.repository.change_status(
            session_id, operation="session.paused", target=SessionStatus.PAUSED,
            expected_revision=expected_revision, idempotency_key=idempotency_key,
        )

    def resume(
        self, session_id: UUID | str, *, expected_revision: int, idempotency_key: str
    ) -> ResearchSession:
        return self.repository.change_status(
            session_id, operation="session.resumed", target=SessionStatus.ACTIVE,
            expected_revision=expected_revision, idempotency_key=idempotency_key,
        )

    def advance(
        self,
        session_id: UUID | str,
        *,
        expected_revision: int,
        idempotency_key: str,
        gap_reason: str | None = None,
    ) -> ResearchSession:
        return self.repository.advance(
            session_id, expected_revision=expected_revision,
            idempotency_key=idempotency_key, gap_reason=gap_reason,
        )

    def list_tasks(self, session_id: UUID | str) -> list[ResearchTask]:
        return self.repository.list_tasks(session_id)

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
        """Internal/future-UI operation; deliberately not exposed by IPC v1."""
        return self.repository.update_task_status(
            session_id, task_id, status=status, expected_revision=expected_revision,
            idempotency_key=idempotency_key, gap_reason=gap_reason,
        )

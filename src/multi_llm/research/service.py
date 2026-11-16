from __future__ import annotations

from collections.abc import Sequence
import re
from pathlib import Path
from uuid import UUID

from .clipboard import copy_text
from .models import (
    Assignment, PromptMetadata, ResearchSession, ResearchTask, ResponseMetadata,
    ResponseReceipt, SessionStatus, SessionSummary, TaskStatus, UploadPartReceipt,
    UploadReservation, S1PackageMetadata, S1PrepareReceipt,
    ReviewerPackageMetadata, ReviewerPrepareReceipt,
    S2PackageMetadata, S2PrepareReceipt,
)
from .repository import MAX_CHUNK_BYTES, ResearchRepository
from .provider_catalog import R0_ASSIGNMENTS
from .workflow import ResearchDomainError


class ResearchSessionService:
    def __init__(self, repository: ResearchRepository | str | Path, *, clipboard_writer=copy_text):
        self.repository = (
            repository if isinstance(repository, ResearchRepository) else ResearchRepository(repository)
        )
        self.clipboard_writer = clipboard_writer

    def create(
        self, *, question: str, idempotency_key: str, title: str | None = None,
        perplexity_prompt: str | None = None,
    ) -> ResearchSession:
        if not question.strip():
            raise ResearchDomainError("BAD_REQUEST", "Research prompt must not be empty")
        if len(question.encode("utf-8")) > 2 * 1024 * 1024:
            raise ResearchDomainError("TOO_LARGE", "Research prompt exceeds the size limit")
        perplexity_prompt = perplexity_prompt if perplexity_prompt and perplexity_prompt.strip() else None
        if perplexity_prompt and len(perplexity_prompt.encode("utf-8")) > 2 * 1024 * 1024:
            raise ResearchDomainError("TOO_LARGE", "Perplexity prompt exceeds the size limit")
        if not idempotency_key.strip():
            raise ResearchDomainError("BAD_REQUEST", "An idempotency key is required")
        return self.repository.create_session(
            question=question, title=title, perplexity_prompt=perplexity_prompt,
            idempotency_key=idempotency_key,
        )

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
        if not question.strip():
            raise ResearchDomainError("BAD_REQUEST", "Research prompt must not be empty")
        if len(question.encode("utf-8")) > 2 * 1024 * 1024:
            raise ResearchDomainError("TOO_LARGE", "Research prompt exceeds the size limit")
        perplexity_prompt = perplexity_prompt if perplexity_prompt and perplexity_prompt.strip() else None
        if perplexity_prompt and len(perplexity_prompt.encode("utf-8")) > 2 * 1024 * 1024:
            raise ResearchDomainError("TOO_LARGE", "Perplexity prompt exceeds the size limit")
        return self.repository.update_intake(
            session_id, question=question, title=title,
            perplexity_prompt=perplexity_prompt, expected_revision=expected_revision,
            idempotency_key=idempotency_key,
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
        assignments: Sequence[Assignment] | None = None,
    ) -> ResearchSession:
        fixed = list(R0_ASSIGNMENTS)
        if assignments is not None and list(assignments) != fixed:
            raise ResearchDomainError("BAD_REQUEST", "The R0 provider set is fixed by the server")
        return self.repository.start(
            session_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            assignments=fixed,
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

    def prepare_s1(
        self,
        session_id: UUID | str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> S1PrepareReceipt:
        return self.repository.prepare_s1(
            session_id, expected_revision=expected_revision, idempotency_key=idempotency_key
        )

    def s1_package(self, session_id: UUID | str) -> S1PackageMetadata:
        return self.repository.get_s1_package(session_id)

    def s1_part_chunk(
        self,
        session_id: UUID | str,
        package_id: UUID | str,
        *,
        part_index: int,
        offset: int,
        limit: int,
    ) -> tuple[bytes, int]:
        return self.repository.get_s1_part_chunk(
            session_id, package_id, part_index=part_index, offset=offset, limit=limit
        )

    def copy_s1_part(
        self, session_id: UUID | str, package_id: UUID | str, part_index: int
    ) -> S1PackageMetadata:
        package = self.repository.get_s1_package(session_id)
        if str(package.package_id) != str(package_id):
            raise ResearchDomainError("NOT_FOUND", "S1 package was not found")
        if package.stale:
            raise ResearchDomainError(
                "STALE_REVISION", "S1 package inputs changed; prepare a current package",
                retryable=True,
            )
        text = self.repository.get_s1_part_text(session_id, package_id, part_index)
        self.clipboard_writer(text)
        return package

    def prepare_reviewers(
        self,
        session_id: UUID | str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> ReviewerPrepareReceipt:
        return self.repository.prepare_reviewers(
            session_id, expected_revision=expected_revision, idempotency_key=idempotency_key
        )

    def reviewer_packages(self, session_id: UUID | str) -> list[ReviewerPackageMetadata]:
        return self.repository.get_reviewer_packages(session_id)

    def reviewer_part_chunk(
        self,
        session_id: UUID | str,
        package_id: UUID | str,
        *,
        part_index: int,
        offset: int,
        limit: int,
    ) -> tuple[bytes, int]:
        return self.repository.get_reviewer_part_chunk(
            session_id, package_id, part_index=part_index, offset=offset, limit=limit
        )

    def copy_reviewer_part(
        self, session_id: UUID | str, package_id: UUID | str, part_index: int
    ) -> ReviewerPackageMetadata:
        packages = self.repository.get_reviewer_packages(session_id)
        package = next((item for item in packages if str(item.package_id) == str(package_id)), None)
        if not package:
            raise ResearchDomainError("NOT_FOUND", "Reviewer package was not found")
        if package.stale:
            raise ResearchDomainError(
                "STALE_REVISION", "Reviewer package is stale; prepare packages for the current S1",
                retryable=True,
            )
        text = self.repository.get_reviewer_part_text(session_id, package_id, part_index)
        self.clipboard_writer(text)
        return package

    def prepare_s2(self, session_id: UUID | str, *, expected_revision: int, idempotency_key: str) -> S2PrepareReceipt:
        return self.repository.prepare_s2(session_id, expected_revision=expected_revision, idempotency_key=idempotency_key)

    def s2_package(self, session_id: UUID | str) -> S2PackageMetadata:
        return self.repository.get_s2_package(session_id)

    def s2_part_chunk(self, session_id: UUID | str, package_id: UUID | str, *, part_index: int, offset: int, limit: int) -> tuple[bytes, int]:
        return self.repository.get_s2_part_chunk(session_id, package_id, part_index=part_index, offset=offset, limit=limit)

    def copy_s2_part(self, session_id: UUID | str, package_id: UUID | str, part_index: int) -> S2PackageMetadata:
        package = self.repository.get_s2_package(session_id)
        if str(package.package_id) != str(package_id):
            raise ResearchDomainError("NOT_FOUND", "S2 package was not found")
        if package.stale:
            raise ResearchDomainError("STALE_REVISION", "S2 package inputs changed; prepare a current package", retryable=True)
        self.clipboard_writer(self.repository.get_s2_part_text(session_id, package_id, part_index))
        return package

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
        require_active_independent: bool = False,
    ) -> ResearchTask:
        """Internal/future-UI operation; deliberately not exposed by IPC v1."""
        return self.repository.update_task_status(
            session_id, task_id, status=status, expected_revision=expected_revision,
            idempotency_key=idempotency_key, gap_reason=gap_reason,
            require_active_independent=require_active_independent,
        )

    def prompt_metadata(self, session_id: UUID | str, task_id: UUID | str) -> PromptMetadata:
        return self.repository.get_prompt_metadata(session_id, task_id)

    def prompt_chunk(
        self, session_id: UUID | str, task_id: UUID | str, *, offset: int, limit: int
    ) -> tuple[bytes, int]:
        return self.repository.get_prompt_chunk(session_id, task_id, offset=offset, limit=limit)

    def copy_prompt(self, session_id: UUID | str, task_id: UUID | str) -> PromptMetadata:
        text = self.repository.get_prompt_text(session_id, task_id)
        self.clipboard_writer(text)
        return self.repository.get_prompt_metadata(session_id, task_id)

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
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ResearchDomainError("BAD_REQUEST", "Invalid response hash")
        return self.repository.begin_upload(
            session_id, task_id, mode=mode, size_bytes=size_bytes, sha256=sha256,
            expected_revision=expected_revision, expected_result_version=expected_result_version,
            idempotency_key=idempotency_key,
        )

    def put_upload_part(
        self, upload_id: UUID | str, *, part_index: int, chunk: bytes, sha256: str
    ) -> UploadPartReceipt:
        if len(chunk) > MAX_CHUNK_BYTES:
            raise ResearchDomainError("TOO_LARGE", "Response chunk exceeds the limit")
        return self.repository.put_upload_part(upload_id, part_index=part_index, chunk=chunk, sha256=sha256)

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
        if source_type != "web_manual":
            raise ResearchDomainError("BAD_REQUEST", "Unsupported response source")
        reported_model = reported_model.strip() if reported_model and reported_model.strip() else None
        return self.repository.commit_upload(
            upload_id, session_id=session_id, task_id=task_id, expected_revision=expected_revision,
            idempotency_key=idempotency_key, source_type=source_type,
            reported_model=reported_model,
        )

    def abort_upload(self, upload_id: UUID | str) -> bool:
        return self.repository.abort_upload(upload_id)

    def list_responses(
        self, session_id: UUID | str, task_id: UUID | str | None = None
    ) -> list[ResponseMetadata]:
        return self.repository.list_responses(session_id, task_id)

    def response_chunk(
        self,
        session_id: UUID | str,
        result_id: UUID | str,
        *,
        version: int,
        offset: int,
        limit: int,
    ) -> tuple[bytes, int]:
        return self.repository.get_response_chunk(
            session_id, result_id, version=version, offset=offset, limit=limit
        )

    def mark_awaiting(
        self, session_id: UUID | str, task_id: UUID | str, *, expected_revision: int, idempotency_key: str
    ) -> ResearchTask:
        return self.update_task_status(
            session_id, task_id, status=TaskStatus.AWAITING_USER,
            expected_revision=expected_revision, idempotency_key=idempotency_key,
            require_active_independent=True,
        )

    def mark_unavailable(
        self,
        session_id: UUID | str,
        task_id: UUID | str,
        *,
        reason: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> ResearchTask:
        return self.update_task_status(
            session_id, task_id, status=TaskStatus.UNAVAILABLE, gap_reason=reason,
            expected_revision=expected_revision, idempotency_key=idempotency_key,
            require_active_independent=True,
        )

    def skip_task(
        self,
        session_id: UUID | str,
        task_id: UUID | str,
        *,
        reason: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> ResearchTask:
        return self.update_task_status(
            session_id, task_id, status=TaskStatus.SKIPPED_WITH_REASON, gap_reason=reason,
            expected_revision=expected_revision, idempotency_key=idempotency_key,
            require_active_independent=True,
        )

    def reassign_task(
        self,
        session_id: UUID | str,
        task_id: UUID | str,
        *,
        provider_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> ResearchTask:
        return self.repository.reassign_task(
            session_id, task_id, provider_id=provider_id, expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

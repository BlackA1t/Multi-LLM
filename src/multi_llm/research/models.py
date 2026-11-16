from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Phase(StrEnum):
    DRAFT = "DRAFT"
    INDEPENDENT = "INDEPENDENT"
    COMPARISON = "COMPARISON"
    CRITIQUE = "CRITIQUE"
    FACT_CHECK = "FACT_CHECK"
    ARBITRATION = "ARBITRATION"
    REVIEW = "REVIEW"
    DONE = "DONE"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_GAPS = "COMPLETED_WITH_GAPS"
    CANCELLED = "CANCELLED"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    PROMPT_READY = "PROMPT_READY"
    AWAITING_USER = "AWAITING_USER"
    RECEIVED = "RECEIVED"
    UNAVAILABLE = "UNAVAILABLE"
    SKIPPED_WITH_REASON = "SKIPPED_WITH_REASON"
    STALE = "STALE"
    FAILED = "FAILED"


class TaskKind(StrEnum):
    INDEPENDENT_RESPONSE = "INDEPENDENT_RESPONSE"
    SEARCH_EVIDENCE = "SEARCH_EVIDENCE"
    S1_SYNTHESIS = "S1_SYNTHESIS"
    STRUCTURAL_COMPARISON = "STRUCTURAL_COMPARISON"
    CRITIQUE = "CRITIQUE"
    FACT_CHECK = "FACT_CHECK"
    ARBITRATION = "ARBITRATION"
    REVIEW = "REVIEW"


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: NonEmptyText
    role: NonEmptyText


class ResearchSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    question: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    title: str | None = Field(default=None, max_length=200)
    perplexity_prompt: str | None = Field(default=None, max_length=2 * 1024 * 1024)
    intake_version: int = Field(default=1, ge=1)
    phase: Phase = Phase.DRAFT
    status: SessionStatus = SessionStatus.ACTIVE
    revision: int = Field(default=0, ge=0)
    gap_reason: str | None = Field(default=None, max_length=2000)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SessionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    title: str | None
    phase: Phase
    status: SessionStatus
    revision: int
    updated_at: datetime


class ResearchTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    session_id: UUID
    round_id: UUID
    phase: Phase
    kind: TaskKind
    provider_id: str | None = None
    role: NonEmptyText
    ordinal: int = Field(ge=0)
    status: TaskStatus = TaskStatus.PENDING
    gap_reason: str | None = Field(default=None, max_length=2000)
    revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PromptArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: UUID
    task_id: UUID
    version: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime = Field(default_factory=utcnow)


class SessionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    session_id: UUID
    event_type: NonEmptyText
    entity_id: UUID | None = None
    revision: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utcnow)


class PromptMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: UUID
    task_id: UUID
    version: int = Field(ge=1)
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class UploadReservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: UUID
    task_id: UUID
    mode: str
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    next_index: int = Field(ge=0)
    expires_at: datetime


class UploadPartReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: UUID
    part_index: int = Field(ge=0)
    next_index: int = Field(ge=0)


class ResponseMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: UUID
    task_id: UUID
    version: int = Field(ge=1)
    source_type: str
    reported_model: str | None = None
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime


class ResponseReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: ResponseMetadata
    task: ResearchTask
    session_revision: int = Field(ge=0)


class S1InputReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordinal: int = Field(ge=0)
    provider_id: NonEmptyText
    reported_model: str | None = Field(default=None, max_length=200)
    task_id: UUID
    status: TaskStatus
    result_id: UUID | None = None
    result_version: int | None = Field(default=None, ge=1)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    gap_reason: str | None = Field(default=None, max_length=2000)


class S1PackagePartMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_index: int = Field(ge=0)
    prompt_id: UUID
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class S1PackageMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: UUID
    session_id: UUID
    task_id: UUID
    template_version: int = Field(ge=1)
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    total_bytes: int = Field(ge=1)
    part_count: int = Field(ge=1)
    r0_complete: bool
    stale: bool = False
    inputs: list[S1InputReference]
    parts: list[S1PackagePartMetadata]
    created_at: datetime


class S1PrepareReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: S1PackageMetadata
    session: ResearchSession

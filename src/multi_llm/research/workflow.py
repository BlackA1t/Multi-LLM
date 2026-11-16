from __future__ import annotations

from .models import Phase, SessionStatus, TaskStatus


class ResearchDomainError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class StaleRevisionError(ResearchDomainError):
    def __init__(self):
        super().__init__("STALE_REVISION", "Session data changed in another window", retryable=True)


PHASE_ORDER = (
    Phase.DRAFT,
    Phase.R0,
    Phase.S1,
    Phase.S1_REVIEW,
    Phase.S2,
    Phase.DONE,
)

SESSION_TRANSITIONS: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.ACTIVE: {
        SessionStatus.PAUSED,
        SessionStatus.BLOCKED,
        SessionStatus.COMPLETED,
        SessionStatus.COMPLETED_WITH_GAPS,
        SessionStatus.CANCELLED,
    },
    SessionStatus.PAUSED: {SessionStatus.ACTIVE, SessionStatus.CANCELLED},
    SessionStatus.BLOCKED: {SessionStatus.ACTIVE, SessionStatus.PAUSED, SessionStatus.CANCELLED},
    SessionStatus.COMPLETED: set(),
    SessionStatus.COMPLETED_WITH_GAPS: set(),
    SessionStatus.CANCELLED: set(),
}

TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.PROMPT_READY, TaskStatus.FAILED, TaskStatus.UNAVAILABLE},
    TaskStatus.PROMPT_READY: {TaskStatus.AWAITING_USER, TaskStatus.FAILED, TaskStatus.UNAVAILABLE},
    TaskStatus.AWAITING_USER: {TaskStatus.RECEIVED, TaskStatus.FAILED, TaskStatus.UNAVAILABLE},
    TaskStatus.RECEIVED: {TaskStatus.STALE},
    TaskStatus.UNAVAILABLE: {TaskStatus.PENDING, TaskStatus.SKIPPED_WITH_REASON},
    TaskStatus.SKIPPED_WITH_REASON: {TaskStatus.STALE},
    TaskStatus.STALE: {TaskStatus.PROMPT_READY, TaskStatus.AWAITING_USER, TaskStatus.RECEIVED, TaskStatus.FAILED},
    TaskStatus.FAILED: {TaskStatus.PENDING},
}


def next_phase(current: Phase) -> Phase:
    try:
        index = PHASE_ORDER.index(current)
    except ValueError as exc:
        raise ResearchDomainError("INVALID_PHASE", "Unknown research phase") from exc
    if index == len(PHASE_ORDER) - 1:
        raise ResearchDomainError("INVALID_PHASE", "Research session is already complete")
    return PHASE_ORDER[index + 1]


def require_phase_transition(current: Phase, target: Phase) -> None:
    if next_phase(current) != target:
        raise ResearchDomainError("INVALID_PHASE", f"Expected the phase after {current.value}")


def require_session_transition(current: SessionStatus, target: SessionStatus) -> None:
    if target not in SESSION_TRANSITIONS[current]:
        raise ResearchDomainError("INVALID_PHASE", f"Session status cannot change from {current.value} to {target.value}")


def require_task_transition(current: TaskStatus, target: TaskStatus, gap_reason: str | None = None) -> None:
    if target not in TASK_TRANSITIONS[current]:
        raise ResearchDomainError("INVALID_PHASE", f"Task status cannot change from {current.value} to {target.value}")
    if target in {TaskStatus.UNAVAILABLE, TaskStatus.SKIPPED_WITH_REASON} and not (gap_reason and gap_reason.strip()):
        raise ResearchDomainError("BAD_REQUEST", "A gap reason is required for unavailable or skipped work")


class RoundManager:
    """Single supported research workflow: R0 → S1 → S1 review → S2."""

    @staticmethod
    def validate_gate(
        phase: Phase,
        statuses: list[TaskStatus],
        gap_reason: str | None,
        task_gap_reasons: list[str | None] | None = None,
    ) -> bool:
        if phase == Phase.R0:
            received = statuses.count(TaskStatus.RECEIVED)
            if len(statuses) == 8 and received == 8:
                return False
            skipped = statuses.count(TaskStatus.SKIPPED_WITH_REASON)
            stored_reason = bool(
                task_gap_reasons
                and len(task_gap_reasons) == len(statuses)
                and all(
                    reason and reason.strip()
                    for status, reason in zip(statuses, task_gap_reasons, strict=True)
                    if status == TaskStatus.SKIPPED_WITH_REASON
                )
            )
            if len(statuses) == 8 and received == 7 and skipped == 1 and stored_reason and gap_reason and gap_reason.strip():
                return True
            raise ResearchDomainError(
                "INVALID_PHASE",
                "Independent R0 requires eight responses, or seven with an explicit gap reason",
            )
        incomplete = [status for status in statuses if status != TaskStatus.RECEIVED]
        if not incomplete:
            return False
        allowed_gap = all(status == TaskStatus.SKIPPED_WITH_REASON for status in incomplete)
        if allowed_gap and gap_reason and gap_reason.strip():
            return True
        raise ResearchDomainError("INVALID_PHASE", "Current phase has incomplete tasks")

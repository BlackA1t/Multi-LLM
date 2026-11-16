import pytest

from multi_llm.research.models import Phase, SessionStatus, TaskStatus
from multi_llm.research.workflow import (
    PHASE_ORDER,
    ResearchDomainError,
    RoundManager,
    next_phase,
    require_session_transition,
    require_task_transition,
)


def test_all_phase_transitions_are_fixed_and_forward_only():
    assert [next_phase(item) for item in PHASE_ORDER[:-1]] == list(PHASE_ORDER[1:])
    with pytest.raises(ResearchDomainError, match="already complete"):
        next_phase(Phase.DONE)


def test_session_and_task_status_transitions_reject_invalid_moves():
    require_session_transition(SessionStatus.ACTIVE, SessionStatus.PAUSED)
    require_session_transition(SessionStatus.PAUSED, SessionStatus.ACTIVE)
    with pytest.raises(ResearchDomainError):
        require_session_transition(SessionStatus.COMPLETED, SessionStatus.ACTIVE)

    require_task_transition(TaskStatus.PROMPT_READY, TaskStatus.AWAITING_USER)
    require_task_transition(TaskStatus.AWAITING_USER, TaskStatus.RECEIVED)
    with pytest.raises(ResearchDomainError):
        require_task_transition(TaskStatus.RECEIVED, TaskStatus.PENDING)
    with pytest.raises(ResearchDomainError, match="gap reason"):
        require_task_transition(TaskStatus.PROMPT_READY, TaskStatus.UNAVAILABLE)


def test_independent_gate_requires_eight_or_explicit_seven_response_gap():
    assert not RoundManager.validate_gate(Phase.R0, [TaskStatus.RECEIVED] * 8, None)
    assert RoundManager.validate_gate(
        Phase.R0,
        [TaskStatus.RECEIVED] * 7 + [TaskStatus.SKIPPED_WITH_REASON],
        "eighth provider unavailable",
        [None] * 7 + ["provider unavailable"],
    )
    with pytest.raises(ResearchDomainError):
        RoundManager.validate_gate(Phase.R0, [TaskStatus.RECEIVED] * 3, None)
    with pytest.raises(ResearchDomainError):
        RoundManager.validate_gate(Phase.R0, [TaskStatus.RECEIVED] * 2, "two missing")

import pytest

from multi_llm.research import Assignment, Phase, ResearchSessionService, SessionStatus, TaskStatus
from multi_llm.research.workflow import ResearchDomainError


def assignments():
    return [Assignment(provider_id=f"provider-{index}", role=f"role-{index}") for index in range(4)]


def transition_all_tasks(service, session_id, revision, prefix):
    for index, task in enumerate([item for item in service.list_tasks(session_id) if item.phase == service.get(session_id).phase]):
        if task.status == TaskStatus.PROMPT_READY:
            service.update_task_status(
                session_id, task.task_id, status=TaskStatus.AWAITING_USER,
                expected_revision=service.get(session_id).revision, idempotency_key=f"{prefix}-await-{index}",
            )
        service.update_task_status(
            session_id, task.task_id, status=TaskStatus.RECEIVED,
            expected_revision=service.get(session_id).revision, idempotency_key=f"{prefix}-recv-{index}",
        )
    return service.get(session_id).revision


def test_full_fixed_workflow_survives_service_restart(tmp_path):
    path = tmp_path / "research.db"
    service = ResearchSessionService(path)
    session = service.create(question="Исследовать тему 🚀", idempotency_key="create")
    session = service.start(session.session_id, expected_revision=0, idempotency_key="start", assignments=assignments())
    assert session.phase == Phase.INDEPENDENT

    expected_phases = [Phase.COMPARISON, Phase.CRITIQUE, Phase.FACT_CHECK, Phase.ARBITRATION, Phase.REVIEW, Phase.DONE]
    for index, target in enumerate(expected_phases):
        transition_all_tasks(service, session.session_id, session.revision, f"phase-{index}")
        service = ResearchSessionService(path)
        session = service.advance(
            session.session_id, expected_revision=service.get(session.session_id).revision,
            idempotency_key=f"advance-{index}",
        )
        assert session.phase == target
    assert session.status == SessionStatus.COMPLETED


def test_three_response_gap_is_recorded_and_finishes_with_gaps(tmp_path):
    service = ResearchSessionService(tmp_path / "research.db")
    session = service.create(question="Q", idempotency_key="create")
    service.start(session.session_id, expected_revision=0, idempotency_key="start", assignments=assignments())
    tasks = service.list_tasks(session.session_id)
    for index, task in enumerate(tasks[:3]):
        service.update_task_status(
            session.session_id, task.task_id, status=TaskStatus.AWAITING_USER,
            expected_revision=service.get(session.session_id).revision, idempotency_key=f"a-{index}",
        )
        service.update_task_status(
            session.session_id, task.task_id, status=TaskStatus.RECEIVED,
            expected_revision=service.get(session.session_id).revision, idempotency_key=f"r-{index}",
        )
    missing = tasks[3]
    service.update_task_status(
        session.session_id, missing.task_id, status=TaskStatus.UNAVAILABLE,
        expected_revision=service.get(session.session_id).revision, idempotency_key="unavailable",
        gap_reason="provider unavailable",
    )
    service.update_task_status(
        session.session_id, missing.task_id, status=TaskStatus.SKIPPED_WITH_REASON,
        expected_revision=service.get(session.session_id).revision, idempotency_key="skipped",
        gap_reason="provider unavailable",
    )
    with pytest.raises(ResearchDomainError):
        service.advance(
            session.session_id, expected_revision=service.get(session.session_id).revision,
            idempotency_key="no-gap",
        )
    advanced = service.advance(
        session.session_id, expected_revision=service.get(session.session_id).revision,
        idempotency_key="with-gap", gap_reason="provider unavailable",
    )
    assert advanced.phase == Phase.COMPARISON
    assert advanced.gap_reason == "provider unavailable"


def test_pause_resume_and_invalid_assignment_count(tmp_path):
    service = ResearchSessionService(tmp_path / "research.db")
    session = service.create(question="Q", idempotency_key="create")
    with pytest.raises(ResearchDomainError, match="Exactly four"):
        service.start(
            session.session_id, expected_revision=0, idempotency_key="bad", assignments=assignments()[:3]
        )
    paused = service.pause(session.session_id, expected_revision=0, idempotency_key="pause")
    resumed = service.resume(session.session_id, expected_revision=paused.revision, idempotency_key="resume")
    assert resumed.status == SessionStatus.ACTIVE

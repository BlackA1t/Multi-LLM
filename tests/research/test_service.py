import hashlib

import pytest

from multi_llm.research import Phase, ResearchSessionService, SessionStatus, TaskStatus
from multi_llm.research.provider_catalog import R0_ASSIGNMENTS
from multi_llm.research.workflow import ResearchDomainError


def assignments():
    return list(R0_ASSIGNMENTS)


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


def import_answer(service, session_id, task, text, key):
    session = service.get(session_id)
    payload = text.encode()
    digest = hashlib.sha256(payload).hexdigest()
    upload = service.begin_upload(
        session_id, task.task_id, mode="create", size_bytes=len(payload), sha256=digest,
        expected_revision=session.revision, expected_result_version=None, idempotency_key=key,
    )
    service.put_upload_part(upload.upload_id, part_index=0, chunk=payload, sha256=digest)
    return service.commit_upload(
        upload.upload_id, session_id=session_id, task_id=task.task_id,
        expected_revision=session.revision, idempotency_key=key,
        source_type="web_manual", reported_model=task.provider_id,
    )


def test_s1_phase_survives_service_restart_and_requires_received_s1(tmp_path):
    path = tmp_path / "research.db"
    service = ResearchSessionService(path)
    session = service.create(question="Исследовать тему 🚀", idempotency_key="create")
    session = service.start(session.session_id, expected_revision=0, idempotency_key="start", assignments=assignments())
    assert session.phase == Phase.R0

    for index, task in enumerate(service.list_tasks(session.session_id)):
        import_answer(service, session.session_id, task, f"answer-{index}", f"answer-{index}")
    service = ResearchSessionService(path)
    session = service.advance(
        session.session_id, expected_revision=service.get(session.session_id).revision,
        idempotency_key="advance-s1",
    )
    assert session.phase == Phase.S1
    package = ResearchSessionService(path).s1_package(session.session_id)
    assert package.r0_complete is True and package.part_count >= 1
    with pytest.raises(ResearchDomainError, match="incomplete"):
        service.advance(
            session.session_id, expected_revision=session.revision,
            idempotency_key="blocked-old-pipeline",
        )


def test_seven_response_gap_is_recorded_and_finishes_with_gaps(tmp_path):
    service = ResearchSessionService(tmp_path / "research.db")
    session = service.create(question="Q", idempotency_key="create")
    service.start(session.session_id, expected_revision=0, idempotency_key="start", assignments=assignments())
    tasks = service.list_tasks(session.session_id)
    for index, task in enumerate(tasks[:7]):
        import_answer(service, session.session_id, task, f"answer-{index}", f"r-{index}")
    missing = tasks[7]
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
    assert advanced.phase == Phase.S1
    assert advanced.gap_reason == "provider unavailable"


def test_pause_resume_and_server_rejects_non_fixed_assignments(tmp_path):
    service = ResearchSessionService(tmp_path / "research.db")
    session = service.create(question="Q", idempotency_key="create")
    with pytest.raises(ResearchDomainError, match="fixed by the server"):
        service.start(
            session.session_id, expected_revision=0, idempotency_key="bad", assignments=assignments()[:3]
        )
    paused = service.pause(session.session_id, expected_revision=0, idempotency_key="pause")
    resumed = service.resume(session.session_id, expected_revision=paused.revision, idempotency_key="resume")
    assert resumed.status == SessionStatus.ACTIVE

from __future__ import annotations

import hashlib

import pytest

from multi_llm.research import Phase, ResearchSessionService, TaskStatus
from multi_llm.research.models import TaskKind
from multi_llm.research.provider_catalog import R0_ASSIGNMENTS
from multi_llm.research.workflow import ResearchDomainError


def _store_response(service, session_id, task, text: str, key: str):
    session = service.get(session_id)
    payload = text.encode("utf-8")
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


def test_fixed_eight_r0_receive_the_exact_same_prompt(tmp_path):
    service = ResearchSessionService(tmp_path / "research.db")
    prompt = "  Готовый промпт\r\nс Unicode 🚀  "
    created = service.create(question=prompt, title="R0", idempotency_key="create")
    started = service.start(
        created.session_id, expected_revision=created.revision, idempotency_key="start"
    )
    tasks = service.list_tasks(started.session_id)
    assert len(tasks) == 8
    assert [task.provider_id for task in tasks] == [item.provider_id for item in R0_ASSIGNMENTS]
    assert {task.kind for task in tasks} == {TaskKind.INDEPENDENT_RESPONSE}
    artifacts = [service.repository.get_prompt_text(started.session_id, task.task_id) for task in tasks]
    assert artifacts == [prompt] * 8
    assert len({service.prompt_metadata(started.session_id, task.task_id).sha256 for task in tasks}) == 1


def test_optional_perplexity_is_separate_and_does_not_block_r0(tmp_path):
    service = ResearchSessionService(tmp_path / "research.db")
    created = service.create(
        question="main", perplexity_prompt="search only", idempotency_key="create"
    )
    started = service.start(
        created.session_id, expected_revision=0, idempotency_key="start"
    )
    tasks = service.list_tasks(started.session_id)
    mandatory = [task for task in tasks if task.kind == TaskKind.INDEPENDENT_RESPONSE]
    search = [task for task in tasks if task.kind == TaskKind.SEARCH_EVIDENCE]
    assert len(mandatory) == 8
    assert len(search) == 1 and search[0].provider_id == "perplexity"
    assert service.repository.get_prompt_text(started.session_id, search[0].task_id) == "search only"
    for index, task in enumerate(mandatory):
        _store_response(service, started.session_id, task, f"answer-{index}", f"answer-{index}")
    current = service.get(started.session_id)
    advanced = service.advance(
        started.session_id, expected_revision=current.revision, idempotency_key="advance"
    )
    assert advanced.phase == Phase.COMPARISON
    assert service.list_tasks(started.session_id)[-1].status == TaskStatus.PROMPT_READY


def test_draft_intake_can_be_versioned_but_not_changed_after_start(tmp_path):
    path = tmp_path / "research.db"
    service = ResearchSessionService(path)
    created = service.create(question="v1", idempotency_key="create")
    updated = service.update_intake(
        created.session_id, question="v2", title="Edited", perplexity_prompt="search",
        expected_revision=created.revision, idempotency_key="edit",
    )
    assert updated.question == "v2" and updated.intake_version == 2
    restarted = ResearchSessionService(path)
    assert restarted.get(created.session_id).perplexity_prompt == "search"
    started = restarted.start(
        created.session_id, expected_revision=updated.revision, idempotency_key="start"
    )
    with pytest.raises(ResearchDomainError, match="before research starts"):
        restarted.update_intake(
            created.session_id, question="v3", title=None, perplexity_prompt=None,
            expected_revision=started.revision, idempotency_key="late-edit",
        )


def test_missing_r0_cannot_be_reported_as_full_and_equal_answers_remain_distinct(tmp_path):
    service = ResearchSessionService(tmp_path / "research.db")
    created = service.create(question="main", idempotency_key="create")
    started = service.start(created.session_id, expected_revision=0, idempotency_key="start")
    tasks = service.list_tasks(started.session_id)
    shared = "identical synthetic answer"
    first = _store_response(service, started.session_id, tasks[0], shared, "first")
    second = _store_response(service, started.session_id, tasks[1], shared, "second")
    assert first.response.sha256 == second.response.sha256
    assert first.response.result_id != second.response.result_id
    assert first.response.task_id != second.response.task_id
    with pytest.raises(ResearchDomainError, match="requires eight"):
        service.advance(
            started.session_id, expected_revision=service.get(started.session_id).revision,
            idempotency_key="too-early",
        )


@pytest.mark.parametrize("prompt", ["", "   ", "\r\n\t"])
def test_blank_main_prompt_is_rejected(tmp_path, prompt):
    service = ResearchSessionService(tmp_path / "research.db")
    with pytest.raises(ResearchDomainError, match="must not be empty"):
        service.create(question=prompt, idempotency_key="blank")

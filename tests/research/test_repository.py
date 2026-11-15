from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from multi_llm.research import ResearchRepository, ResearchSessionService, TaskStatus
from multi_llm.research.provider_catalog import R0_ASSIGNMENTS
from multi_llm.research.workflow import ResearchDomainError, StaleRevisionError


def assignments():
    return list(R0_ASSIGNMENTS)


def test_database_is_separate_migrated_persistent_and_has_safety_pragmas(tmp_path):
    path = tmp_path / "research.db"
    first = ResearchSessionService(path)
    created = first.create(question="Почему небо синее?", title="Небо", idempotency_key="create-1")

    second = ResearchSessionService(path)
    restored = second.get(created.session_id)
    assert restored.question == "Почему небо синее?"
    assert second.create(question=restored.question, title="Небо", idempotency_key="create-1").session_id == created.session_id

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3
        columns = {row[1] for row in connection.execute("PRAGMA table_info(session_events)")}
        assert "question" not in columns and "message" not in columns and "payload" not in columns


def test_concurrent_repository_initialization_is_migration_safe(tmp_path):
    path = tmp_path / "research.db"
    with ThreadPoolExecutor(max_workers=4) as pool:
        repositories = list(pool.map(lambda _index: ResearchRepository(path), range(4)))
    assert len(repositories) == 4
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3


def test_failed_idempotent_create_rolls_back_without_duplicate(tmp_path):
    path = tmp_path / "research.db"
    service = ResearchSessionService(path)
    service.create(question="first", idempotency_key="same")
    with pytest.raises(ResearchDomainError, match="different arguments"):
        service.create(question="secret second question", idempotency_key="same")
    assert len(service.list()) == 1
    assert "secret second question" not in repr(service.repository.list_events(service.list()[0].session_id))


def test_start_failure_rolls_back_every_partial_row(tmp_path, monkeypatch):
    service = ResearchSessionService(tmp_path / "research.db")
    created = service.create(question="rollback", idempotency_key="create")

    def fail_prompt(*_args, **_kwargs):
        raise RuntimeError("synthetic prompt failure")

    monkeypatch.setattr("multi_llm.research.repository.PromptComposer.verbatim", fail_prompt)
    with pytest.raises(RuntimeError, match="synthetic"):
        service.start(
            created.session_id, expected_revision=0, idempotency_key="start", assignments=assignments()
        )
    restored = service.get(created.session_id)
    assert restored.revision == 0 and restored.phase.value == "DRAFT"
    assert service.list_tasks(created.session_id) == []
    with sqlite3.connect(tmp_path / "research.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM research_rounds").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM session_participants").fetchone()[0] == 0


def test_optimistic_revision_and_idempotency_are_atomic(tmp_path):
    service = ResearchSessionService(tmp_path / "research.db")
    created = service.create(question="concurrent", idempotency_key="create")
    started = service.start(
        created.session_id, expected_revision=0, idempotency_key="start", assignments=assignments()
    )
    repeated = service.start(
        created.session_id, expected_revision=0, idempotency_key="start", assignments=assignments()
    )
    assert repeated.revision == started.revision == 1
    assert len(service.list_tasks(created.session_id)) == 8

    def pause(key):
        try:
            return service.pause(created.session_id, expected_revision=1, idempotency_key=key).status.value
        except StaleRevisionError:
            return "STALE_REVISION"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(pause, ["pause-a", "pause-b"]))
    assert sorted(outcomes) == ["PAUSED", "STALE_REVISION"]


def test_task_update_is_transactional_and_increments_session_revision(tmp_path):
    service = ResearchSessionService(tmp_path / "research.db")
    session = service.create(question="Q", idempotency_key="c")
    session = service.start(session.session_id, expected_revision=0, idempotency_key="s", assignments=assignments())
    task = service.list_tasks(session.session_id)[0]
    updated = service.update_task_status(
        session.session_id, task.task_id, status=TaskStatus.AWAITING_USER,
        expected_revision=session.revision, idempotency_key="task-1",
    )
    assert updated.status == TaskStatus.AWAITING_USER
    assert service.get(session.session_id).revision == 2
    with pytest.raises(StaleRevisionError):
        service.update_task_status(
            session.session_id, task.task_id, status=TaskStatus.RECEIVED,
            expected_revision=1, idempotency_key="task-2",
        )

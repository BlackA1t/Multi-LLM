from __future__ import annotations

import hashlib
import sqlite3
from uuid import uuid4

import pytest

from multi_llm.research import ResearchSessionService, TaskStatus
from multi_llm.research.provider_catalog import R0_ASSIGNMENTS
from multi_llm.research.repository import MAX_CHUNK_BYTES, MAX_RESPONSE_BYTES
from multi_llm.research.migration import MIGRATIONS
from multi_llm.research.native_protocol import NativeDispatcher, encode_message
from multi_llm.research.workflow import ResearchDomainError


def assignments():
    return list(R0_ASSIGNMENTS)


def started_service(tmp_path, *, clipboard_writer=lambda _text: None):
    service = ResearchSessionService(tmp_path / "research.db", clipboard_writer=clipboard_writer)
    session = service.create(question="Тест CRLF\r\nи Unicode 🚀", idempotency_key="create")
    session = service.start(
        session.session_id, expected_revision=0, idempotency_key="start", assignments=assignments()
    )
    return service, session, service.list_tasks(session.session_id)


def test_v1_database_migrates_without_losing_session_task_or_prompt(tmp_path):
    path = tmp_path / "research.db"
    session_id, round_id, task_id, prompt_id = [str(uuid4()) for _ in range(4)]
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)")
        connection.executescript(MIGRATIONS[0][1])
        connection.execute("INSERT INTO schema_migrations VALUES (1, CURRENT_TIMESTAMP)")
        now = "2026-11-14T00:00:00+00:00"
        connection.execute(
            "INSERT INTO research_sessions VALUES (?, ?, ?, 'INDEPENDENT', 'ACTIVE', 3, NULL, ?, ?, ?)",
            (session_id, "P103 question", "P103 Runtime Test", "p103-create", now, now),
        )
        connection.execute(
            "INSERT INTO research_rounds VALUES (?, ?, 'INDEPENDENT', 1, 'ACTIVE', ?)",
            (round_id, session_id, now),
        )
        connection.execute(
            "INSERT INTO research_tasks VALUES (?, ?, ?, 'INDEPENDENT', 'INDEPENDENT_RESPONSE', 'gemini', 'role', 0, 'PROMPT_READY', NULL, 0, ?, ?)",
            (task_id, session_id, round_id, now, now),
        )
        prompt = "original P103 prompt"
        connection.execute(
            "INSERT INTO prompt_artifacts VALUES (?, ?, 1, ?, ?, ?, ?)",
            (prompt_id, task_id, prompt, len(prompt.encode()), hashlib.sha256(prompt.encode()).hexdigest(), now),
        )
    service = ResearchSessionService(path)
    restored = service.get(session_id)
    assert (restored.phase.value, restored.status.value, restored.revision) == ("INDEPENDENT", "ACTIVE", 3)
    assert str(service.list_tasks(session_id)[0].task_id) == task_id
    assert service.repository.get_prompt_text(session_id, task_id) == "original P103 prompt"
    with sqlite3.connect(path) as connection:
        assert [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3, 4, 5]
    ResearchSessionService(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 5


def upload(
    service, session, task, text, key, *, mode="create", result_version=None,
    chunk_size=MAX_CHUNK_BYTES, return_upload_id=False,
):
    payload = text.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    reservation = service.begin_upload(
        session.session_id, task.task_id, mode=mode, size_bytes=len(payload), sha256=digest,
        expected_revision=session.revision, expected_result_version=result_version,
        idempotency_key=key,
    )
    for index, offset in enumerate(range(0, len(payload), chunk_size)):
        chunk = payload[offset:offset + chunk_size]
        service.put_upload_part(
            reservation.upload_id, part_index=index, chunk=chunk,
            sha256=hashlib.sha256(chunk).hexdigest(),
        )
    receipt = service.commit_upload(
        reservation.upload_id, session_id=session.session_id, task_id=task.task_id,
        expected_revision=session.revision, idempotency_key=key, source_type="web_manual",
        reported_model=None,
    )
    return (receipt, reservation.upload_id) if return_upload_id else receipt


def test_prompt_roundtrip_and_explicit_clipboard_copy(tmp_path):
    copied = []
    service, session, tasks = started_service(tmp_path, clipboard_writer=copied.append)
    task = tasks[0]
    metadata = service.prompt_metadata(session.session_id, task.task_id)
    data = bytearray()
    offset = 0
    while offset < metadata.size_bytes:
        chunk, total = service.prompt_chunk(session.session_id, task.task_id, offset=offset, limit=7)
        assert total == metadata.size_bytes
        data.extend(chunk)
        offset += len(chunk)
    assert hashlib.sha256(data).hexdigest() == metadata.sha256
    assert copied == []
    service.copy_prompt(session.session_id, task.task_id)
    assert copied == [data.decode("utf-8")]


def test_upload_parts_are_idempotent_and_conflicts_are_rejected(tmp_path):
    service, session, tasks = started_service(tmp_path)
    data = "ответ".encode()
    reservation = service.begin_upload(
        session.session_id, tasks[0].task_id, mode="create", size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(), expected_revision=session.revision,
        expected_result_version=None, idempotency_key="response-1",
    )
    digest = hashlib.sha256(data).hexdigest()
    first = service.put_upload_part(reservation.upload_id, part_index=0, chunk=data, sha256=digest)
    repeated = service.put_upload_part(reservation.upload_id, part_index=0, chunk=data, sha256=digest)
    assert first == repeated
    with pytest.raises(ResearchDomainError) as raised:
        service.put_upload_part(
            reservation.upload_id, part_index=0, chunk=b"other",
            sha256=hashlib.sha256(b"other").hexdigest(),
        )
    assert raised.value.code == "CHUNK_CONFLICT"
    assert service.list_responses(session.session_id) == []


def test_upload_rejects_chunks_beyond_declared_size(tmp_path):
    service, session, tasks = started_service(tmp_path)
    data = b"tiny"
    reservation = service.begin_upload(
        session.session_id, tasks[0].task_id, mode="create", size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(), expected_revision=session.revision,
        expected_result_version=None, idempotency_key="bounded-staging",
    )
    with pytest.raises(ResearchDomainError):
        service.put_upload_part(
            reservation.upload_id, part_index=1, chunk=data,
            sha256=hashlib.sha256(data).hexdigest(),
        )


@pytest.mark.parametrize("text", ["   \r\n\t", "ok"])
def test_commit_validates_content_and_integrity(tmp_path, text):
    service, session, tasks = started_service(tmp_path)
    payload = text.encode()
    expected_payload = payload if not text.strip() else b"x" * MAX_CHUNK_BYTES + payload
    reservation = service.begin_upload(
        session.session_id, tasks[0].task_id, mode="create", size_bytes=len(expected_payload),
        sha256=hashlib.sha256(expected_payload).hexdigest(), expected_revision=session.revision,
        expected_result_version=None, idempotency_key="integrity",
    )
    if payload:
        service.put_upload_part(
            reservation.upload_id, part_index=1 if text.strip() else 0, chunk=payload,
            sha256=hashlib.sha256(payload).hexdigest(),
        )
    with pytest.raises(ResearchDomainError) as raised:
        service.commit_upload(
            reservation.upload_id, session_id=session.session_id, task_id=tasks[0].task_id,
            expected_revision=session.revision, idempotency_key="integrity",
            source_type="web_manual", reported_model=None,
        )
    assert raised.value.code == "BAD_REQUEST"


@pytest.mark.parametrize("payload", [b"\xff\xfe", b"valid bytes"])
def test_commit_rejects_invalid_utf8_or_wrong_total_hash(tmp_path, payload):
    service, session, tasks = started_service(tmp_path)
    advertised = hashlib.sha256(payload if payload.startswith(b"\xff") else b"different").hexdigest()
    reservation = service.begin_upload(
        session.session_id, tasks[0].task_id, mode="create", size_bytes=len(payload),
        sha256=advertised, expected_revision=session.revision,
        expected_result_version=None, idempotency_key="bad-payload",
    )
    service.put_upload_part(
        reservation.upload_id, part_index=0, chunk=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    with pytest.raises(ResearchDomainError) as raised:
        service.commit_upload(
            reservation.upload_id, session_id=session.session_id, task_id=tasks[0].task_id,
            expected_revision=session.revision, idempotency_key="bad-payload",
            source_type="web_manual", reported_model=None,
        )
    assert raised.value.code == "BAD_REQUEST"
    assert service.list_responses(session.session_id) == []


def test_commit_is_atomic_persistent_and_lost_ack_retry_is_identical(tmp_path):
    service, session, tasks = started_service(tmp_path)
    text = "Большой синтетический ответ 🚀\r\n" * 1000
    receipt, upload_id = upload(
        service, session, tasks[0], text, "response-atomic", return_upload_id=True
    )
    repeated = service.commit_upload(
        upload_id, session_id=session.session_id,
        task_id=tasks[0].task_id, expected_revision=session.revision,
        idempotency_key="response-atomic", source_type="web_manual", reported_model=None,
    )
    assert repeated == receipt
    assert receipt.task.status == TaskStatus.RECEIVED
    restarted = ResearchSessionService(tmp_path / "research.db")
    metadata = restarted.list_responses(session.session_id)[0]
    restored = bytearray()
    offset = 0
    while offset < metadata.size_bytes:
        chunk, total = restarted.response_chunk(
            session.session_id, metadata.result_id, version=metadata.version,
            offset=offset, limit=MAX_CHUNK_BYTES,
        )
        assert total == metadata.size_bytes
        restored.extend(chunk)
        offset += len(chunk)
    assert restored.decode() == text
    with sqlite3.connect(tmp_path / "research.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM research_results").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM response_revisions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM session_events WHERE event_type='response.committed'").fetchone()[0] == 1


def test_revision_preserves_original_and_is_blocked_after_phase_change(tmp_path):
    service, session, tasks = started_service(tmp_path)
    first = upload(service, session, tasks[0], "revision one", "r1")
    session = service.get(session.session_id)
    second = upload(
        service, session, first.task, "revision two", "r2", mode="revise",
        result_version=first.response.version,
    )
    assert second.response.version == 2
    with sqlite3.connect(tmp_path / "research.db") as connection:
        assert [row[0] for row in connection.execute(
            "SELECT response_text FROM response_revisions ORDER BY version"
        )] == ["revision one", "revision two"]
    session = service.get(session.session_id)
    for index, task in enumerate(tasks[1:], start=1):
        upload(service, session, task, f"other-{index}", f"other-{index}")
        session = service.get(session.session_id)
    advanced = service.advance(
        session.session_id, expected_revision=session.revision,
        idempotency_key="advance-after-revision", gap_reason=None,
    )
    payload = b"late revision"
    with pytest.raises(ResearchDomainError) as raised:
        service.begin_upload(
            advanced.session_id, tasks[0].task_id, mode="revise", size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(), expected_revision=advanced.revision,
            expected_result_version=2, idempotency_key="late-revision",
        )
    assert raised.value.code == "INVALID_PHASE"


def test_reassign_updates_only_pending_task_and_prompt_version(tmp_path):
    service, session, tasks = started_service(tmp_path)
    changed = service.reassign_task(
        session.session_id, tasks[0].task_id, provider_id="perplexity",
        expected_revision=session.revision, idempotency_key="reassign",
    )
    assert changed.provider_id == "perplexity" and changed.status == TaskStatus.PROMPT_READY
    assert service.prompt_metadata(session.session_id, changed.task_id).version == 2
    untouched = service.list_tasks(session.session_id)[1:]
    assert [task.provider_id for task in untouched] == [item.provider_id for item in R0_ASSIGNMENTS[1:]]
    current = service.get(session.session_id)
    with pytest.raises(ResearchDomainError):
        service.reassign_task(
            session.session_id, changed.task_id, provider_id="deepseek",
            expected_revision=current.revision, idempotency_key="duplicate",
        )
    receipt = upload(service, current, changed, "confirmed", "confirmed-after-reassign")
    current = service.get(session.session_id)
    with pytest.raises(ResearchDomainError):
        service.reassign_task(
            session.session_id, receipt.task.task_id, provider_id="chatgpt",
            expected_revision=current.revision, idempotency_key="received-reassign",
        )


def test_gate_requires_exact_states_and_stored_reason(tmp_path):
    service, session, tasks = started_service(tmp_path)
    for index in range(7):
        receipt = upload(service, session, tasks[index], f"answer-{index}", f"answer-{index}")
        session = service.get(session.session_id)
    with pytest.raises(ResearchDomainError):
        service.advance(
            session.session_id, expected_revision=session.revision,
            idempotency_key="bad-gap", gap_reason="one source missing",
        )
    unavailable = service.mark_unavailable(
        session.session_id, tasks[7].task_id, reason="service unavailable",
        expected_revision=session.revision, idempotency_key="unavailable",
    )
    session = service.get(session.session_id)
    service.skip_task(
        session.session_id, unavailable.task_id, reason="service unavailable",
        expected_revision=session.revision, idempotency_key="skip",
    )
    session = service.get(session.session_id)
    advanced = service.advance(
        session.session_id, expected_revision=session.revision,
        idempotency_key="good-gap", gap_reason="accepted missing source",
    )
    assert advanced.phase.value == "COMPARISON"


def test_limits_reject_oversize_without_staging(tmp_path):
    service, session, tasks = started_service(tmp_path)
    with pytest.raises(ResearchDomainError):
        service.begin_upload(
            session.session_id, tasks[0].task_id, mode="create", size_bytes=MAX_RESPONSE_BYTES + 1,
            sha256="0" * 64, expected_revision=session.revision, expected_result_version=None,
            idempotency_key="too-large",
        )
    with pytest.raises(ResearchDomainError):
        service.put_upload_part(
            "00000000-0000-4000-8000-000000000001", part_index=0,
            chunk=b"x" * (MAX_CHUNK_BYTES + 1), sha256=hashlib.sha256(b"x" * (MAX_CHUNK_BYTES + 1)).hexdigest(),
        )


def test_eight_mib_response_crosses_unicode_chunk_boundary_and_host_output_stays_bounded(tmp_path):
    service, session, tasks = started_service(tmp_path)
    payload = b"x" * (MAX_CHUNK_BYTES - 1) + "🚀".encode() + b"y" * (
        MAX_RESPONSE_BYTES - MAX_CHUNK_BYTES - 3
    )
    assert len(payload) == MAX_RESPONSE_BYTES
    digest = hashlib.sha256(payload).hexdigest()
    reservation = service.begin_upload(
        session.session_id, tasks[0].task_id, mode="create", size_bytes=len(payload), sha256=digest,
        expected_revision=session.revision, expected_result_version=None, idempotency_key="max-size",
    )
    for index, offset in enumerate(range(0, len(payload), MAX_CHUNK_BYTES)):
        chunk = payload[offset:offset + MAX_CHUNK_BYTES]
        service.put_upload_part(
            reservation.upload_id, part_index=index, chunk=chunk,
            sha256=hashlib.sha256(chunk).hexdigest(),
        )
    receipt = service.commit_upload(
        reservation.upload_id, session_id=session.session_id, task_id=tasks[0].task_id,
        expected_revision=session.revision, idempotency_key="max-size", source_type="web_manual",
        reported_model=None,
    )
    dispatcher = NativeDispatcher(service)
    response = dispatcher.dispatch({
        "v": 1, "id": str(uuid4()), "op": "response.chunk",
        "args": {
            "session_id": session.session_id, "result_id": receipt.response.result_id,
            "version": 1, "offset": MAX_CHUNK_BYTES - 1, "limit": MAX_CHUNK_BYTES,
        },
    })
    assert response["ok"] is True
    assert len(encode_message(response)) < 1024 * 1024

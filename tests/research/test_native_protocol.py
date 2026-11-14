from io import BytesIO
import json
import struct
from uuid import uuid4

import pytest

from multi_llm.research.native_host import run
from multi_llm.research.native_protocol import (
    MAX_CHROME_MESSAGE_BYTES,
    FrameError,
    NativeDispatcher,
    encode_message,
    read_message,
)
from multi_llm.research.service import ResearchSessionService


def request(operation, args=None, *, version=1, request_id=None):
    return {"v": version, "id": request_id or str(uuid4()), "op": operation, "args": args or {}}


def test_native_framing_round_trips_unicode_and_emoji():
    message = request("session.create", {"question": "Кириллица 🚀", "idempotency_key": "ключ"})
    assert read_message(BytesIO(encode_message(message))) == message


@pytest.mark.parametrize(
    "payload,code",
    [
        (b"\x01\x00", "BAD_REQUEST"),
        (struct.pack("=I", 8) + b"{}", "BAD_REQUEST"),
        (struct.pack("=I", MAX_CHROME_MESSAGE_BYTES + 1), "TOO_LARGE"),
        (struct.pack("=I", 1) + b"{", "BAD_REQUEST"),
    ],
)
def test_bad_prefix_truncated_oversized_and_invalid_json_are_typed(payload, code):
    with pytest.raises(FrameError) as raised:
        read_message(BytesIO(payload))
    assert raised.value.code == code


def test_output_larger_than_one_mib_is_rejected():
    with pytest.raises(FrameError) as raised:
        encode_message({"value": "x" * (1024 * 1024)})
    assert raised.value.code == "TOO_LARGE"


def test_unknown_operation_and_version_have_no_side_effects(tmp_path):
    service = ResearchSessionService(tmp_path / "research.db")
    dispatcher = NativeDispatcher(service)
    unknown = dispatcher.dispatch(request("session.erase", {"question": "must not persist"}))
    unsupported = dispatcher.dispatch(request("session.create", {"question": "also not persist"}, version=2))
    assert unknown["error"]["code"] == "BAD_REQUEST"
    assert unsupported["error"]["code"] == "UNSUPPORTED_VERSION"
    assert service.list() == []


def test_native_host_stdout_contains_only_frames_and_logs_do_not_leak_question(tmp_path, capsys):
    question = "СЕКРЕТНЫЙ ВОПРОС 🚀"
    raw_request = request("session.create", {"question": question, "idempotency_key": "create"})
    stdin = BytesIO(encode_message(raw_request))
    stdout = BytesIO()
    assert run(stdin, stdout, ResearchSessionService(tmp_path / "research.db")) == 0
    stdout.seek(0)
    response = read_message(stdout)
    assert response["ok"] is True
    assert response["result"]["question"] == question
    assert read_message(stdout) is None
    captured = capsys.readouterr()
    assert question not in captured.out and question not in captured.err


def test_bad_request_error_does_not_echo_user_content(tmp_path):
    dispatcher = NativeDispatcher(ResearchSessionService(tmp_path / "research.db"))
    secret = "DO_NOT_ECHO_SECRET"
    response = dispatcher.dispatch(request("session.create", {"question": secret, "unexpected": secret}))
    assert response["ok"] is False
    assert secret not in json.dumps(response)

from __future__ import annotations

from io import BufferedReader, BufferedWriter
import json
import sqlite3
import struct
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from .models import Assignment
from .service import ResearchSessionService
from .workflow import ResearchDomainError


PROTOCOL_VERSION = 1
MAX_CHROME_MESSAGE_BYTES = 64 * 1024 * 1024
MAX_HOST_MESSAGE_BYTES = 1024 * 1024
_LENGTH = struct.Struct("=I")


class FrameError(ResearchDomainError):
    pass


class RequestEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: StrictInt
    id: UUID
    op: str = Field(min_length=1, max_length=100)
    args: dict[str, Any]


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionCreateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    title: str | None = Field(default=None, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)


class SessionIdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID


class SessionListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: StrictInt = Field(default=50, ge=1, le=50)
    cursor: StrictInt = Field(default=0, ge=0)


class MutationArgs(SessionIdArgs):
    expected_revision: StrictInt = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)


class SessionStartArgs(MutationArgs):
    assignments: list[Assignment] = Field(min_length=4, max_length=4)


class SessionAdvanceArgs(MutationArgs):
    gap_reason: str | None = Field(default=None, max_length=2000)


def _read_exact(stream: BufferedReader, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise FrameError("BAD_REQUEST", "Native message was truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream: BufferedReader) -> dict[str, Any] | None:
    prefix = stream.read(_LENGTH.size)
    if not prefix:
        return None
    if len(prefix) != _LENGTH.size:
        raise FrameError("BAD_REQUEST", "Native message length prefix was truncated")
    length = _LENGTH.unpack(prefix)[0]
    if length == 0:
        raise FrameError("BAD_REQUEST", "Native message must not be empty")
    if length > MAX_CHROME_MESSAGE_BYTES:
        raise FrameError("TOO_LARGE", "Native message exceeds the input limit")
    payload = _read_exact(stream, length)
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrameError("BAD_REQUEST", "Native message is not valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise FrameError("BAD_REQUEST", "Native message must be a JSON object")
    return parsed


def encode_message(message: dict[str, Any]) -> bytes:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_HOST_MESSAGE_BYTES:
        raise FrameError("TOO_LARGE", "Native response exceeds the output limit")
    return _LENGTH.pack(len(payload)) + payload


def write_message(stream: BufferedWriter, message: dict[str, Any]) -> None:
    stream.write(encode_message(message))
    stream.flush()


class NativeDispatcher:
    def __init__(self, service: ResearchSessionService):
        self.service = service

    def dispatch(self, raw: dict[str, Any]) -> dict[str, Any]:
        request_id = raw.get("id") if isinstance(raw.get("id"), str) else None
        try:
            request = RequestEnvelope.model_validate(raw)
            request_id = str(request.id)
            if request.v != PROTOCOL_VERSION:
                raise ResearchDomainError("UNSUPPORTED_VERSION", "Unsupported native protocol version")
            result = self._execute(request.op, request.args)
            return {"v": PROTOCOL_VERSION, "id": request_id, "ok": True, "result": result}
        except ValidationError:
            return self.error(request_id, "BAD_REQUEST", "Invalid native request", False)
        except ResearchDomainError as exc:
            return self.error(request_id, exc.code, str(exc), exc.retryable)
        except sqlite3.DatabaseError:
            return self.error(request_id, "DATABASE_ERROR", "Research database operation failed", True)
        except Exception:
            return self.error(request_id, "INTERNAL_ERROR", "Internal native host error", False)

    @staticmethod
    def error(request_id: str | None, code: str, message: str, retryable: bool) -> dict[str, Any]:
        return {
            "v": PROTOCOL_VERSION,
            "id": request_id,
            "ok": False,
            "error": {"code": code, "message": message, "retryable": retryable},
        }

    def _execute(self, operation: str, raw_args: dict[str, Any]) -> Any:
        if operation == "system.hello":
            EmptyArgs.model_validate(raw_args)
            return {
                "protocol_version": PROTOCOL_VERSION,
                "operations": [
                    "system.hello", "session.create", "session.get", "session.list",
                    "session.start", "session.pause", "session.resume", "session.advance", "task.list",
                ],
            }
        if operation == "session.create":
            args = SessionCreateArgs.model_validate(raw_args)
            return self.service.create(**args.model_dump()).model_dump(mode="json")
        if operation == "session.get":
            args = SessionIdArgs.model_validate(raw_args)
            return self.service.get(args.session_id).model_dump(mode="json")
        if operation == "session.list":
            args = SessionListArgs.model_validate(raw_args)
            sessions = self.service.list(limit=args.limit, offset=args.cursor)
            return {
                "items": [item.model_dump(mode="json") for item in sessions],
                "next_cursor": args.cursor + len(sessions) if len(sessions) == args.limit else None,
            }
        if operation == "session.start":
            args = SessionStartArgs.model_validate(raw_args)
            return self.service.start(
                args.session_id, expected_revision=args.expected_revision,
                idempotency_key=args.idempotency_key, assignments=args.assignments,
            ).model_dump(mode="json")
        if operation in {"session.pause", "session.resume"}:
            args = MutationArgs.model_validate(raw_args)
            method = self.service.pause if operation == "session.pause" else self.service.resume
            return method(
                args.session_id, expected_revision=args.expected_revision,
                idempotency_key=args.idempotency_key,
            ).model_dump(mode="json")
        if operation == "session.advance":
            args = SessionAdvanceArgs.model_validate(raw_args)
            return self.service.advance(
                args.session_id, expected_revision=args.expected_revision,
                idempotency_key=args.idempotency_key, gap_reason=args.gap_reason,
            ).model_dump(mode="json")
        if operation == "task.list":
            args = SessionIdArgs.model_validate(raw_args)
            return {"items": [task.model_dump(mode="json") for task in self.service.list_tasks(args.session_id)]}
        raise ResearchDomainError("BAD_REQUEST", "Unsupported operation")

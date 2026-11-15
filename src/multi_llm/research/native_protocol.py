from __future__ import annotations

from io import BufferedReader, BufferedWriter
import base64
import binascii
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
    perplexity_prompt: str | None = Field(default=None, max_length=2 * 1024 * 1024)
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
    assignments: list[Assignment] | None = None


class SessionUpdateIntakeArgs(MutationArgs):
    question: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    title: str | None = Field(default=None, max_length=200)
    perplexity_prompt: str | None = Field(default=None, max_length=2 * 1024 * 1024)


class SessionAdvanceArgs(MutationArgs):
    gap_reason: str | None = Field(default=None, max_length=2000)


class TaskIdArgs(SessionIdArgs):
    task_id: UUID


class ChunkArgs(TaskIdArgs):
    offset: StrictInt = Field(ge=0)
    limit: StrictInt = Field(default=32_768, ge=1, le=32_768)


class TaskMutationArgs(TaskIdArgs):
    expected_revision: StrictInt = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)


class TaskReasonArgs(TaskMutationArgs):
    reason: str = Field(min_length=1, max_length=2000)


class TaskReassignArgs(TaskMutationArgs):
    provider_id: str = Field(min_length=1, max_length=100)


class UploadBeginArgs(TaskMutationArgs):
    mode: Literal["create", "revise"]
    size_bytes: StrictInt = Field(ge=1, le=8 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_result_version: StrictInt | None = Field(default=None, ge=1)


class UploadPartArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: UUID
    part_index: StrictInt = Field(ge=0)
    chunk_b64: str = Field(min_length=1, max_length=50_000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class UploadCommitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: UUID
    session_id: UUID
    task_id: UUID
    expected_revision: StrictInt = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    source_type: Literal["web_manual"] = "web_manual"
    reported_model: str | None = Field(default=None, max_length=200)


class UploadAbortArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: UUID


class ResponseListArgs(SessionIdArgs):
    task_id: UUID | None = None


class ResponseChunkArgs(SessionIdArgs):
    result_id: UUID
    version: StrictInt = Field(ge=1)
    offset: StrictInt = Field(ge=0)
    limit: StrictInt = Field(default=32_768, ge=1, le=32_768)


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
                    "system.hello", "session.create", "session.update_intake", "session.get", "session.list",
                    "session.start", "session.pause", "session.resume", "session.advance", "task.list",
                    "task.prompt.meta", "task.prompt.chunk", "task.prompt.copy", "task.mark_awaiting",
                    "response.upload.begin", "response.upload.part", "response.upload.commit",
                    "response.upload.abort", "response.list", "response.chunk", "task.mark_unavailable",
                    "task.skip", "task.reassign",
                ],
            }
        if operation == "session.create":
            args = SessionCreateArgs.model_validate(raw_args)
            return self.service.create(**args.model_dump()).model_dump(mode="json")
        if operation == "session.get":
            args = SessionIdArgs.model_validate(raw_args)
            return self.service.get(args.session_id).model_dump(mode="json")
        if operation == "session.update_intake":
            args = SessionUpdateIntakeArgs.model_validate(raw_args)
            return self.service.update_intake(**args.model_dump()).model_dump(mode="json")
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
        if operation == "task.prompt.meta":
            args = TaskIdArgs.model_validate(raw_args)
            return self.service.prompt_metadata(args.session_id, args.task_id).model_dump(mode="json")
        if operation == "task.prompt.chunk":
            args = ChunkArgs.model_validate(raw_args)
            chunk, total = self.service.prompt_chunk(
                args.session_id, args.task_id, offset=args.offset, limit=args.limit
            )
            return {
                "chunk_b64": base64.b64encode(chunk).decode("ascii"),
                "offset": args.offset,
                "size_bytes": len(chunk),
                "total_bytes": total,
                "done": args.offset + len(chunk) >= total,
            }
        if operation == "task.prompt.copy":
            args = TaskIdArgs.model_validate(raw_args)
            return self.service.copy_prompt(args.session_id, args.task_id).model_dump(mode="json")
        if operation == "task.mark_awaiting":
            args = TaskMutationArgs.model_validate(raw_args)
            return self.service.mark_awaiting(
                args.session_id, args.task_id, expected_revision=args.expected_revision,
                idempotency_key=args.idempotency_key,
            ).model_dump(mode="json")
        if operation == "response.upload.begin":
            args = UploadBeginArgs.model_validate(raw_args)
            return self.service.begin_upload(
                args.session_id, args.task_id, mode=args.mode, size_bytes=args.size_bytes,
                sha256=args.sha256, expected_revision=args.expected_revision,
                expected_result_version=args.expected_result_version,
                idempotency_key=args.idempotency_key,
            ).model_dump(mode="json")
        if operation == "response.upload.part":
            args = UploadPartArgs.model_validate(raw_args)
            try:
                chunk = base64.b64decode(args.chunk_b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ResearchDomainError("BAD_REQUEST", "Response chunk is not valid base64") from exc
            return self.service.put_upload_part(
                args.upload_id, part_index=args.part_index, chunk=chunk, sha256=args.sha256,
            ).model_dump(mode="json")
        if operation == "response.upload.commit":
            args = UploadCommitArgs.model_validate(raw_args)
            return self.service.commit_upload(
                args.upload_id, idempotency_key=args.idempotency_key,
                session_id=args.session_id, task_id=args.task_id, expected_revision=args.expected_revision,
                source_type=args.source_type, reported_model=args.reported_model,
            ).model_dump(mode="json")
        if operation == "response.upload.abort":
            args = UploadAbortArgs.model_validate(raw_args)
            return {"aborted": self.service.abort_upload(args.upload_id)}
        if operation == "response.list":
            args = ResponseListArgs.model_validate(raw_args)
            return {"items": [item.model_dump(mode="json") for item in self.service.list_responses(
                args.session_id, args.task_id
            )]}
        if operation == "response.chunk":
            args = ResponseChunkArgs.model_validate(raw_args)
            chunk, total = self.service.response_chunk(
                args.session_id, args.result_id, version=args.version,
                offset=args.offset, limit=args.limit,
            )
            return {
                "chunk_b64": base64.b64encode(chunk).decode("ascii"),
                "offset": args.offset,
                "size_bytes": len(chunk),
                "total_bytes": total,
                "done": args.offset + len(chunk) >= total,
            }
        if operation in {"task.mark_unavailable", "task.skip"}:
            args = TaskReasonArgs.model_validate(raw_args)
            method = self.service.mark_unavailable if operation == "task.mark_unavailable" else self.service.skip_task
            return method(
                args.session_id, args.task_id, reason=args.reason,
                expected_revision=args.expected_revision, idempotency_key=args.idempotency_key,
            ).model_dump(mode="json")
        if operation == "task.reassign":
            args = TaskReassignArgs.model_validate(raw_args)
            return self.service.reassign_task(
                args.session_id, args.task_id, provider_id=args.provider_id,
                expected_revision=args.expected_revision, idempotency_key=args.idempotency_key,
            ).model_dump(mode="json")
        raise ResearchDomainError("BAD_REQUEST", "Unsupported operation")

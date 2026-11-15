from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from typing import Any
from uuid import uuid4


LENGTH = struct.Struct("=I")
REQUIRED_OPERATIONS = {
    "session.update_intake",
    "task.prompt.meta", "task.prompt.chunk", "task.prompt.copy", "task.mark_awaiting",
    "response.upload.begin", "response.upload.part", "response.upload.commit",
    "response.upload.abort", "response.list", "response.chunk", "task.mark_unavailable",
    "task.skip", "task.reassign",
}


def frame(message: dict[str, Any]) -> bytes:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return LENGTH.pack(len(payload)) + payload


def request(process: subprocess.Popen[bytes], operation: str, args: dict[str, Any]) -> dict[str, Any]:
    request_id = str(uuid4())
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(frame({"v": 1, "id": request_id, "op": operation, "args": args}))
    process.stdin.flush()
    prefix = process.stdout.read(LENGTH.size)
    if len(prefix) != LENGTH.size:
        raise RuntimeError("Native Host returned a truncated length prefix")
    size = LENGTH.unpack(prefix)[0]
    payload = process.stdout.read(size)
    if len(payload) != size:
        raise RuntimeError("Native Host returned a truncated response")
    response = json.loads(payload.decode("utf-8"))
    if response.get("v") != 1 or response.get("id") != request_id or not isinstance(response.get("ok"), bool):
        raise RuntimeError("Native Host returned an invalid response envelope")
    return response


def success(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok") is not True or not isinstance(response.get("result"), dict):
        raise RuntimeError("Expected a successful Native Host response")
    return response["result"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic Native Messaging stdio acceptance probe")
    parser.add_argument("--host-exe", required=True, type=Path)
    args = parser.parse_args()
    host = args.host_exe.resolve()
    if not host.is_file() or host.suffix.lower() != ".exe":
        raise SystemExit("Host launcher must be an existing .exe")

    with tempfile.TemporaryDirectory(prefix="multi-llm-p104-") as directory:
        database = Path(directory) / "research.db"
        environment = os.environ.copy()
        environment["MULTI_LLM_RESEARCH_DB"] = str(database.resolve())
        process = subprocess.Popen(
            [str(host)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        try:
            hello = success(request(process, "system.hello", {}))
            if hello.get("protocol_version") != 1:
                raise RuntimeError("Native Host protocol version is not 1")
            if not REQUIRED_OPERATIONS.issubset(set(hello.get("operations", []))):
                raise RuntimeError("Native Host does not advertise all required operations")

            created = success(request(process, "session.create", {
                "question": "Синтетическая проверка устойчивости локальной сессии",
                "perplexity_prompt": "Синтетический поисковый промпт",
                "title": "P104 synthetic probe",
                "idempotency_key": str(uuid4()),
            }))
            session_id = created["session_id"]
            listed = success(request(process, "session.list", {"limit": 50, "cursor": 0}))
            if not any(item.get("session_id") == session_id for item in listed.get("items", [])):
                raise RuntimeError("Created session is missing from session.list")
            restored = success(request(process, "session.get", {"session_id": session_id}))
            if restored.get("revision") != 0:
                raise RuntimeError("Unexpected initial session revision")

            started = success(request(process, "session.start", {
                "session_id": session_id,
                "expected_revision": 0,
                "idempotency_key": str(uuid4()),
            }))
            tasks = success(request(process, "task.list", {"session_id": session_id}))
            mandatory_tasks = [item for item in tasks.get("items", []) if item.get("kind") == "INDEPENDENT_RESPONSE"]
            search_tasks = [item for item in tasks.get("items", []) if item.get("kind") == "SEARCH_EVIDENCE"]
            if len(mandatory_tasks) != 8 or len(search_tasks) != 1:
                raise RuntimeError("session.start did not create eight R0 tasks and one search task")
            paused = success(request(process, "session.pause", {
                "session_id": session_id,
                "expected_revision": started["revision"],
                "idempotency_key": str(uuid4()),
            }))
            resumed = success(request(process, "session.resume", {
                "session_id": session_id,
                "expected_revision": paused["revision"],
                "idempotency_key": str(uuid4()),
            }))
            rejected = request(process, "session.advance", {
                "session_id": session_id,
                "expected_revision": resumed["revision"],
                "idempotency_key": str(uuid4()),
            })
            if rejected.get("ok") is not False or rejected.get("error", {}).get("code") != "INVALID_PHASE":
                raise RuntimeError("Incomplete workflow gate was not rejected")
            unchanged = success(request(process, "session.get", {"session_id": session_id}))
            if unchanged.get("phase") != "INDEPENDENT" or unchanged.get("revision") != resumed["revision"]:
                raise RuntimeError("Rejected advance changed the session")

            first_task = tasks["items"][0]
            prompt_meta = success(request(process, "task.prompt.meta", {
                "session_id": session_id, "task_id": first_task["task_id"],
            }))
            prompt_part = success(request(process, "task.prompt.chunk", {
                "session_id": session_id, "task_id": first_task["task_id"],
                "offset": 0, "limit": 32768,
            }))
            prompt_bytes = base64.b64decode(prompt_part["chunk_b64"], validate=True)
            if len(prompt_bytes) != prompt_meta["size_bytes"] or hashlib.sha256(prompt_bytes).hexdigest() != prompt_meta["sha256"]:
                raise RuntimeError("Prompt chunk failed integrity validation")

            last_receipt = None
            for index, task in enumerate(mandatory_tasks):
                current = success(request(process, "session.get", {"session_id": session_id}))
                text = f"Синтетический ручной ответ {index} 🚀\r\n" * 100
                payload = text.encode("utf-8")
                digest = hashlib.sha256(payload).hexdigest()
                key = str(uuid4())
                upload = success(request(process, "response.upload.begin", {
                    "session_id": session_id, "task_id": task["task_id"], "mode": "create",
                    "size_bytes": len(payload), "sha256": digest,
                    "expected_revision": current["revision"], "idempotency_key": key,
                }))
                for part_index, offset in enumerate(range(0, len(payload), 32768)):
                    chunk = payload[offset:offset + 32768]
                    success(request(process, "response.upload.part", {
                        "upload_id": upload["upload_id"], "part_index": part_index,
                        "chunk_b64": base64.b64encode(chunk).decode("ascii"),
                        "sha256": hashlib.sha256(chunk).hexdigest(),
                    }))
                commit_args = {
                    "upload_id": upload["upload_id"], "session_id": session_id,
                    "task_id": task["task_id"], "source_type": "web_manual",
                    "expected_revision": current["revision"], "idempotency_key": key,
                }
                receipt = success(request(process, "response.upload.commit", commit_args))
                repeated = success(request(process, "response.upload.commit", commit_args))
                if repeated != receipt:
                    raise RuntimeError("Lost-ACK commit retry did not return the durable receipt")
                last_receipt = receipt

            responses = success(request(process, "response.list", {"session_id": session_id}))
            if len(responses.get("items", [])) != 8 or last_receipt is None:
                raise RuntimeError("Confirmed responses are missing from response.list")
            response_part = success(request(process, "response.chunk", {
                "session_id": session_id, "result_id": last_receipt["response"]["result_id"],
                "version": 1, "offset": 0, "limit": 32768,
            }))
            if not base64.b64decode(response_part["chunk_b64"], validate=True):
                raise RuntimeError("Confirmed response chunk is empty")
            current = success(request(process, "session.get", {"session_id": session_id}))
            advanced = success(request(process, "session.advance", {
                "session_id": session_id, "expected_revision": current["revision"],
                "idempotency_key": str(uuid4()),
            }))
            if advanced.get("phase") != "COMPARISON":
                raise RuntimeError("Eight confirmed responses did not pass the server gate")

            assert process.stdin is not None
            process.stdin.close()
            return_code = process.wait(timeout=10)
            assert process.stdout is not None and process.stderr is not None
            trailing_stdout = process.stdout.read()
            stderr = process.stderr.read()
            if return_code != 0 or trailing_stdout or stderr:
                raise RuntimeError("Native Host stdio channels were not clean")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    print(json.dumps({
        "status": "PASS",
        "protocol_version": 1,
        "operations_checked": 23,
        "tasks_created": 9,
        "initial_advance_gate": "INVALID_PHASE",
        "confirmed_responses": 8,
        "final_phase": "COMPARISON",
        "durable_receipt_retry": "PASS",
        "stdout_framing": "clean",
        "stderr": "empty",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

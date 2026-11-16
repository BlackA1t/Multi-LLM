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
    "s1.package.prepare", "s1.package.get", "s1.package.part.chunk", "s1.package.part.copy",
    "review.package.prepare", "review.package.list",
    "review.package.part.chunk", "review.package.part.copy",
    "s2.package.prepare", "s2.package.get", "s2.package.part.chunk", "s2.package.part.copy",
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
            mandatory_tasks = [item for item in tasks.get("items", []) if item.get("kind") == "R0_RESPONSE"]
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
            if unchanged.get("phase") != "R0" or unchanged.get("revision") != resumed["revision"]:
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
            if advanced.get("phase") != "S1":
                raise RuntimeError("Eight confirmed responses did not pass the server gate")
            package = success(request(process, "s1.package.get", {"session_id": session_id}))
            if package.get("part_count", 0) < 1 or len(package.get("inputs", [])) != 9:
                raise RuntimeError("S1 package manifest is incomplete")
            first_part = package["parts"][0]
            package_chunk = success(request(process, "s1.package.part.chunk", {
                "session_id": session_id, "package_id": package["package_id"],
                "part_index": 0, "offset": 0, "limit": 32768,
            }))
            if not base64.b64decode(package_chunk["chunk_b64"], validate=True):
                raise RuntimeError("S1 package part is empty")
            s1_tasks = success(request(process, "task.list", {"session_id": session_id}))["items"]
            s1_task = next((item for item in s1_tasks if item.get("kind") == "S1_SYNTHESIS"), None)
            if not s1_task:
                raise RuntimeError("S1 synthesis task is missing")
            current = success(request(process, "session.get", {"session_id": session_id}))
            s1_payload = "Синтетический отчёт S1".encode("utf-8")
            s1_digest = hashlib.sha256(s1_payload).hexdigest()
            s1_key = str(uuid4())
            s1_upload = success(request(process, "response.upload.begin", {
                "session_id": session_id, "task_id": s1_task["task_id"], "mode": "create",
                "size_bytes": len(s1_payload), "sha256": s1_digest,
                "expected_revision": current["revision"], "idempotency_key": s1_key,
            }))
            success(request(process, "response.upload.part", {
                "upload_id": s1_upload["upload_id"], "part_index": 0,
                "chunk_b64": base64.b64encode(s1_payload).decode("ascii"),
                "sha256": s1_digest,
            }))
            s1_receipt = success(request(process, "response.upload.commit", {
                "upload_id": s1_upload["upload_id"], "session_id": session_id,
                "task_id": s1_task["task_id"], "source_type": "web_manual",
                "reported_model": "ChatGPT Plus", "expected_revision": current["revision"],
                "idempotency_key": s1_key,
            }))
            if s1_receipt.get("task", {}).get("status") != "RECEIVED":
                raise RuntimeError("S1 report was not stored")

            current = success(request(process, "session.get", {"session_id": session_id}))
            review_phase = success(request(process, "session.advance", {
                "session_id": session_id, "expected_revision": current["revision"],
                "idempotency_key": str(uuid4()),
            }))
            if review_phase.get("phase") != "S1_REVIEW":
                raise RuntimeError("Received S1 did not open independent review")
            review_tasks = [item for item in success(request(
                process, "task.list", {"session_id": session_id}
            ))["items"] if item.get("kind") == "S1_REVIEW"]
            if [item.get("provider_id") for item in review_tasks] != ["deepseek", "grok", "gemini"]:
                raise RuntimeError("Independent reviewer set is incorrect")
            review_packages = success(request(
                process, "review.package.list", {"session_id": session_id}
            ))["items"]
            if len(review_packages) != 3 or any(item.get("stale") for item in review_packages):
                raise RuntimeError("Current reviewer packages are missing or stale")
            review_chunk = success(request(process, "review.package.part.chunk", {
                "session_id": session_id, "package_id": review_packages[0]["package_id"],
                "part_index": 0, "offset": 0, "limit": 32768,
            }))
            if not base64.b64decode(review_chunk["chunk_b64"], validate=True):
                raise RuntimeError("Reviewer package part is empty")
            for index, task in enumerate(review_tasks):
                current = success(request(process, "session.get", {"session_id": session_id}))
                review_payload = f"Синтетическая независимая рецензия {index}".encode("utf-8")
                review_digest = hashlib.sha256(review_payload).hexdigest()
                review_key = str(uuid4())
                review_upload = success(request(process, "response.upload.begin", {
                    "session_id": session_id, "task_id": task["task_id"], "mode": "create",
                    "size_bytes": len(review_payload), "sha256": review_digest,
                    "expected_revision": current["revision"], "idempotency_key": review_key,
                }))
                success(request(process, "response.upload.part", {
                    "upload_id": review_upload["upload_id"], "part_index": 0,
                    "chunk_b64": base64.b64encode(review_payload).decode("ascii"),
                    "sha256": review_digest,
                }))
                review_receipt = success(request(process, "response.upload.commit", {
                    "upload_id": review_upload["upload_id"], "session_id": session_id,
                    "task_id": task["task_id"], "source_type": "web_manual",
                    "reported_model": task["provider_id"],
                    "expected_revision": current["revision"], "idempotency_key": review_key,
                }))
                if review_receipt.get("task", {}).get("status") != "RECEIVED":
                    raise RuntimeError("Independent review was not stored")
            current = success(request(process, "session.get", {"session_id": session_id}))
            s2_phase = success(request(process, "session.advance", {
                "session_id": session_id, "expected_revision": current["revision"],
                "idempotency_key": str(uuid4()),
            }))
            if s2_phase.get("phase") != "S2":
                raise RuntimeError("Three reviews did not open S2 finalization")
            s2_package = success(request(process, "s2.package.get", {"session_id": session_id}))
            if s2_package.get("stale") or len(s2_package.get("reviews", [])) != 3:
                raise RuntimeError("S2 package provenance is incomplete or stale")
            s2_chunk = success(request(process, "s2.package.part.chunk", {
                "session_id": session_id, "package_id": s2_package["package_id"],
                "part_index": 0, "offset": 0, "limit": 32768,
            }))
            if not base64.b64decode(s2_chunk["chunk_b64"], validate=True):
                raise RuntimeError("S2 package part is empty")
            s2_task = next((item for item in success(request(
                process, "task.list", {"session_id": session_id}
            ))["items"] if item.get("kind") == "S2_SYNTHESIS"), None)
            if not s2_task:
                raise RuntimeError("S2 synthesis task is missing")
            current = success(request(process, "session.get", {"session_id": session_id}))
            s2_payload = "Синтетический итоговый отчёт S2".encode("utf-8")
            s2_digest = hashlib.sha256(s2_payload).hexdigest()
            s2_key = str(uuid4())
            s2_upload = success(request(process, "response.upload.begin", {
                "session_id": session_id, "task_id": s2_task["task_id"], "mode": "create",
                "size_bytes": len(s2_payload), "sha256": s2_digest,
                "expected_revision": current["revision"], "idempotency_key": s2_key,
            }))
            success(request(process, "response.upload.part", {
                "upload_id": s2_upload["upload_id"], "part_index": 0,
                "chunk_b64": base64.b64encode(s2_payload).decode("ascii"), "sha256": s2_digest,
            }))
            success(request(process, "response.upload.commit", {
                "upload_id": s2_upload["upload_id"], "session_id": session_id,
                "task_id": s2_task["task_id"], "source_type": "web_manual",
                "reported_model": "ChatGPT Plus", "expected_revision": current["revision"],
                "idempotency_key": s2_key,
            }))
            completed = success(request(process, "session.get", {"session_id": session_id}))
            if completed.get("phase") != "DONE" or completed.get("status") != "COMPLETED":
                raise RuntimeError("S2 import did not complete the research session")

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
        "operations_checked": 35,
        "tasks_created": 14,
        "initial_advance_gate": "INVALID_PHASE",
        "confirmed_responses": 8,
        "final_phase": "DONE",
        "s1_package_parts": package["part_count"],
        "s1_import": "RECEIVED",
        "reviewer_packages": 3,
        "review_imports": 3,
        "s2_package_parts": s2_package["part_count"],
        "s2_import": "COMPLETED",
        "legacy_pipeline": "REMOVED",
        "durable_receipt_retry": "PASS",
        "stdout_framing": "clean",
        "stderr": "empty",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

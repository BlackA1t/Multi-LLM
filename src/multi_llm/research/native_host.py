from __future__ import annotations

import os
from pathlib import Path
import sys

from .native_protocol import FrameError, NativeDispatcher, encode_message, read_message, write_message
from .service import ResearchSessionService


def _binary_stdio() -> tuple[object, object]:
    if os.name == "nt":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    return sys.stdin.buffer, sys.stdout.buffer


def _database_path() -> Path:
    configured = os.environ.get("MULTI_LLM_RESEARCH_DB")
    if configured is not None:
        if not configured.strip():
            raise RuntimeError("MULTI_LLM_RESEARCH_DB must not be empty")
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise RuntimeError("MULTI_LLM_RESEARCH_DB must be an absolute path")
    else:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data or not local_app_data.strip():
            raise RuntimeError("LOCALAPPDATA is unavailable for the research database")
        base = Path(local_app_data).expanduser()
        if not base.is_absolute() or base == Path(base.anchor):
            raise RuntimeError("LOCALAPPDATA is invalid for the research database")
        path = base / "Multi-LLM" / "research-v2.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def run(stdin: object, stdout: object, service: ResearchSessionService) -> int:
    dispatcher = NativeDispatcher(service)
    while True:
        try:
            raw = read_message(stdin)  # type: ignore[arg-type]
            if raw is None:
                return 0
            response = dispatcher.dispatch(raw)
            try:
                write_message(stdout, response)  # type: ignore[arg-type]
            except FrameError as exc:
                request_id = response.get("id") if isinstance(response.get("id"), str) else None
                fallback = dispatcher.error(request_id, exc.code, str(exc), exc.retryable)
                stdout.write(encode_message(fallback))
                stdout.flush()
        except FrameError as exc:
            response = dispatcher.error(None, exc.code, str(exc), exc.retryable)
            write_message(stdout, response)  # type: ignore[arg-type]
        except Exception:
            print("multi-llm-native-host: INTERNAL_ERROR", file=sys.stderr)
            return 1


def main() -> None:
    stdin, stdout = _binary_stdio()
    service = ResearchSessionService(_database_path())
    raise SystemExit(run(stdin, stdout, service))


if __name__ == "__main__":
    main()

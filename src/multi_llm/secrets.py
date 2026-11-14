from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

import keyring
from keyring.errors import PasswordDeleteError

from .errors import CredentialIntegrityError, CredentialStoreError

SERVICE = "multi-llm-p04"
POINTER_MARKER = "multi-llm-chunked-v1"
MANIFEST_MARKER = "multi-llm-secret-v1"
CRED_MAX_CREDENTIAL_BLOB_SIZE = 5 * 512
# keyring's Windows backend passes str as wchar_t[]. Keep a substantial margin.
CHUNK_CHARACTER_LIMIT = 1000
MAX_SECRET_BYTES = 1024 * 1024


@dataclass(frozen=True)
class CredentialSizeInfo:
    utf8_bytes: int
    direct_windows_blob_bytes: int
    chunk_count: int
    max_chunk_windows_blob_bytes: int


def windows_blob_size(value: str) -> int:
    """Exact CredWriteW blob size for keyring's UTF-16LE string backend."""
    return len(value.encode("utf-16-le"))


class CredentialStore:
    """Versioned chunk storage backed exclusively by the OS credential vault."""

    def __init__(self, backend: Any | None = None, *, service: str = SERVICE):
        self.backend = backend or keyring.get_keyring()
        self.service = service

    @staticmethod
    def size_info(value: str) -> CredentialSizeInfo:
        raw = value.encode("utf-8")
        encoded_length = 4 * math.ceil(len(raw) / 3) if raw else 0
        chunks = max(1, math.ceil(encoded_length / CHUNK_CHARACTER_LIMIT))
        max_chars = min(CHUNK_CHARACTER_LIMIT, encoded_length)
        return CredentialSizeInfo(
            utf8_bytes=len(raw),
            direct_windows_blob_bytes=windows_blob_size(value),
            chunk_count=chunks,
            max_chunk_windows_blob_bytes=max_chars * 2,
        )

    def set(self, name: str, value: str) -> None:
        self.set_atomic(name, value)

    def set_atomic(self, name: str, value: str) -> None:
        if not value:
            raise ValueError("Credential cannot be empty")
        raw = value.encode("utf-8")
        if len(raw) > MAX_SECRET_BYTES:
            raise CredentialStoreError("Credential exceeds the configured reasonable size limit")

        pointer_name = self._pointer_name(name)
        previous_raw = self._raw_get(pointer_name)
        previous_pointer = self._parse_pointer(previous_raw)
        stale = list(previous_pointer.get("stale", [])) if previous_pointer else []
        if previous_pointer:
            stale.insert(0, self._descriptor(previous_pointer))

        descriptor = self._write_generation(name, raw)
        pointer = {
            "format": POINTER_MARKER,
            "active": descriptor["version"],
            "chunks": descriptor["chunks"],
            "stale": stale,
        }
        pointer_raw = self._compact(pointer)
        switched = False
        try:
            self._raw_set(pointer_name, pointer_raw)
            switched = True
            restored = self.get(name)
            if restored is None or not secrets.compare_digest(restored, value):
                raise CredentialIntegrityError("Credential verification failed after active-version switch")
        except Exception as exc:
            if switched:
                self._restore_pointer(pointer_name, previous_raw)
            self._delete_generation(name, descriptor)
            if isinstance(exc, CredentialStoreError):
                raise
            raise CredentialStoreError("Credential rotation failed; the previous version remains active") from None

        remaining = [item for item in stale if not self._delete_generation(name, item)]
        if remaining:
            pointer["stale"] = remaining
        else:
            pointer.pop("stale", None)
        try:
            self._raw_set(pointer_name, self._compact(pointer))
        except Exception:
            # The already-verified active pointer remains valid. A later rotation/deletion
            # can retry cleanup using the stale descriptors from the first pointer.
            pass
        # Legacy single-entry values use the logical name. The active pointer has
        # a distinct username, so the old secret can now be removed safely.
        self._raw_delete(name)

    def get(self, name: str) -> str | None:
        pointer_raw = self._raw_get(self._pointer_name(name))
        if pointer_raw is not None:
            pointer = self._parse_pointer(pointer_raw)
            if pointer is None:
                raise CredentialIntegrityError("Credential active pointer is malformed")
            return self._read_generation(name, self._descriptor(pointer))
        return self._raw_get(name)  # Legacy single-entry credential.

    def delete(self, name: str) -> None:
        pointer_name = self._pointer_name(name)
        pointer_raw = self._raw_get(pointer_name)
        if pointer_raw is not None:
            pointer = self._parse_pointer(pointer_raw)
            descriptors = [self._descriptor(pointer), *pointer.get("stale", [])] if pointer else []
            # Remove the public pointer first so no caller can read a partially deleted set.
            self._raw_delete(pointer_name)
            for descriptor in descriptors:
                self._delete_generation(name, descriptor)
        self._raw_delete(name)

    def _write_generation(self, name: str, raw: bytes) -> dict[str, Any]:
        version = uuid.uuid4().hex
        encoded = base64.b64encode(raw).decode("ascii")
        chunks = [encoded[i:i + CHUNK_CHARACTER_LIMIT] for i in range(0, len(encoded), CHUNK_CHARACTER_LIMIT)] or [""]
        descriptor = {"version": version, "chunks": len(chunks)}
        written: list[str] = []
        try:
            for index, chunk in enumerate(chunks):
                username = self._chunk_name(name, version, index)
                self._raw_set(username, chunk)
                written.append(username)
                if self._raw_get(username) != chunk:
                    raise CredentialIntegrityError("Credential chunk verification failed")
            manifest = self._compact({
                "format": MANIFEST_MARKER,
                "version": version,
                "chunks": len(chunks),
                "utf8_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
            manifest_name = self._manifest_name(name, version)
            self._raw_set(manifest_name, manifest)
            written.append(manifest_name)
            if self._raw_get(manifest_name) != manifest:
                raise CredentialIntegrityError("Credential manifest verification failed")
            # Full reconstruction is checked before the active pointer changes.
            decoded = self._read_generation(name, descriptor)
            if not secrets.compare_digest(decoded.encode("utf-8"), raw):
                raise CredentialIntegrityError("Credential generation verification failed")
            return descriptor
        except Exception as exc:
            for username in reversed(written):
                try:
                    self._raw_delete(username)
                except CredentialStoreError:
                    pass
            if isinstance(exc, CredentialStoreError):
                raise
            raise CredentialStoreError("Credential Manager rejected a secret fragment") from None

    def _read_generation(self, name: str, descriptor: dict[str, Any]) -> str:
        version, expected_chunks = descriptor.get("version"), descriptor.get("chunks")
        if not isinstance(version, str) or not isinstance(expected_chunks, int) or not 0 < expected_chunks <= 2000:
            raise CredentialIntegrityError("Credential pointer is invalid")
        manifest_raw = self._raw_get(self._manifest_name(name, version))
        if manifest_raw is None:
            raise CredentialIntegrityError("Credential manifest is missing")
        try:
            manifest = json.loads(manifest_raw)
        except json.JSONDecodeError as exc:
            raise CredentialIntegrityError("Credential manifest is malformed") from exc
        if (
            manifest.get("format") != MANIFEST_MARKER
            or manifest.get("version") != version
            or manifest.get("chunks") != expected_chunks
            or not isinstance(manifest.get("utf8_bytes"), int)
            or not isinstance(manifest.get("sha256"), str)
        ):
            raise CredentialIntegrityError("Credential manifest does not match its pointer")
        encoded_parts: list[str] = []
        for index in range(expected_chunks):
            chunk = self._raw_get(self._chunk_name(name, version, index))
            if chunk is None or windows_blob_size(chunk) > CRED_MAX_CREDENTIAL_BLOB_SIZE:
                raise CredentialIntegrityError("Credential chunk is missing or oversized")
            encoded_parts.append(chunk)
        try:
            raw = base64.b64decode("".join(encoded_parts), validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise CredentialIntegrityError("Credential chunk encoding is invalid") from exc
        if len(raw) != manifest["utf8_bytes"] or not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), manifest["sha256"]):
            raise CredentialIntegrityError("Credential integrity check failed")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CredentialIntegrityError("Credential text encoding is invalid") from exc

    def _delete_generation(self, name: str, descriptor: dict[str, Any]) -> bool:
        version, chunks = descriptor.get("version"), descriptor.get("chunks")
        if not isinstance(version, str) or not isinstance(chunks, int) or not 0 < chunks <= 2000:
            return False
        success = True
        for index in range(chunks):
            try:
                self._raw_delete(self._chunk_name(name, version, index))
            except CredentialStoreError:
                success = False
        try:
            self._raw_delete(self._manifest_name(name, version))
        except CredentialStoreError:
            success = False
        return success

    def _restore_pointer(self, name: str, previous_raw: str | None) -> None:
        try:
            if previous_raw is None:
                self._raw_delete(name)
            else:
                self._raw_set(name, previous_raw)
        except Exception as exc:
            raise CredentialStoreError("Credential rollback failed; manual credential cleanup is required") from None

    @staticmethod
    def _descriptor(pointer: dict[str, Any]) -> dict[str, Any]:
        return {"version": pointer.get("active") or pointer.get("version"), "chunks": pointer.get("chunks")}

    @staticmethod
    def _parse_pointer(raw: str | None) -> dict[str, Any] | None:
        if raw is None or not raw.startswith("{"):
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) and value.get("format") == POINTER_MARKER else None

    @staticmethod
    def _compact(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _chunk_name(name: str, version: str, index: int) -> str:
        return f"{name}::chunked::{version}::{index:06d}"

    @staticmethod
    def _manifest_name(name: str, version: str) -> str:
        return f"{name}::chunked::{version}::manifest"

    @staticmethod
    def _pointer_name(name: str) -> str:
        return f"{name}::chunked::active"

    def _raw_get(self, username: str) -> str | None:
        try:
            return self.backend.get_password(self.service, username)
        except Exception as exc:
            raise CredentialStoreError("Credential Manager read failed") from None

    def _raw_set(self, username: str, value: str) -> None:
        if windows_blob_size(value) > CRED_MAX_CREDENTIAL_BLOB_SIZE:
            raise CredentialStoreError("Credential fragment exceeds the Windows blob limit")
        try:
            self.backend.set_password(self.service, username, value)
        except Exception as exc:
            raise CredentialStoreError("Credential Manager write failed") from None

    def _raw_delete(self, username: str) -> None:
        try:
            self.backend.delete_password(self.service, username)
        except PasswordDeleteError:
            pass
        except Exception as exc:
            raise CredentialStoreError("Credential Manager delete failed") from None

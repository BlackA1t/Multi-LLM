import json

import pytest
from keyring.errors import PasswordDeleteError

from multi_llm.errors import CredentialIntegrityError, CredentialStoreError
from multi_llm.secrets import (
    CHUNK_CHARACTER_LIMIT,
    CRED_MAX_CREDENTIAL_BLOB_SIZE,
    CredentialStore,
    windows_blob_size,
)


class MemoryBackend:
    def __init__(self):
        self.values = {}
        self.fail_username = None
        self.error_secret = None

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        if username == self.fail_username:
            raise OSError(f"synthetic backend failure {self.error_secret or ''}")
        self.values[(service, username)] = password

    def delete_password(self, service, username):
        try:
            del self.values[(service, username)]
        except KeyError:
            raise PasswordDeleteError(username)


def test_short_secret_round_trip_uses_compatible_get_and_set():
    backend = MemoryBackend()
    store = CredentialStore(backend)
    store.set("short", "synthetic-secret")
    assert store.get("short") == "synthetic-secret"
    info = store.size_info("synthetic-secret")
    assert info.chunk_count == 1
    assert info.direct_windows_blob_bytes == windows_blob_size("synthetic-secret")


def test_long_secret_is_chunked_below_windows_blob_limit():
    backend = MemoryBackend()
    store = CredentialStore(backend)
    value = "synthetic-" + "x" * 12_000
    assert windows_blob_size(value) > CRED_MAX_CREDENTIAL_BLOB_SIZE
    store.set_atomic("oauth", value)
    assert store.get("oauth") == value
    pointer = json.loads(backend.get_password(store.service, store._pointer_name("oauth")))
    assert pointer["chunks"] > 1
    for index in range(pointer["chunks"]):
        chunk = backend.get_password(store.service, store._chunk_name("oauth", pointer["active"], index))
        assert len(chunk) <= CHUNK_CHARACTER_LIMIT
        assert windows_blob_size(chunk) < CRED_MAX_CREDENTIAL_BLOB_SIZE


def test_large_rotation_removes_old_generation_after_switch():
    backend = MemoryBackend()
    store = CredentialStore(backend)
    first, second = "A" * 9_000, "B" * 11_000
    store.set_atomic("oauth", first)
    old_pointer = json.loads(backend.get_password(store.service, store._pointer_name("oauth")))
    store.set_atomic("oauth", second)
    assert store.get("oauth") == second
    for index in range(old_pointer["chunks"]):
        assert backend.get_password(store.service, store._chunk_name("oauth", old_pointer["active"], index)) is None
    assert backend.get_password(store.service, store._manifest_name("oauth", old_pointer["active"])) is None


def test_failed_new_generation_keeps_previous_active_and_hides_secret():
    backend = MemoryBackend()
    store = CredentialStore(backend)
    previous, replacement = "P" * 8_000, "sensitive-replacement-" + "R" * 9_000
    store.set_atomic("oauth", previous)
    backend.fail_username = store._pointer_name("oauth")
    backend.error_secret = replacement
    with pytest.raises(CredentialStoreError) as raised:
        store.set_atomic("oauth", replacement)
    assert replacement not in str(raised.value)
    assert store.get("oauth") == previous


def test_tampered_chunk_fails_integrity_check():
    backend = MemoryBackend()
    store = CredentialStore(backend)
    store.set_atomic("oauth", "Z" * 8_000)
    pointer = json.loads(backend.get_password(store.service, store._pointer_name("oauth")))
    chunk_name = store._chunk_name("oauth", pointer["active"], 0)
    backend.values[(store.service, chunk_name)] = "A" * len(backend.values[(store.service, chunk_name)])
    with pytest.raises(CredentialIntegrityError, match="integrity"):
        store.get("oauth")


def test_delete_cleans_active_generation_and_pointer():
    backend = MemoryBackend()
    store = CredentialStore(backend)
    store.set_atomic("oauth", "D" * 8_000)
    store.delete("oauth")
    assert store.get("oauth") is None
    assert backend.values == {}


def test_legacy_single_entry_remains_readable_and_deletable():
    backend = MemoryBackend()
    backend.set_password("multi-llm-p04", "legacy", "legacy-secret")
    store = CredentialStore(backend)
    assert store.get("legacy") == "legacy-secret"
    store.delete("legacy")
    assert store.get("legacy") is None

import json
import sys
import uuid

import pytest
from keyring.errors import PasswordDeleteError

from multi_llm.errors import CredentialIntegrityError, CredentialStoreError
from multi_llm.secrets import CredentialStore, windows_blob_size

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager integration")


class FailingPointerBackend:
    def __init__(self, delegate, pointer_name):
        self.delegate = delegate
        self.pointer_name = pointer_name
        self.enabled = False

    def get_password(self, service, username):
        return self.delegate.get_password(service, username)

    def set_password(self, service, username, password):
        if self.enabled and username == self.pointer_name:
            raise OSError("synthetic pointer failure")
        return self.delegate.set_password(service, username, password)

    def delete_password(self, service, username):
        return self.delegate.delete_password(service, username)


def _safe_delete(backend, service, username):
    try:
        backend.delete_password(service, username)
    except PasswordDeleteError:
        pass


def test_real_windows_backend_limit_chunking_rotation_integrity_and_cleanup():
    import keyring

    backend = keyring.get_keyring()
    assert backend.__class__.__module__ == "keyring.backends.Windows"
    service = f"multi-llm-p0421-test-{uuid.uuid4().hex}"
    direct_short, direct_long = "direct-short", "direct-long"
    store_name = "oauth-test"
    store = CredentialStore(backend, service=service)
    final_pointer = None
    try:
        backend.set_password(service, direct_short, "S" * 1000)
        assert backend.get_password(service, direct_short) == "S" * 1000

        oversized = "L" * 1400
        assert windows_blob_size(oversized) > 2560
        with pytest.raises(Exception) as raised:
            backend.set_password(service, direct_long, oversized)
        winerror = getattr(raised.value, "winerror", raised.value.args[0] if raised.value.args else None)
        assert winerror == 1783

        short = "short-artificial-secret"
        store.set_atomic(store_name, short)
        assert store.get(store_name) == short

        first = "A" * 12_000
        second = "B" * 15_000
        store.set_atomic(store_name, first)
        assert store.get(store_name) == first
        store.set_atomic(store_name, second)
        assert store.get(store_name) == second

        pointer = json.loads(backend.get_password(service, store._pointer_name(store_name)))
        chunk_name = store._chunk_name(store_name, pointer["active"], 0)
        original_chunk = backend.get_password(service, chunk_name)
        backend.set_password(service, chunk_name, "A" * len(original_chunk))
        with pytest.raises(CredentialIntegrityError):
            store.get(store_name)
        backend.set_password(service, chunk_name, original_chunk)
        assert store.get(store_name) == second

        wrapper = FailingPointerBackend(backend, store._pointer_name(store_name))
        failing_store = CredentialStore(wrapper, service=service)
        wrapper.enabled = True
        with pytest.raises(CredentialStoreError):
            failing_store.set_atomic(store_name, "C" * 13_000)
        assert store.get(store_name) == second
        final_pointer = json.loads(backend.get_password(service, store._pointer_name(store_name)))
    finally:
        store.delete(store_name)
        _safe_delete(backend, service, direct_short)
        _safe_delete(backend, service, direct_long)
    assert store.get(store_name) is None
    assert backend.get_password(service, store._pointer_name(store_name)) is None
    if final_pointer:
        for index in range(final_pointer["chunks"]):
            assert backend.get_password(service, store._chunk_name(store_name, final_pointer["active"], index)) is None
        assert backend.get_password(service, store._manifest_name(store_name, final_pointer["active"])) is None

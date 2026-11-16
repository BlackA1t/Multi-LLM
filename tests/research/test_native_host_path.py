from pathlib import Path

import pytest

from multi_llm.research.native_host import _database_path
from multi_llm.research.service import ResearchSessionService


def test_database_path_uses_stable_local_app_data_across_working_directories(tmp_path, monkeypatch):
    local_app_data = tmp_path / "LocalAppData"
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    monkeypatch.delenv("MULTI_LLM_RESEARCH_DB", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data.resolve()))

    monkeypatch.chdir(first_cwd)
    first = _database_path()
    monkeypatch.chdir(second_cwd)
    second = _database_path()

    assert first == second == local_app_data.resolve() / "Multi-LLM" / "research-v2.db"
    assert first.parent.is_dir()


def test_absolute_override_is_used_and_parent_is_created(tmp_path, monkeypatch):
    configured = (tmp_path / "explicit" / "research.db").resolve()
    monkeypatch.setenv("MULTI_LLM_RESEARCH_DB", str(configured))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert _database_path() == configured
    assert configured.parent.is_dir()


@pytest.mark.parametrize("configured", ["", "   ", "relative/research.db"])
def test_invalid_override_is_rejected_without_exposing_a_path(configured, monkeypatch):
    monkeypatch.setenv("MULTI_LLM_RESEARCH_DB", configured)
    with pytest.raises(RuntimeError) as raised:
        _database_path()
    assert "relative/research.db" not in str(raised.value)


def test_missing_or_invalid_local_app_data_is_rejected(monkeypatch):
    monkeypatch.delenv("MULTI_LLM_RESEARCH_DB", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(RuntimeError, match="LOCALAPPDATA is unavailable"):
        _database_path()

    monkeypatch.setenv("LOCALAPPDATA", Path.cwd().anchor)
    with pytest.raises(RuntimeError, match="LOCALAPPDATA is invalid"):
        _database_path()


def test_reopening_host_database_restores_the_same_session(tmp_path, monkeypatch):
    path = (tmp_path / "persistent" / "research.db").resolve()
    monkeypatch.setenv("MULTI_LLM_RESEARCH_DB", str(path))
    first = ResearchSessionService(_database_path())
    created = first.create(question="Синтетический вопрос", idempotency_key="create-once")

    second = ResearchSessionService(_database_path())
    assert second.get(created.session_id).question == "Синтетический вопрос"

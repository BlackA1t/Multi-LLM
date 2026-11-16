from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import sys


def _load_backup_module():
    path = Path(__file__).parents[2] / "scripts" / "backup_research_db.py"
    spec = importlib.util.spec_from_file_location("backup_research_db", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backup_defaults_to_research_v2_and_preserves_source(tmp_path, monkeypatch, capsys):
    local = tmp_path / "LocalAppData"
    source = local / "Multi-LLM" / "research-v2.db"
    source.parent.mkdir(parents=True)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES ('synthetic')")
    legacy = source.with_name("research.db")
    with sqlite3.connect(legacy) as connection:
        connection.execute("CREATE TABLE legacy_only(value TEXT NOT NULL)")

    destination = tmp_path / "backup.db"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(sys, "argv", ["backup_research_db.py", "--destination", str(destination)])
    assert _load_backup_module().main() == 0
    assert destination.is_file()
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == "synthetic"
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'legacy_only'"
        ).fetchone()[0] == 0
    assert source.is_file() and legacy.is_file()
    assert str(destination) in capsys.readouterr().out

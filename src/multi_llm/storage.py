from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import RunRecord
from .redaction import redact, sanitize_json


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, prompt TEXT NOT NULL,
                    participants_json TEXT NOT NULL, status TEXT NOT NULL, duration_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS results (
                    run_id TEXT NOT NULL, provider_id TEXT NOT NULL, model_id TEXT NOT NULL, actual_model_id TEXT,
                    family_id TEXT NOT NULL, developer TEXT NOT NULL, auth_method TEXT NOT NULL,
                    status TEXT NOT NULL, response_text TEXT, error TEXT, duration_ms INTEGER NOT NULL,
                    usage_json TEXT, raw_response_json TEXT, rate_limits_json TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, event_type TEXT NOT NULL,
                    message TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(results)")}
            if "actual_model_id" not in columns:
                db.execute("ALTER TABLE results ADD COLUMN actual_model_id TEXT")

    def save_run(self, run: RunRecord) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)", (
                run.run_id, run.created_at.isoformat(), run.prompt,
                json.dumps(run.participants), run.status.value, run.duration_ms,
            ))
            for result in run.results:
                db.execute("""INSERT INTO results(
                    run_id,provider_id,model_id,actual_model_id,family_id,developer,auth_method,status,response_text,
                    error,duration_ms,usage_json,raw_response_json,rate_limits_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                    run.run_id, result.provider_id, result.requested_model_id, result.actual_model_id, result.model_family_id,
                    result.model_developer, result.auth_method, result.status.value, result.response_text,
                    redact(result.error) if result.error else None, result.duration_ms,
                    result.usage.model_dump_json() if result.usage else None,
                    json.dumps(sanitize_json(result.raw_response), ensure_ascii=False) if result.raw_response else None,
                    json.dumps(sanitize_json(result.rate_limits)),
                ))
                db.execute("INSERT INTO events(run_id,event_type,message) VALUES (?,?,?)", (
                    run.run_id, "provider_result", redact(f"{result.provider_id}:{result.status.value}"),
                ))

    def export(self, run_id: str, output: Path) -> None:
        with self.connect() as db:
            run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                raise KeyError(f"Unknown run_id: {run_id}")
            results = [dict(row) for row in db.execute("SELECT * FROM results WHERE run_id=?", (run_id,))]
            events = [dict(row) for row in db.execute("SELECT * FROM events WHERE run_id=?", (run_id,))]
        payload = {"run": dict(run), "results": results, "events": events}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

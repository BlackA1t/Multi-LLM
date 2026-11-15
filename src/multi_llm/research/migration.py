from __future__ import annotations

import sqlite3


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, """
        CREATE TABLE IF NOT EXISTS research_sessions (
            session_id TEXT PRIMARY KEY,
            question TEXT NOT NULL,
            title TEXT,
            phase TEXT NOT NULL,
            status TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0,
            gap_reason TEXT,
            create_idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS session_participants (
            participant_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT,
            provider_id TEXT NOT NULL,
            role TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(session_id, ordinal)
        );
        CREATE TABLE IF NOT EXISTS research_rounds (
            round_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT,
            phase TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(session_id, ordinal)
        );
        CREATE TABLE IF NOT EXISTS research_tasks (
            task_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT,
            round_id TEXT NOT NULL REFERENCES research_rounds(round_id) ON DELETE RESTRICT,
            phase TEXT NOT NULL,
            kind TEXT NOT NULL,
            provider_id TEXT,
            role TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            status TEXT NOT NULL,
            gap_reason TEXT,
            revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(round_id, ordinal)
        );
        CREATE TABLE IF NOT EXISTS prompt_artifacts (
            prompt_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES research_tasks(task_id) ON DELETE RESTRICT,
            version INTEGER NOT NULL,
            prompt_text TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(task_id, version)
        );
        CREATE TABLE IF NOT EXISTS research_results (
            result_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES research_tasks(task_id) ON DELETE RESTRICT,
            source_type TEXT NOT NULL,
            reported_model TEXT,
            current_revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS response_revisions (
            revision_id TEXT PRIMARY KEY,
            result_id TEXT NOT NULL REFERENCES research_results(result_id) ON DELETE RESTRICT,
            version INTEGER NOT NULL,
            response_text TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(result_id, version)
        );
        CREATE TABLE IF NOT EXISTS comparison_items (
            comparison_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fact_check_items (
            fact_check_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS arbitration_records (
            arbitration_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS session_events (
            event_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT,
            event_type TEXT NOT NULL,
            entity_id TEXT,
            revision INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS idempotency_operations (
            operation TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT,
            request_hash TEXT NOT NULL,
            result_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(operation, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS ix_sessions_updated ON research_sessions(updated_at DESC, session_id);
        CREATE INDEX IF NOT EXISTS ix_tasks_session_phase ON research_tasks(session_id, phase, ordinal);
        CREATE INDEX IF NOT EXISTS ix_events_session ON session_events(session_id, created_at);
    """),
    (2, """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_research_results_task
            ON research_results(task_id);
        CREATE TABLE IF NOT EXISTS response_uploads (
            upload_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT,
            task_id TEXT NOT NULL REFERENCES research_tasks(task_id) ON DELETE RESTRICT,
            mode TEXT NOT NULL CHECK(mode IN ('create', 'revise')),
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            expected_revision INTEGER NOT NULL,
            expected_result_version INTEGER,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            UNIQUE(mode, idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS response_upload_parts (
            upload_id TEXT NOT NULL REFERENCES response_uploads(upload_id) ON DELETE CASCADE,
            part_index INTEGER NOT NULL,
            chunk_blob BLOB NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            PRIMARY KEY(upload_id, part_index)
        );
        CREATE TABLE IF NOT EXISTS operation_receipts (
            operation TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT,
            task_id TEXT NOT NULL REFERENCES research_tasks(task_id) ON DELETE RESTRICT,
            request_hash TEXT NOT NULL,
            receipt_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(operation, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS ix_response_uploads_expiry
            ON response_uploads(expires_at);
        CREATE INDEX IF NOT EXISTS ix_response_revisions_result
            ON response_revisions(result_id, version);
    """),
    (3, """
        ALTER TABLE research_sessions ADD COLUMN perplexity_prompt TEXT;
        ALTER TABLE research_sessions ADD COLUMN intake_version INTEGER NOT NULL DEFAULT 1;
    """),
)


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + sql
                + f"\nINSERT OR IGNORE INTO schema_migrations(version) VALUES ({version});\nCOMMIT;"
            )
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

from __future__ import annotations

import sqlite3

SCHEMA_NAME = "multi-llm-research-v2"
SCHEMA_VERSION = 1

BASE_SCHEMA = """
CREATE TABLE schema_identity (name TEXT PRIMARY KEY, version INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
INSERT INTO schema_identity(name, version) VALUES ('multi-llm-research-v2', 1);
CREATE TABLE research_sessions (session_id TEXT PRIMARY KEY, question TEXT NOT NULL, title TEXT, phase TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, gap_reason TEXT, create_idempotency_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, perplexity_prompt TEXT, intake_version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE session_participants (participant_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT, provider_id TEXT NOT NULL, role TEXT NOT NULL, ordinal INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE(session_id, ordinal));
CREATE TABLE research_rounds (round_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT, phase TEXT NOT NULL, ordinal INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(session_id, ordinal));
CREATE TABLE research_tasks (task_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT, round_id TEXT NOT NULL REFERENCES research_rounds(round_id) ON DELETE RESTRICT, phase TEXT NOT NULL, kind TEXT NOT NULL, provider_id TEXT, role TEXT NOT NULL, ordinal INTEGER NOT NULL, status TEXT NOT NULL, gap_reason TEXT, revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(round_id, ordinal));
CREATE TABLE prompt_artifacts (prompt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES research_tasks(task_id) ON DELETE RESTRICT, version INTEGER NOT NULL, prompt_text TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(task_id, version));
CREATE TABLE research_results (result_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES research_tasks(task_id) ON DELETE RESTRICT, source_type TEXT NOT NULL, reported_model TEXT, current_revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE response_revisions (revision_id TEXT PRIMARY KEY, result_id TEXT NOT NULL REFERENCES research_results(result_id) ON DELETE RESTRICT, version INTEGER NOT NULL, response_text TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(result_id, version));
CREATE TABLE session_events (event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT, event_type TEXT NOT NULL, entity_id TEXT, revision INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE idempotency_operations (operation TEXT NOT NULL, idempotency_key TEXT NOT NULL, session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT, request_hash TEXT NOT NULL, result_revision INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(operation, idempotency_key));
CREATE TABLE response_uploads (upload_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT, task_id TEXT NOT NULL REFERENCES research_tasks(task_id) ON DELETE RESTRICT, mode TEXT NOT NULL CHECK(mode IN ('create','revise')), size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, expected_revision INTEGER NOT NULL, expected_result_version INTEGER, idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, UNIQUE(mode, idempotency_key));
CREATE TABLE response_upload_parts (upload_id TEXT NOT NULL REFERENCES response_uploads(upload_id) ON DELETE CASCADE, part_index INTEGER NOT NULL, chunk_blob BLOB NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(upload_id, part_index));
CREATE TABLE operation_receipts (operation TEXT NOT NULL, idempotency_key TEXT NOT NULL, session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT, task_id TEXT NOT NULL REFERENCES research_tasks(task_id) ON DELETE RESTRICT, request_hash TEXT NOT NULL, receipt_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(operation, idempotency_key));
CREATE TABLE s1_packages (package_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT, task_id TEXT NOT NULL UNIQUE REFERENCES research_tasks(task_id) ON DELETE RESTRICT, template_version INTEGER NOT NULL, input_fingerprint TEXT NOT NULL, package_sha256 TEXT NOT NULL, total_bytes INTEGER NOT NULL, part_count INTEGER NOT NULL, r0_complete INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE s1_package_inputs (package_id TEXT NOT NULL REFERENCES s1_packages(package_id) ON DELETE RESTRICT, ordinal INTEGER NOT NULL, provider_id TEXT NOT NULL, reported_model TEXT, task_id TEXT NOT NULL REFERENCES research_tasks(task_id) ON DELETE RESTRICT, status TEXT NOT NULL, result_id TEXT REFERENCES research_results(result_id) ON DELETE RESTRICT, result_version INTEGER, sha256 TEXT, gap_reason TEXT, PRIMARY KEY(package_id, ordinal));
CREATE TABLE s1_package_parts (package_id TEXT NOT NULL REFERENCES s1_packages(package_id) ON DELETE RESTRICT, part_index INTEGER NOT NULL, prompt_id TEXT NOT NULL UNIQUE REFERENCES prompt_artifacts(prompt_id) ON DELETE RESTRICT, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(package_id, part_index));
CREATE TABLE s1_result_links (result_id TEXT PRIMARY KEY REFERENCES research_results(result_id) ON DELETE RESTRICT, package_id TEXT NOT NULL REFERENCES s1_packages(package_id) ON DELETE RESTRICT);
CREATE TABLE reviewer_packages (package_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT, task_id TEXT NOT NULL UNIQUE REFERENCES research_tasks(task_id) ON DELETE RESTRICT, provider_id TEXT NOT NULL, template_version INTEGER NOT NULL, s1_result_id TEXT NOT NULL REFERENCES research_results(result_id) ON DELETE RESTRICT, s1_result_version INTEGER NOT NULL, s1_package_id TEXT NOT NULL REFERENCES s1_packages(package_id) ON DELETE RESTRICT, s1_package_fingerprint TEXT NOT NULL, input_fingerprint TEXT NOT NULL, package_sha256 TEXT NOT NULL, total_bytes INTEGER NOT NULL, part_count INTEGER NOT NULL, r0_complete INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE reviewer_package_inputs (package_id TEXT NOT NULL REFERENCES reviewer_packages(package_id) ON DELETE RESTRICT, ordinal INTEGER NOT NULL, provider_id TEXT NOT NULL, reported_model TEXT, task_id TEXT NOT NULL REFERENCES research_tasks(task_id) ON DELETE RESTRICT, status TEXT NOT NULL, result_id TEXT REFERENCES research_results(result_id) ON DELETE RESTRICT, result_version INTEGER, sha256 TEXT, gap_reason TEXT, PRIMARY KEY(package_id, ordinal));
CREATE TABLE reviewer_package_parts (package_id TEXT NOT NULL REFERENCES reviewer_packages(package_id) ON DELETE RESTRICT, part_index INTEGER NOT NULL, prompt_id TEXT NOT NULL UNIQUE REFERENCES prompt_artifacts(prompt_id) ON DELETE RESTRICT, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(package_id, part_index));
CREATE TABLE reviewer_result_links (result_id TEXT PRIMARY KEY REFERENCES research_results(result_id) ON DELETE RESTRICT, package_id TEXT NOT NULL REFERENCES reviewer_packages(package_id) ON DELETE RESTRICT);
CREATE TABLE s2_packages (package_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE RESTRICT, task_id TEXT NOT NULL UNIQUE REFERENCES research_tasks(task_id) ON DELETE RESTRICT, template_version INTEGER NOT NULL, s1_result_id TEXT NOT NULL REFERENCES research_results(result_id) ON DELETE RESTRICT, s1_result_version INTEGER NOT NULL, s1_package_id TEXT NOT NULL REFERENCES s1_packages(package_id) ON DELETE RESTRICT, input_fingerprint TEXT NOT NULL, package_sha256 TEXT NOT NULL, total_bytes INTEGER NOT NULL, part_count INTEGER NOT NULL, r0_complete INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE s2_package_reviews (package_id TEXT NOT NULL REFERENCES s2_packages(package_id) ON DELETE RESTRICT, ordinal INTEGER NOT NULL, provider_id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES research_tasks(task_id) ON DELETE RESTRICT, result_id TEXT NOT NULL REFERENCES research_results(result_id) ON DELETE RESTRICT, result_version INTEGER NOT NULL, sha256 TEXT NOT NULL, reviewer_package_id TEXT NOT NULL REFERENCES reviewer_packages(package_id) ON DELETE RESTRICT, PRIMARY KEY(package_id, ordinal));
CREATE TABLE s2_package_inputs (package_id TEXT NOT NULL REFERENCES s2_packages(package_id) ON DELETE RESTRICT, ordinal INTEGER NOT NULL, provider_id TEXT NOT NULL, reported_model TEXT, task_id TEXT NOT NULL REFERENCES research_tasks(task_id) ON DELETE RESTRICT, status TEXT NOT NULL, result_id TEXT REFERENCES research_results(result_id) ON DELETE RESTRICT, result_version INTEGER, sha256 TEXT, gap_reason TEXT, PRIMARY KEY(package_id, ordinal));
CREATE TABLE s2_package_parts (package_id TEXT NOT NULL REFERENCES s2_packages(package_id) ON DELETE RESTRICT, part_index INTEGER NOT NULL, prompt_id TEXT NOT NULL UNIQUE REFERENCES prompt_artifacts(prompt_id) ON DELETE RESTRICT, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(package_id, part_index));
CREATE TABLE s2_result_links (result_id TEXT PRIMARY KEY REFERENCES research_results(result_id) ON DELETE RESTRICT, package_id TEXT NOT NULL REFERENCES s2_packages(package_id) ON DELETE RESTRICT);
CREATE INDEX ix_sessions_updated ON research_sessions(updated_at DESC, session_id);
CREATE INDEX ix_tasks_session_phase ON research_tasks(session_id, phase, ordinal);
CREATE INDEX ix_events_session ON session_events(session_id, created_at);
CREATE INDEX ix_response_uploads_expiry ON response_uploads(expires_at);
CREATE INDEX ix_response_revisions_result ON response_revisions(result_id, version);
CREATE INDEX ix_s1_packages_session ON s1_packages(session_id, created_at);
CREATE INDEX ix_reviewer_packages_session_s1 ON reviewer_packages(session_id, s1_result_id, s1_result_version, created_at);
CREATE INDEX ix_s2_packages_session ON s2_packages(session_id, created_at);
"""

# Baseline creation may be attempted concurrently by multiple Native Host
# processes.  SQLite serializes the write transaction; idempotent DDL then
# lets the process that acquired the lock second verify the same identity.
BASE_SCHEMA = (BASE_SCHEMA
               .replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ")
               .replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ")
               .replace("INSERT INTO schema_identity", "INSERT OR IGNORE INTO schema_identity"))

def migrate(connection: sqlite3.Connection) -> None:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not tables:
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + BASE_SCHEMA + "\nCOMMIT;")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return
    if "schema_identity" not in tables:
        raise RuntimeError("Legacy research database is unsupported; keep it as an archive and use research-v2.db")
    row = connection.execute("SELECT name, version FROM schema_identity").fetchone()
    if not row or tuple(row) != (SCHEMA_NAME, SCHEMA_VERSION):
        raise RuntimeError("Unsupported research database schema")

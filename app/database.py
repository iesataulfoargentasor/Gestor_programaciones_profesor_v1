import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('collaborator','reviewer','admin')),
    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, center TEXT NOT NULL, module TEXT NOT NULL, academic_year TEXT NOT NULL,
    title TEXT NOT NULL, current_version_id TEXT, created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL, UNIQUE(center, module, academic_year)
);
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY, original_name TEXT NOT NULL, storage_name TEXT NOT NULL UNIQUE,
    extension TEXT NOT NULL, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id), version_no INTEGER NOT NULL,
    file_id TEXT NOT NULL UNIQUE REFERENCES files(id), status TEXT NOT NULL, declared_status TEXT NOT NULL,
    relation_type TEXT, relation_reason TEXT, declared_version TEXT NOT NULL DEFAULT '',
    uploaded_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
    approved_by TEXT REFERENCES users(id), approved_at TEXT, verified_by TEXT REFERENCES users(id), verified_at TEXT,
    published_by TEXT REFERENCES users(id), published_at TEXT, replaces_version_id TEXT REFERENCES versions(id),
    UNIQUE(document_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_versions_status ON versions(status);
CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);
CREATE TABLE IF NOT EXISTS extractions (
    id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id), extractor TEXT NOT NULL,
    result TEXT NOT NULL CHECK(result IN ('processing','success','error')),
    error TEXT NOT NULL DEFAULT '', text_normalized TEXT NOT NULL DEFAULT '', hash_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_extractions_version ON extractions(version_id, created_at);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, extraction_id TEXT NOT NULL REFERENCES extractions(id) ON DELETE CASCADE,
    locator TEXT NOT NULL, text TEXT NOT NULL, content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id), reviewer_id TEXT NOT NULL REFERENCES users(id),
    decision TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provenance (
    id TEXT PRIMARY KEY, matched_version_id TEXT NOT NULL REFERENCES versions(id), submitted_by TEXT NOT NULL REFERENCES users(id),
    submitted_name TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id), action TEXT NOT NULL, entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
"""


def get_db(path):
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=10000")
    return db


def initialize_database(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = get_db(path)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
    finally:
        db.close()


@contextmanager
def transaction(path, immediate=False):
    db = get_db(path)
    try:
        db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

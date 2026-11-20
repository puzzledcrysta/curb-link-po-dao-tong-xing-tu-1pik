import sqlite3
from contextlib import contextmanager
from pathlib import Path
from app import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, source BLOB NOT NULL, book BLOB NOT NULL,
  result TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
  run_id TEXT NOT NULL REFERENCES runs(id), version INTEGER NOT NULL,
  decision TEXT NOT NULL, note TEXT NOT NULL, changed TEXT NOT NULL,
  PRIMARY KEY(run_id,version)
);
CREATE TABLE IF NOT EXISTS settlement_batches (
  batch_id TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL,
  book_sha256 TEXT NOT NULL, source BLOB NOT NULL, book BLOB NOT NULL,
  created TEXT NOT NULL, updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settlement_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,
  book_sha256 TEXT NOT NULL, kind TEXT NOT NULL, book BLOB NOT NULL,
  settled INTEGER NOT NULL, created TEXT NOT NULL,
  UNIQUE(batch_id, book_sha256)
);
CREATE TABLE IF NOT EXISTS settlements (
  batch_id TEXT NOT NULL, key TEXT NOT NULL, amount TEXT NOT NULL,
  reference TEXT NOT NULL, reference_line INTEGER NOT NULL,
  delta TEXT NOT NULL, code TEXT NOT NULL, source_line INTEGER NOT NULL,
  revision INTEGER, raw TEXT NOT NULL, proof TEXT NOT NULL,
  attempt_id INTEGER NOT NULL, settled TEXT NOT NULL,
  PRIMARY KEY(batch_id,key)
);
CREATE TABLE IF NOT EXISTS settlement_evidence (
  batch_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
  line INTEGER NOT NULL, attempt_id INTEGER NOT NULL
);
"""


@contextmanager
def connection():
    path = Path(settings.DB)
    path.parent.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(path,timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys = ON')
        db.executescript(SCHEMA)
        with db:
            yield db
    finally:
        db.close()

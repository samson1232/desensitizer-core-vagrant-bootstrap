"""The integration database: one SQLite file both layers read and write.

Layer A (aliasing) writes documents. Layer B (the Hermes agent) is handed a job that names
those documents and its work directory. This database is the contract between them: it
records what was aliased, what was asked of the agent, what came back, and what was
restored — never a value.

**No plaintext PII is stored here.** A row carries ids, paths, kinds, counts and hashes.
The values live in `state/vault.json` (0600) and nowhere else; `tests/test_app.py` asserts
this by scanning the database file for the planted values.

Why SQLite and not an ORM: one file, one writer, no migrations to run, and it is in the
standard library. The schema is small enough to read at a glance.
"""
import json
import os
import sqlite3
import time
import uuid

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    source_name  TEXT NOT NULL,
    format       TEXT NOT NULL,
    extractor    TEXT,
    aliased_path TEXT NOT NULL,
    private_path TEXT,
    kinds        TEXT NOT NULL DEFAULT '{}',
    chars        INTEGER NOT NULL DEFAULT 0,
    raw_chars    INTEGER NOT NULL DEFAULT 0,
    sha256       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'aliased',
    spans_count  INTEGER NOT NULL DEFAULT 0,
    fragments_count INTEGER NOT NULL DEFAULT 0,
    added_at     TEXT NOT NULL
);

-- One row per value found: how it was found, where, how big -- never what it was.
-- method: strict | loose | fragment.  restorable=0 is damage the extractor caused.
CREATE TABLE IF NOT EXISTS document_spans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    method     TEXT NOT NULL,
    start      INTEGER NOT NULL,
    end        INTEGER NOT NULL,
    chars      INTEGER NOT NULL,
    restorable INTEGER NOT NULL DEFAULT 1
);

-- Fragments are kept apart: they are the reason a document is needs_review.
CREATE TABLE IF NOT EXISTS document_fragments (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    kind   TEXT NOT NULL,
    start  INTEGER NOT NULL,
    chars  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL DEFAULT 'work',
    agent       TEXT,
    prompt      TEXT NOT NULL,
    doc_ids     TEXT NOT NULL DEFAULT '[]',
    work_dir    TEXT,
    status      TEXT NOT NULL DEFAULT 'queued',
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    exit_code   INTEGER,
    output      TEXT,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS restores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id        TEXT,
    source_path   TEXT NOT NULL,
    restored_path TEXT NOT NULL,
    tokens        INTEGER NOT NULL DEFAULT 0,
    format        TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     TEXT NOT NULL,
    kind   TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def connect(path):
    """One short-lived connection per call: WAL makes that cheap and it cannot leak."""
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    fresh = not os.path.exists(path)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        # Databases created by 0.2.0-rc predate the span columns; add them in place.
        for table, column, decl in (("documents", "extractor", "TEXT"),
                                    ("documents", "raw_chars", "INTEGER DEFAULT 0"),
                                    ("documents", "spans_count", "INTEGER DEFAULT 0"),
                                    ("documents", "fragments_count", "INTEGER DEFAULT 0")):
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.commit()
    finally:
        conn.close()
    if fresh:
        os.chmod(path, 0o600)
    return path


# ------------------------------------------------------------------ documents
def add_document(path, entry):
    conn = connect(path)
    try:
        conn.execute("INSERT OR REPLACE INTO documents "
                     "(id, source_name, format, extractor, aliased_path, private_path, kinds, chars, "
                     "raw_chars, sha256, status, spans_count, fragments_count, added_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (entry["id"], entry["source_name"], entry["format"], entry.get("extractor"),
                      entry["aliased_path"], entry.get("private_path"),
                      json.dumps(entry["kinds"], sort_keys=True),
                      entry.get("chars_out", 0), entry.get("raw_chars", 0),
                      entry["aliased_sha256"], entry.get("status", "aliased"),
                      len(entry.get("spans", [])), len(entry.get("fragments", [])),
                      entry["added"]))
        record_spans(path, entry, conn=conn)
        conn.execute("INSERT INTO events (at, kind, detail) VALUES (?,?,?)",
                     (now(), "document.added", entry["id"]))
        conn.commit()
    finally:
        conn.close()


def record_spans(path, entry, conn=None):
    """Write the per-value span record. Values are not included by construction."""
    own = conn is None
    conn = conn or connect(path)
    try:
        conn.execute("DELETE FROM document_spans WHERE doc_id=?", (entry["id"],))
        conn.execute("DELETE FROM document_fragments WHERE doc_id=?", (entry["id"],))
        for span in entry.get("spans", []):
            conn.execute("INSERT INTO document_spans (doc_id, kind, method, start, end, chars, restorable) "
                         "VALUES (?,?,?,?,?,?,?)",
                         (entry["id"], span["kind"], span.get("method", "strict"), span["start"],
                          span["end"], span.get("chars", span["end"] - span["start"]),
                          1 if span.get("restorable", True) else 0))
        for frag in entry.get("fragments", []):
            conn.execute("INSERT INTO document_fragments (doc_id, kind, start, chars) VALUES (?,?,?,?)",
                         (entry["id"], frag["kind"], frag["start"], frag["chars"]))
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def spans(path, doc_id):
    conn = connect(path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT kind, method, start, end, chars, restorable FROM document_spans "
            "WHERE doc_id=? ORDER BY start", (doc_id,)).fetchall()]
    finally:
        conn.close()


def fragments(path, doc_id):
    conn = connect(path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT kind, start, chars FROM document_fragments WHERE doc_id=? ORDER BY start",
            (doc_id,)).fetchall()]
    finally:
        conn.close()


def documents(path):
    conn = connect(path)
    try:
        rows = conn.execute("SELECT * FROM documents ORDER BY id").fetchall()
        return [{**dict(r), "kinds": json.loads(r["kinds"])} for r in rows]
    finally:
        conn.close()


def document(path, doc_id):
    conn = connect(path)
    try:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return {**dict(row), "kinds": json.loads(row["kinds"])} if row else None
    finally:
        conn.close()


# ----------------------------------------------------------------------- jobs
def create_job(path, prompt, agent, doc_ids, work_dir, kind="work"):
    job_id = f"job-{uuid.uuid4().hex[:10]}"
    conn = connect(path)
    try:
        conn.execute("INSERT INTO jobs (id, kind, agent, prompt, doc_ids, work_dir, status, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     (job_id, kind, agent, prompt, json.dumps(doc_ids), work_dir, "queued", now()))
        conn.execute("INSERT INTO events (at, kind, detail) VALUES (?,?,?)", (now(), "job.created", job_id))
        conn.commit()
    finally:
        conn.close()
    return job_id


def set_job(path, job_id, **fields):
    if not fields:
        return
    assignments = ", ".join(f"{key}=?" for key in fields)
    conn = connect(path)
    try:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id=?", (*fields.values(), job_id))
        conn.commit()
    finally:
        conn.close()


def job(path, job_id):
    conn = connect(path)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["doc_ids"] = json.loads(data["doc_ids"])
        return data
    finally:
        conn.close()


def jobs(path, limit=50):
    conn = connect(path)
    try:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            data = dict(r)
            data["doc_ids"] = json.loads(data["doc_ids"])
            out.append(data)
        return out
    finally:
        conn.close()


# ------------------------------------------------------------------- restores
def add_restore(path, doc_id, entry):
    conn = connect(path)
    try:
        conn.execute("INSERT INTO restores (doc_id, source_path, restored_path, tokens, format, sha256, at) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (doc_id, entry["source"], entry["restored"], entry["tokens"], entry["format"],
                      entry["restored_sha256"], entry["at"]))
        conn.execute("INSERT INTO events (at, kind, detail) VALUES (?,?,?)",
                     (now(), "document.restored", entry["restored"]))
        conn.commit()
    finally:
        conn.close()


def restores(path, limit=50):
    conn = connect(path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM restores ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    finally:
        conn.close()


def events(path, limit=100):
    conn = connect(path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    finally:
        conn.close()

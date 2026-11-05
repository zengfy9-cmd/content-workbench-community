"""SQLite storage. One file in the private data directory."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .config import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,            -- base64url credential id
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    rp_id TEXT NOT NULL,
    label TEXT,
    created_at REAL NOT NULL,
    last_used_at REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    user_agent TEXT
);
CREATE TABLE IF NOT EXISTS challenges (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    challenge BLOB NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,           -- note | audio | feishu_minutes | feishu_doc | paste
    source_ref TEXT,                -- minute token / doc url / file name
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ready',   -- ready | transcribing | failed
    error TEXT,
    created_at REAL NOT NULL,
    occurred_at REAL,
    UNIQUE(source, source_ref)
);
CREATE TABLE IF NOT EXISTS content_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER REFERENCES materials(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    angle TEXT,
    platforms TEXT,                 -- json list
    quotes TEXT,                    -- json list of the author's own words from the material
    notes TEXT,
    starred INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',     -- new | drafted | dropped
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER REFERENCES ideas(id) ON DELETE SET NULL,
    platform TEXT NOT NULL,         -- wechat | x
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '', -- 改稿备注, never published
    status TEXT NOT NULL DEFAULT 'draft',   -- draft | generating | published | failed
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reference_highlights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_id INTEGER NOT NULL REFERENCES draft_references(id) ON DELETE CASCADE,
    start INTEGER NOT NULL,
    end INTEGER NOT NULL,
    quote TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(reference_id, start, end)
);
CREATE TABLE IF NOT EXISTS benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '未分类',
    tags TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'paste',
    archived INTEGER NOT NULL DEFAULT 0,
    analysis_state TEXT NOT NULL DEFAULT 'idle',
    analysis_error TEXT,
    analysis_progress TEXT NOT NULL DEFAULT '',
    pending_job_id INTEGER,
    current_analysis_id INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmark_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    benchmark_id INTEGER NOT NULL REFERENCES benchmarks(id) ON DELETE CASCADE,
    body_hash TEXT NOT NULL,
    source_title TEXT NOT NULL,
    source_body TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmark_reviews (
    analysis_id INTEGER NOT NULL REFERENCES benchmark_analyses(id) ON DELETE CASCADE,
    paragraph_key TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    useful INTEGER NOT NULL DEFAULT 0,
    reviewed INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY(analysis_id, paragraph_key)
);
CREATE TABLE IF NOT EXISTS benchmark_review_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL REFERENCES benchmark_analyses(id) ON DELETE CASCADE,
    paragraph_key TEXT NOT NULL,
    record TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS draft_benchmarks (
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    benchmark_id INTEGER NOT NULL REFERENCES benchmarks(id) ON DELETE CASCADE,
    analysis_id INTEGER REFERENCES benchmark_analyses(id) ON DELETE SET NULL,
    legacy_reference_id INTEGER,
    created_at REAL NOT NULL,
    PRIMARY KEY(draft_id, benchmark_id)
);
CREATE TABLE IF NOT EXISTS benchmark_legacy (
    reference_id INTEGER PRIMARY KEY,
    benchmark_id INTEGER NOT NULL REFERENCES benchmarks(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS benchmark_highlights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    benchmark_id INTEGER NOT NULL REFERENCES benchmarks(id) ON DELETE CASCADE,
    body_hash TEXT NOT NULL,
    start INTEGER NOT NULL,
    end INTEGER NOT NULL,
    quote TEXT NOT NULL,
    legacy_highlight_id INTEGER UNIQUE,
    created_at REAL NOT NULL,
    UNIQUE(benchmark_id, body_hash, start, end)
);
CREATE TABLE IF NOT EXISTS benchmark_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    auto_classify INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmark_import_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES benchmark_imports(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    error TEXT NOT NULL DEFAULT '',
    warning TEXT NOT NULL DEFAULT '',
    benchmark_id INTEGER REFERENCES benchmarks(id) ON DELETE SET NULL,
    pending_job_id INTEGER,
    updated_at REAL NOT NULL,
    UNIQUE(batch_id,url)
);
CREATE INDEX IF NOT EXISTS benchmark_import_batch ON benchmark_import_items(batch_id,id);
CREATE TABLE IF NOT EXISTS draft_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'paste',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS draft_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    title TEXT, body TEXT, created_at REAL NOT NULL, origin TEXT
);
CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER REFERENCES drafts(id) ON DELETE SET NULL,
    platform TEXT NOT NULL,
    state TEXT NOT NULL,            -- sent | failed | unknown
    remote_id TEXT,
    detail TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER REFERENCES drafts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,             -- original file name, for display
    file TEXT NOT NULL,             -- file name inside the private images directory
    mime TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS image_remotes (
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    target TEXT NOT NULL,           -- wx_cover | wx_inline | x_media
    remote_id TEXT,
    url TEXT,
    expires_at REAL,                -- X media ids expire if unused; NULL = permanent
    created_at REAL NOT NULL,
    PRIMARY KEY (image_id, target)
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,  -- NULL = start of a thread
    author TEXT NOT NULL,           -- gary | ai
    body TEXT NOT NULL,
    quote TEXT,                     -- anchored text as it reads in the article (no Markdown); NULL = whole article
    prefix TEXT,                    -- a few characters before/after the quote, to tell repeats apart
    suffix TEXT,
    status TEXT NOT NULL DEFAULT 'open',    -- open | resolved (thread starts only)
    created_at REAL NOT NULL,
    resolved_at REAL
);
CREATE TABLE IF NOT EXISTS edit_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    base_version INTEGER NOT NULL,  -- the AI-written version the author started from
    trigger TEXT NOT NULL,          -- before-ai | publish | idle | manual
    changes TEXT NOT NULL,          -- json list of {before, after, context}
    stats TEXT,                     -- json counts
    end_hash TEXT,
    learned INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(draft_id, base_version)
);
CREATE TABLE IF NOT EXISTS case_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER REFERENCES drafts(id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',   -- queued | done | failed
    results TEXT,                   -- json list of cases found in 生财 MCP
    error TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | failed
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS media_imports (
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT '', original_title TEXT NOT NULL DEFAULT '',
 url TEXT NOT NULL DEFAULT '', source_platform TEXT NOT NULL DEFAULT '本地', source_kind TEXT NOT NULL,
 author TEXT NOT NULL DEFAULT '', upload_name TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'queued',
 stage INTEGER NOT NULL DEFAULT 0, skipped TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '',
 warning TEXT NOT NULL DEFAULT '', raw TEXT NOT NULL DEFAULT '', clean TEXT NOT NULL DEFAULT '', article TEXT NOT NULL DEFAULT '',
 tags TEXT NOT NULL DEFAULT '[]', target_platforms TEXT NOT NULL DEFAULT '[]', material_id INTEGER REFERENCES materials(id) ON DELETE SET NULL,
 benchmark_id INTEGER REFERENCES benchmarks(id) ON DELETE SET NULL, job_id INTEGER,
 created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS intake_batches (
 id INTEGER PRIMARY KEY AUTOINCREMENT, request_key TEXT NOT NULL UNIQUE,
 signature TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS intake_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL REFERENCES intake_batches(id),
 input_kind TEXT NOT NULL, input_value TEXT NOT NULL, fingerprint TEXT NOT NULL, supplement_body TEXT NOT NULL DEFAULT '',
 title TEXT NOT NULL DEFAULT '', depth TEXT NOT NULL, destination TEXT NOT NULL, asr_model TEXT NOT NULL DEFAULT 'base',
 route TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'queued', error TEXT NOT NULL DEFAULT '',
 media_id INTEGER REFERENCES media_imports(id), benchmark_id INTEGER REFERENCES benchmarks(id),
 material_id INTEGER REFERENCES materials(id), analysis_id INTEGER REFERENCES benchmark_analyses(id),
 duplicate_of INTEGER REFERENCES intake_items(id), job_id INTEGER, analysis_job_id INTEGER,
 created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS intake_batch_items ON intake_items(batch_id,id);
CREATE INDEX IF NOT EXISTS intake_fingerprint ON intake_items(fingerprint);

"""

_lock = threading.RLock()


def db_path() -> Path:
    return data_dir() / "workbench.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    new = not path.exists()
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if new:
        os.chmod(path, 0o600)
    return conn


_conn: sqlite3.Connection | None = None


# Columns added after the first release; ALTER TABLE keeps existing data.
MIGRATIONS = {
    "jobs": {"depends_on":"INTEGER REFERENCES jobs(id) ON DELETE SET NULL"},
    "media_imports": {"extraction_only":"INTEGER NOT NULL DEFAULT 0","file_hash":"TEXT NOT NULL DEFAULT ''","archived":"INTEGER NOT NULL DEFAULT 0", "angle":"TEXT NOT NULL DEFAULT ''", "facts":"TEXT NOT NULL DEFAULT ''", "asr_model":"TEXT NOT NULL DEFAULT 'base'", "distiller":"TEXT NOT NULL DEFAULT 'xiaod'"},
    "benchmark_reviews": {"decision": "TEXT NOT NULL DEFAULT 'unreviewed'", "judgment": "TEXT NOT NULL DEFAULT '{}'"},
    "ideas": {
        "method_ids": "TEXT NOT NULL DEFAULT '[]'",
        "audience": "TEXT NOT NULL DEFAULT ''",
        "problem": "TEXT NOT NULL DEFAULT ''",
        "thesis": "TEXT NOT NULL DEFAULT ''",
        "available_materials": "TEXT NOT NULL DEFAULT ''",
        "missing_materials": "TEXT NOT NULL DEFAULT ''",
        "readiness": "TEXT NOT NULL DEFAULT 'unreviewed'",
        "material_ids": "TEXT NOT NULL DEFAULT '[]'",
        "benchmark_ids": "TEXT NOT NULL DEFAULT '[]'",
        "sources_selected": "INTEGER NOT NULL DEFAULT 0",
        "image_ids": "TEXT NOT NULL DEFAULT '[]'",
        "media_id": "INTEGER REFERENCES media_imports(id) ON DELETE SET NULL",
        "group_id": "INTEGER REFERENCES content_groups(id) ON DELETE SET NULL",
        "benchmark_id": "INTEGER REFERENCES benchmarks(id) ON DELETE SET NULL",
    },
    "materials": {"media_id": "INTEGER REFERENCES media_imports(id) ON DELETE SET NULL"},
    "benchmarks": {"media_id": "INTEGER REFERENCES media_imports(id) ON DELETE SET NULL"},
    "drafts": {
        "review_issues": "TEXT NOT NULL DEFAULT '[]'",
        "writing_context": "TEXT NOT NULL DEFAULT ''",
        "media_id": "INTEGER REFERENCES media_imports(id) ON DELETE SET NULL",
        "group_id": "INTEGER REFERENCES content_groups(id) ON DELETE SET NULL",
        "cover_wechat": "INTEGER REFERENCES images(id) ON DELETE SET NULL",
        "cover_x": "INTEGER REFERENCES images(id) ON DELETE SET NULL",
        "cover_crop": "TEXT",           # json {"square_x": 0..1}: where the 1:1 公众号 crop starts
        "summary": "TEXT NOT NULL DEFAULT ''",     # 公众号 摘要; empty = first lines of the body
        "source_url": "TEXT NOT NULL DEFAULT ''",  # 公众号 阅读原文
        "wechat_theme": "TEXT NOT NULL DEFAULT ''",  # xilo theme; empty keeps original layout
        "allow_reference_learning": "INTEGER NOT NULL DEFAULT 0",
        # the one Feishu document that archives this draft
        "feishu_doc": "TEXT",
        "feishu_url": "TEXT",
        "feishu_rev": "INTEGER",            # revision right after our last write; differs → edited in Feishu
        "feishu_state": "TEXT",             # syncing | ok | conflict | failed
        "feishu_error": "TEXT",
        "feishu_synced_version": "REAL",    # drafts.updated_at that the archive reflects
        "feishu_tried_version": "REAL",     # drafts.updated_at of the last attempt
        "feishu_synced_at": "REAL",
        # the last AI revision from comments: {"before": version id, "after": version id, "summary", "at", "threads"}
        "last_revision": "TEXT",
        "xhs": "TEXT",                       # 小红书 version: title, body, tags, cards, picture ids
        "video": "TEXT",                     # short video: scenes (script), voice, render state, last MP4
    },
}


def migrate(conn: sqlite3.Connection) -> None:
    MIGRATIONS.setdefault('benchmarks', {}).update({'original_html': "TEXT NOT NULL DEFAULT ''", 'read_count': 'INTEGER', 'read_count_at': 'REAL'})
    for table, columns in MIGRATIONS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    from .benchmarks import migrate_references
    migrate_references(conn)
    conn.commit()


def get() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = connect()
            _conn.executescript(SCHEMA)
            migrate(_conn)
        return _conn


def reset_for_tests() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


@contextmanager
def tx():
    conn = get()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def rows(sql: str, args=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in get().execute(sql, args).fetchall()]


def row(sql: str, args=()) -> dict | None:
    with _lock:
        r = get().execute(sql, args).fetchone()
        return dict(r) if r else None


def now() -> float:
    return time.time()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value, default=None):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default

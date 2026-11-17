"""Relational metadata store (SQLite locally; swap for Postgres on server).

Holds normalized metadata + durable agent-run/evaluation records separate from raw
evidence, embeddings and graph — per the 'keep layers separate' principle.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    name TEXT PRIMARY KEY, path TEXT, base_branch TEXT, last_ingest REAL, head_sha TEXT
);
CREATE TABLE IF NOT EXISTS symbols (
    id TEXT PRIMARY KEY, repo TEXT, path TEXT, name TEXT, kind TEXT,
    lineno INTEGER, signature TEXT, branch TEXT
);
CREATE TABLE IF NOT EXISTS commits (
    sha TEXT PRIMARY KEY, repo TEXT, author TEXT, ts REAL, subject TEXT, branch TEXT
);
CREATE TABLE IF NOT EXISTS change_files (
    repo TEXT, branch TEXT, sha TEXT, path TEXT, author TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS runs (
    request_id TEXT PRIMARY KEY, request TEXT, repo TEXT, status TEXT,
    created_at REAL, ended_at REAL, state_json TEXT
);
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT, outcome TEXT,
    lesson TEXT, recommendation TEXT, ts REAL
);
CREATE INDEX IF NOT EXISTS ix_symbols_repo ON symbols(repo, name);
CREATE INDEX IF NOT EXISTS ix_changes_path ON change_files(repo, path);
"""


class MetadataStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._local = threading.local()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    # ---- repos ----
    def upsert_repo(self, name: str, path: str, base_branch: str, head_sha: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO repos(name,path,base_branch,last_ingest,head_sha) VALUES(?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET path=excluded.path, base_branch=excluded.base_branch, "
                "last_ingest=excluded.last_ingest, head_sha=excluded.head_sha",
                (name, path, base_branch, time.time(), head_sha),
            )

    def repos(self) -> list[dict]:
        return [dict(r) for r in self._conn().execute("SELECT * FROM repos").fetchall()]

    # ---- symbols ----
    def replace_symbols(self, repo: str, branch: str, rows: list[dict]) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM symbols WHERE repo=? AND branch=?", (repo, branch))
            c.executemany(
                "INSERT OR REPLACE INTO symbols(id,repo,path,name,kind,lineno,signature,branch) "
                "VALUES(:id,:repo,:path,:name,:kind,:lineno,:signature,:branch)",
                [{**r, "repo": repo, "branch": branch} for r in rows],
            )

    def symbols_by_name(self, name: str, limit: int = 25) -> list[dict]:
        q = "SELECT * FROM symbols WHERE name LIKE ? LIMIT ?"
        return [dict(r) for r in self._conn().execute(q, (f"%{name}%", limit)).fetchall()]

    # ---- commits + changed files ----
    def record_commit(self, sha: str, repo: str, author: str, ts: float, subject: str, branch: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO commits(sha,repo,author,ts,subject,branch) VALUES(?,?,?,?,?,?)",
                      (sha, repo, author, ts, subject, branch))

    def record_change_files(self, repo: str, branch: str, sha: str, author: str, ts: float, paths: list[str]) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM change_files WHERE repo=? AND branch=? AND sha=?", (repo, branch, sha))
            c.executemany(
                "INSERT INTO change_files(repo,branch,sha,path,author,ts) VALUES(?,?,?,?,?,?)",
                [(repo, branch, sha, p, author, ts) for p in paths],
            )

    def active_change_files(self, repo: str, exclude_branch: str) -> list[dict]:
        q = ("SELECT DISTINCT repo,branch,path,author,ts FROM change_files "
             "WHERE repo=? AND branch<>? ORDER BY ts DESC")
        return [dict(r) for r in self._conn().execute(q, (repo, exclude_branch)).fetchall()]

    # ---- runs + evaluations (self-learning memory) ----
    def save_run(self, request_id: str, request: str, repo: str, status: str,
                 created_at: float, ended_at: float | None, state_json: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO runs(request_id,request,repo,status,created_at,ended_at,state_json) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET status=excluded.status, "
                "ended_at=excluded.ended_at, state_json=excluded.state_json",
                (request_id, request, repo, status, created_at, ended_at, json.dumps(state_json)),
            )

    def add_evaluation(self, request_id: str, outcome: str, lesson: str, recommendation: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO evaluations(request_id,outcome,lesson,recommendation,ts) VALUES(?,?,?,?,?)",
                      (request_id, outcome, lesson, recommendation, time.time()))

    def recent_lessons(self, limit: int = 10) -> list[dict]:
        q = "SELECT * FROM evaluations ORDER BY ts DESC LIMIT ?"
        return [dict(r) for r in self._conn().execute(q, (limit,)).fetchall()]

    def get_run_state(self, request_id: str) -> dict | None:
        row = self._conn().execute("SELECT state_json FROM runs WHERE request_id=?", (request_id,)).fetchone()
        return json.loads(row["state_json"]) if row and row["state_json"] else None

    def runs(self, limit: int = 50) -> list[dict]:
        q = "SELECT request_id,request,repo,status,created_at,ended_at FROM runs ORDER BY created_at DESC LIMIT ?"
        return [dict(r) for r in self._conn().execute(q, (limit,)).fetchall()]

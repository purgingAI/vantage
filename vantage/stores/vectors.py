"""Embedded vector store + offline embedder (pure Python, no numpy).

Local: a deterministic hashing embedder + cosine search over SQLite-persisted vectors.
Good enough to demonstrate semantic retrieval offline. On server, set VANTAGE_VECTORS=
clickhouse (or a dedicated store like Qdrant) and swap embed() for a real model such as
Voyage AI (Anthropic's recommended embeddings partner) — the VectorStore surface is stable.

We embed MEANINGFUL chunks (symbols, feature summaries, tests, commit/PR summaries),
never blind per-line embeddings.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from pathlib import Path

DIM = 256
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def embed(text: str, dim: int = DIM) -> list[float]:
    """Deterministic bag-of-hashed-tokens embedding (offline).

    Token -> bucket via sha1; tf weighting; L2-normalized. Captures lexical/semantic
    overlap well enough for local retrieval demos and is fully reproducible.
    """
    vec = [0.0] * dim
    toks = _TOKEN.findall((text or "").lower())
    if not toks:
        return vec
    for t in toks:
        h = int(hashlib.sha1(t.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
        vec[(h // dim) % dim] += 0.5   # a second bucket reduces collisions
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))   # inputs are pre-normalized


class VectorStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._local = threading.local()
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS vectors("
                      "id TEXT PRIMARY KEY, repo TEXT, kind TEXT, ref TEXT, text TEXT, vec TEXT)")
            c.execute("CREATE INDEX IF NOT EXISTS ix_vec_repo ON vectors(repo)")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn = conn
        return conn

    def upsert(self, id: str, repo: str, kind: str, ref: str, text: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO vectors(id,repo,kind,ref,text,vec) VALUES(?,?,?,?,?,?)",
                      (id, repo, kind, ref, text, json.dumps(embed(text))))

    def upsert_many(self, rows: list[dict]) -> None:
        with self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO vectors(id,repo,kind,ref,text,vec) VALUES(?,?,?,?,?,?)",
                [(r["id"], r["repo"], r["kind"], r["ref"], r["text"], json.dumps(embed(r["text"]))) for r in rows],
            )

    def search(self, query: str, repo: str | None = None, k: int = 8) -> list[dict]:
        qv = embed(query)
        sql = "SELECT id,repo,kind,ref,text,vec FROM vectors"
        args: tuple = ()
        if repo:
            sql += " WHERE repo=?"
            args = (repo,)
        rows = self._conn().execute(sql, args).fetchall()
        scored = []
        for id_, rp, kind, ref, text, vec in rows:
            score = cosine(qv, json.loads(vec))
            if score > 0:
                scored.append({"id": id_, "repo": rp, "kind": kind, "ref": ref,
                               "text": text, "score": round(score, 4)})
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:k]

    def count(self, repo: str | None = None) -> int:
        if repo:
            return self._conn().execute("SELECT COUNT(*) FROM vectors WHERE repo=?", (repo,)).fetchone()[0]
        return self._conn().execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

"""
Vector memory store for MARK 1 (formerly Mark XLVIII).

Semantic recall over JARVIS's long-term memory. Lets the assistant answer
questions like "what did the user tell me about Project X last week?" by
similarity search instead of exact key lookup.

Design
──────
• Embeddings: prefers Google's free `text-embedding-004` via the
  google-genai SDK when a Gemini key is configured. Falls back to a
  pure-Python hashing bag-of-words embedding (1024-dim, no extra deps)
  so the store works even without a network.
• Storage: SQLite (one row per memory: id, text, metadata JSON, embedding
  JSON, created_at). Pure-Python cosine similarity — fine for the 1k–10k
  memory scale a single user generates; upgrade to sqlite-vec if you ever
  outgrow it.
• Concurrency: a single threading.Lock around writes. Reads are lock-free.

Public surface
──────────────
    from core.memory_vector import vstore
    vstore.remember("User asked about the Honda project timeline.",
                    metadata={"category": "projects", "tags": ["honda"]})
    hits = vstore.recall("honda timeline", k=5)
    for h in hits:
        print(h["score"], h["text"])

The existing `memory/memory_manager.py` JSON store is preserved —
the vector store layers on top, not replaces.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Optional


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR     = _get_base_dir()
DB_PATH      = BASE_DIR / "memory" / "vector_store.db"
_lock        = threading.Lock()
_initialized = False


# ───────────────────────── schema setup ─────────────────────────────

def _ensure_schema() -> None:
    global _initialized
    if _initialized:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock, _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id         TEXT PRIMARY KEY,
                text       TEXT NOT NULL,
                metadata   TEXT NOT NULL DEFAULT '{}',
                embedding  TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON memories(created_at)")
        conn.commit()
    _initialized = True


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ───────────────────────── embedding backends ─────────────────────

# Tiny stop-word list — enough to remove noise from the hashing embedding.
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "should", "could", "may", "might", "must", "shall",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
    "them", "my", "your", "his", "its", "our", "their", "this", "that",
    "these", "those", "of", "in", "on", "at", "to", "for", "with", "from",
    "by", "as", "so", "than", "too", "very", "can", "just", "about", "up",
})

_HASH_DIM = 1024
_word_re = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _hash_embedding(text: str) -> list[float]:
    """
    Pure-Python hashing bag-of-words embedding. No ML, no network.

    Each token is hashed into a bucket in [0, _HASH_DIM) and its count is
    added to that dimension. Final vector is L2-normalised so cosine
    similarity reduces to a dot product.

    Quality is good enough for personal-scale semantic recall (~10k
    memories). When a Gemini key is available the SDK embedding is
    strongly preferred — see `embed()`.
    """
    vec = [0.0] * _HASH_DIM
    for tok in _word_re.findall(text.lower()):
        if tok in _STOP_WORDS or len(tok) < 2:
            continue
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "little") % _HASH_DIM
        sign = 1.0 if (h[4] & 1) == 0 else -1.0
        vec[idx] += sign
    # L2 normalise
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _gemini_embedding(text: str) -> Optional[list[float]]:
    """
    Try Google's free text-embedding-004 model via the genai SDK.
    Returns None if the SDK / key isn't available — caller falls back to
    the hashing embedding.
    """
    try:
        from google import genai  # type: ignore
        from core import ai_client
        key = ai_client.get_gemini_key()
        if not key:
            return None
        client = genai.Client(api_key=key)
        # Truncate text to keep API cost low (~8k tokens is the model limit).
        snippet = text[:8000]
        r = client.models.embed_content(
            model="text-embedding-004",
            contents=snippet,
        )
        # SDK returns an object with .embeddings
        embs = getattr(r, "embeddings", None)
        if embs and len(embs) > 0:
            values = getattr(embs[0], "values", None)
            if isinstance(values, list):
                return values
        # Fallback shape: dict-like
        if isinstance(embs, list) and embs and isinstance(embs[0], dict):
            v = embs[0].get("values")
            if isinstance(v, list):
                return v
    except Exception as e:
        print(f"[vector] Gemini embedding failed ({type(e).__name__}: {e}) — using hashing fallback.")
    return None


def embed(text: str) -> list[float]:
    """Return an embedding for `text`. Tries Gemini first, falls back to hashing."""
    gem = _gemini_embedding(text)
    if gem is not None:
        return gem
    return _hash_embedding(text)


# ───────────────────────── similarity ──────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Vectors are assumed L2-normalised (hashing path).
    Gemini embeddings are also unit-norm, but we re-normalise defensively."""
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        # Mismatched dims → treat as orthogonal (similarity 0)
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na  += x * x
        nb  += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ───────────────────────── public API ──────────────────────────────

class VectorStore:
    """File-backed vector memory. Singleton-friendly — one shared instance."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        _ensure_schema()  # ensures DB + table exist

    # ── writes ────────────────────────────────────────────────────

    def remember(
        self,
        text: str,
        *,
        metadata: Optional[dict] = None,
        id: Optional[str] = None,
    ) -> str:
        """
        Store a memory with its embedding. Returns the memory id.

        `metadata` can hold arbitrary JSON-serialisable context (category,
        tags, source, etc.). Idempotent on `id`: re-inserting the same id
        replaces the row.
        """
        _ensure_schema()
        text = (text or "").strip()
        if not text:
            return ""
        mid = id or hashlib.sha256(
            (text + str(time.time())).encode("utf-8")
        ).hexdigest()[:16]
        embedding = embed(text)
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        emb_json = json.dumps(embedding)
        ts = time.time()
        with _lock, _connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO memories (id, text, metadata, embedding, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (mid, text, meta_json, emb_json, ts),
            )
            conn.commit()
        return mid

    def forget(self, memory_id: str) -> bool:
        """Delete a memory by id. Returns True if a row was removed."""
        _ensure_schema()
        with _lock, _connect() as conn:
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()
            return cur.rowcount > 0

    def clear(self) -> int:
        """Wipe ALL memories. Returns count deleted. Use with caution."""
        _ensure_schema()
        with _lock, _connect() as conn:
            cur = conn.execute("DELETE FROM memories")
            conn.commit()
            return cur.rowcount

    # ── reads ─────────────────────────────────────────────────────

    def recall(
        self,
        query: str,
        *,
        k: int = 5,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        min_score: float = 0.0,
    ) -> list[dict]:
        """
        Semantic search. Returns a list of `{"id","text","metadata","score",
        "created_at"}` dicts, sorted by similarity descending.

        Optional filters:
          category: only rows where metadata['category'] == value
          tag:      only rows where metadata['tags'] contains value
          min_score: drop anything below this cosine threshold
        """
        _ensure_schema()
        q_emb = embed(query)
        rows = self._scan(category=category, tag=tag)
        scored = []
        for r in rows:
            emb = json.loads(r["embedding"] or "[]")
            score = _cosine(q_emb, emb)
            if score < min_score:
                continue
            scored.append({
                "id":         r["id"],
                "text":       r["text"],
                "metadata":   json.loads(r["metadata"] or "{}"),
                "score":      round(score, 4),
                "created_at": r["created_at"],
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:k]

    def list_all(self, limit: int = 1000) -> list[dict]:
        """Return all memories (no embeddings) — for UI / debug."""
        _ensure_schema()
        with _connect() as conn:
            cur = conn.execute(
                """SELECT id, text, metadata, created_at
                   FROM memories ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            )
            return [
                {
                    "id":         row["id"],
                    "text":       row["text"],
                    "metadata":   json.loads(row["metadata"] or "{}"),
                    "created_at": row["created_at"],
                }
                for row in cur.fetchall()
            ]

    def count(self) -> int:
        _ensure_schema()
        with _connect() as conn:
            cur = conn.execute("SELECT COUNT(*) AS n FROM memories")
            return cur.fetchone()["n"]

    # ── internal ──────────────────────────────────────────────────

    def _scan(self, *, category: Optional[str], tag: Optional[str]) -> list[sqlite3.Row]:
        """Fetch candidate rows, optionally filtered by metadata."""
        with _connect() as conn:
            if not category and not tag:
                cur = conn.execute(
                    "SELECT id, text, metadata, embedding, created_at FROM memories"
                )
                return cur.fetchall()
            # Cheap filter: pull all rows then filter in Python on metadata.
            # For 10k+ rows, switch to a metadata column + index.
            cur = conn.execute(
                "SELECT id, text, metadata, embedding, created_at FROM memories"
            )
            out = []
            for row in cur.fetchall():
                meta = json.loads(row["metadata"] or "{}")
                if category and meta.get("category") != category:
                    continue
                if tag and tag not in (meta.get("tags") or []):
                    continue
                out.append(row)
            return out


# Module-level singleton — most call sites use this directly.
vstore = VectorStore()


# ───────────────────────── smoke test ──────────────────────────────

if __name__ == "__main__":
    # Drop test rows so reruns are clean
    vstore.clear()
    vstore.remember(
        "User asked me to remind them about the dentist appointment on Friday.",
        metadata={"category": "reminders", "tags": ["dentist", "friday"]},
    )
    vstore.remember(
        "User said the Honda Civic needs an oil change next month.",
        metadata={"category": "projects", "tags": ["honda", "car", "maintenance"]},
    )
    vstore.remember(
        "User's preferred name is 'sir' and they speak Turkish at home.",
        metadata={"category": "identity", "tags": ["language", "name"]},
    )

    print(f"Stored {vstore.count()} memories.\n")
    for q in ("car maintenance", "what language does the user speak?", "dentist"):
        print(f"Query: {q!r}")
        for hit in vstore.recall(q, k=2):
            print(f"  {hit['score']:.3f}  {hit['text'][:80]}")
        print()

    # Cleanup
    vstore.clear()
    print("Done.")

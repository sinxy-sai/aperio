from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, get_knowledge_db_path, get_knowledge_enabled


TEXT_SUFFIXES = {".md", ".txt", ".toml"}
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "aperio_agent_backend/workspace",
}
MAX_FILE_BYTES = 512 * 1024
CHUNK_SIZE = 2200
CHUNK_OVERLAP = 260


@dataclass(frozen=True)
class KnowledgeHit:
    path: str
    title: str
    content: str
    score: float


def knowledge_enabled() -> bool:
    return get_knowledge_enabled()


def knowledge_db_path() -> Path:
    return get_knowledge_db_path()


def sync_project_knowledge(project_root: Path | None = None) -> dict[str, Any]:
    if not knowledge_enabled():
        return {"enabled": False, "indexed": 0, "database": str(knowledge_db_path())}
    root = (project_root or PROJECT_ROOT).resolve()
    try:
        store = _KnowledgeStore(knowledge_db_path())
    except (OSError, sqlite3.Error) as exc:
        return {
            "enabled": False,
            "indexed": 0,
            "database": str(knowledge_db_path()),
            "error": str(exc),
        }
    indexed = 0
    for path in _iter_knowledge_files(root):
        try:
            changed = store.upsert_file(path, root)
        except (OSError, sqlite3.Error):
            changed = False
        if changed:
            indexed += 1
    return {
        "enabled": True,
        "indexed": indexed,
        "database": str(knowledge_db_path()),
        "project_root": str(root),
    }


def search_project_knowledge(query: str, *, limit: int = 8, project_root: Path | None = None) -> list[KnowledgeHit]:
    if not knowledge_enabled() or not query.strip():
        return []
    root = (project_root or PROJECT_ROOT).resolve()
    sync_result = sync_project_knowledge(root)
    if sync_result.get("error"):
        return []
    try:
        return _KnowledgeStore(knowledge_db_path()).search(query, limit=max(1, min(limit, 20)))
    except (OSError, sqlite3.Error):
        return []


def build_project_knowledge_context(
    *,
    query: str,
    project_root: Path | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    if not knowledge_enabled():
        return {"enabled": False, "items": [], "markdown": "Project knowledge search is disabled."}
    hits = search_project_knowledge(query, limit=limit, project_root=project_root)
    markdown = knowledge_hits_to_markdown(hits)
    return {
        "enabled": True,
        "database": str(knowledge_db_path()),
        "query": query,
        "items": [_hit_to_dict(hit) for hit in hits],
        "markdown": markdown,
    }


def knowledge_hits_to_markdown(hits: list[KnowledgeHit]) -> str:
    if not hits:
        return "No project knowledge matched the current request."
    lines = ["# Aperio Project Knowledge", ""]
    for index, hit in enumerate(hits, start=1):
        excerpt = _compact(hit.content, 1200)
        lines.extend(
            [
                f"## {index}. {hit.title or hit.path}",
                "",
                f"- Source: `{hit.path}`",
                f"- Score: {hit.score:.3f}",
                "",
                excerpt,
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


class _KnowledgeStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                create table if not exists documents (
                    id integer primary key autoincrement,
                    path text not null unique,
                    title text not null,
                    content_hash text not null,
                    mtime real not null,
                    updated_at real not null
                )
                """
            )
            db.execute(
                """
                create table if not exists chunks (
                    id integer primary key autoincrement,
                    document_id integer not null references documents(id) on delete cascade,
                    path text not null,
                    title text not null,
                    chunk_index integer not null,
                    content text not null
                )
                """
            )
            db.execute(
                """
                create virtual table if not exists chunks_fts using fts5(
                    content,
                    path unindexed,
                    title unindexed,
                    chunk_id unindexed
                )
                """
            )

    def upsert_file(self, path: Path, root: Path) -> bool:
        try:
            raw = path.read_bytes()
        except OSError:
            return False
        if len(raw) > MAX_FILE_BYTES:
            return False
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return False
        rel_path = path.resolve().relative_to(root).as_posix()
        digest = hashlib.sha256(raw).hexdigest()
        mtime = path.stat().st_mtime
        title = _title_for(path, text)

        with self._connect() as db:
            existing = db.execute("select id, content_hash from documents where path = ?", (rel_path,)).fetchone()
            if existing and existing["content_hash"] == digest:
                return False
            now = time.time()
            if existing:
                doc_id = int(existing["id"])
                db.execute(
                    "update documents set title = ?, content_hash = ?, mtime = ?, updated_at = ? where id = ?",
                    (title, digest, mtime, now, doc_id),
                )
                self._delete_chunks(db, doc_id)
            else:
                cursor = db.execute(
                    """
                    insert into documents(path, title, content_hash, mtime, updated_at)
                    values (?, ?, ?, ?, ?)
                    """,
                    (rel_path, title, digest, mtime, now),
                )
                doc_id = int(cursor.lastrowid)

            for chunk_index, chunk in enumerate(_chunk_text(text)):
                cursor = db.execute(
                    """
                    insert into chunks(document_id, path, title, chunk_index, content)
                    values (?, ?, ?, ?, ?)
                    """,
                    (doc_id, rel_path, title, chunk_index, chunk),
                )
                chunk_id = int(cursor.lastrowid)
                db.execute(
                    "insert into chunks_fts(content, path, title, chunk_id) values (?, ?, ?, ?)",
                    (chunk, rel_path, title, chunk_id),
                )
        return True

    def search(self, query: str, limit: int) -> list[KnowledgeHit]:
        fts_query = _fts_query(query)
        with self._connect() as db:
            rows: list[sqlite3.Row] = []
            if fts_query:
                try:
                    rows = db.execute(
                        """
                        select path, title, content, bm25(chunks_fts) as rank
                        from chunks_fts
                        where chunks_fts match ?
                        order by rank
                        limit ?
                        """,
                        (fts_query, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if not rows:
                like = f"%{query.strip()}%"
                rows = db.execute(
                    """
                    select path, title, content, 1.0 as rank
                    from chunks
                    where content like ? or path like ? or title like ?
                    limit ?
                    """,
                    (like, like, like, limit),
                ).fetchall()
        return [
            KnowledgeHit(
                path=str(row["path"]),
                title=str(row["title"]),
                content=str(row["content"]),
                score=float(-row["rank"]) if isinstance(row["rank"], (int, float)) else 0.0,
            )
            for row in rows
        ]

    def _delete_chunks(self, db: sqlite3.Connection, doc_id: int) -> None:
        chunk_ids = [int(row["id"]) for row in db.execute("select id from chunks where document_id = ?", (doc_id,))]
        for chunk_id in chunk_ids:
            db.execute("delete from chunks_fts where chunk_id = ?", (chunk_id,))
        db.execute("delete from chunks where document_id = ?", (doc_id,))


def _iter_knowledge_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    explicit = [
        root / "README.md",
        root / "pyproject.toml",
        root / "aperio_agent_backend" / "README.md",
        root / "aperio_agent_web" / "README.md",
    ]
    candidates.extend(path for path in explicit if path.exists())
    docs = root / "docs"
    if docs.exists():
        candidates.extend(path for path in docs.rglob("*") if path.is_file())
    return sorted(path for path in set(candidates) if _is_indexable(path, root))


def _is_indexable(path: Path, root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root).as_posix()
    except ValueError:
        return False
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    return not any(rel == excluded or rel.startswith(f"{excluded}/") for excluded in EXCLUDED_DIRS)


def _chunk_text(text: str) -> list[str]:
    normalized = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(normalized) <= CHUNK_SIZE:
        return [normalized]
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + CHUNK_SIZE)
        split = normalized.rfind("\n\n", start, end)
        if split <= start + 400:
            split = end
        chunks.append(normalized[start:split].strip())
        start = max(split - CHUNK_OVERLAP, split)
        if start >= len(normalized):
            break
    return [chunk for chunk in chunks if chunk]


def _title_for(path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.name
    return path.name


def _fts_query(query: str) -> str:
    terms = re.findall(r"[\w\u4e00-\u9fff]+", query.lower())
    terms = [term for term in terms if len(term) > 1]
    return " OR ".join(f'"{term}"' for term in terms[:12])


def _compact(text: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _hit_to_dict(hit: KnowledgeHit) -> dict[str, Any]:
    return {
        "path": hit.path,
        "title": hit.title,
        "content": _compact(hit.content, 1200),
        "score": hit.score,
    }

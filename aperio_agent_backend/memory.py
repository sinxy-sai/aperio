from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import get_memory_db_path, get_memory_enabled


SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|password|passwd|secret|authorization)\b\s*[:=]\s*([^\s,;]+)"
)


@dataclass(frozen=True)
class MemoryItem:
    id: int
    scope: str
    kind: str
    key: str
    content: str
    metadata: dict[str, Any]
    created_at: float
    updated_at: float


def memory_enabled() -> bool:
    return get_memory_enabled()


def memory_db_path() -> Path:
    return get_memory_db_path()


def add_memory(
    *,
    kind: str,
    content: str,
    scope: str = "project",
    key: str = "",
    metadata: dict[str, Any] | None = None,
) -> MemoryItem | None:
    if not memory_enabled():
        return None
    normalized = _sanitize(content)
    if not normalized:
        return None
    now = time.time()
    db = _connect()
    with db:
        db.execute(
            """
            insert into memories(scope, kind, key, content, metadata_json, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope.strip() or "project",
                kind.strip() or "note",
                key.strip(),
                normalized,
                json.dumps(metadata or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        memory_id = int(db.execute("select last_insert_rowid()").fetchone()[0])
    return get_memory(memory_id)


def list_memories(
    *,
    scope: str | None = None,
    kind: str | None = None,
    query: str = "",
    limit: int = 20,
) -> list[MemoryItem]:
    if not memory_enabled():
        return []
    limit = max(1, min(int(limit or 20), 200))
    search_query = _search_query(query)
    if search_query:
        try:
            results = _search_fts(search_query, scope=scope, kind=kind, limit=limit)
            if results:
                return results
        except sqlite3.DatabaseError:
            pass
        return _search_like(query, scope=scope, kind=kind, limit=limit)

    clauses: list[str] = []
    params: list[Any] = []
    if scope:
        clauses.append("scope = ?")
        params.append(scope)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    where = f" where {' and '.join(clauses)}" if clauses else ""
    rows = _connect().execute(
        f"""
        select id, scope, kind, key, content, metadata_json, created_at, updated_at
        from memories
        {where}
        order by updated_at desc, id desc
        limit ?
        """,
        [*params, limit],
    )
    return [_row_to_memory(row) for row in rows.fetchall()]


def search_memories(query: str, *, scope: str | None = None, kind: str | None = None, limit: int = 8) -> list[MemoryItem]:
    return list_memories(scope=scope, kind=kind, query=query, limit=limit)


def get_memory(memory_id: int) -> MemoryItem | None:
    if not memory_enabled():
        return None
    row = _connect().execute(
        """
        select id, scope, kind, key, content, metadata_json, created_at, updated_at
        from memories
        where id = ?
        """,
        (memory_id,),
    ).fetchone()
    return _row_to_memory(row) if row else None


def delete_memory(memory_id: int) -> bool:
    if not memory_enabled():
        return False
    db = _connect()
    with db:
        cursor = db.execute("delete from memories where id = ?", (memory_id,))
    return cursor.rowcount > 0


def build_memory_context(*, query: str = "", limit: int = 8) -> dict[str, Any]:
    if not memory_enabled():
        return {"enabled": False, "items": [], "markdown": ""}
    items = search_memories(query, limit=limit) if query else list_memories(limit=limit)
    markdown = memory_items_to_markdown(items)
    return {
        "enabled": True,
        "database": str(memory_db_path()),
        "query": query,
        "items": [_memory_to_dict(item) for item in items],
        "markdown": markdown,
    }


def memory_items_to_markdown(items: list[MemoryItem]) -> str:
    if not items:
        return "No persistent memory has been recorded yet."
    lines = ["# Aperio Persistent Memory", ""]
    for item in items:
        label = item.kind
        if item.key:
            label += f" - {item.key}"
        lines.append(f"- [{item.scope}] {label}: {item.content}")
    return "\n".join(lines).strip()


def record_run_memory(
    *,
    run_id: str,
    route: str,
    user_message: str,
    answer: str,
    artifacts: list[Any] | None = None,
) -> MemoryItem | None:
    artifact_paths = [
        str(getattr(item, "path", "") or item.get("path", ""))
        for item in (artifacts or [])
        if getattr(item, "path", "") or (isinstance(item, dict) and item.get("path"))
    ]
    content = "\n".join(
        [
            f"Run {run_id} route={route or 'unknown'}.",
            f"User asked: {_compact(user_message, 300)}",
            f"Assistant answered: {_compact(answer, 600)}",
            f"Artifacts: {', '.join(artifact_paths[:8])}" if artifact_paths else "",
        ]
    ).strip()
    return add_memory(
        kind="run_summary",
        key=run_id,
        content=content,
        metadata={"run_id": run_id, "route": route, "artifacts": artifact_paths[:20]},
    )


def _connect() -> sqlite3.Connection:
    path = memory_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    _init_schema(db)
    return db


def _init_schema(db: sqlite3.Connection) -> None:
    with db:
        db.execute(
            """
            create table if not exists memories (
                id integer primary key autoincrement,
                scope text not null,
                kind text not null,
                key text not null default '',
                content text not null,
                metadata_json text not null default '{}',
                created_at real not null,
                updated_at real not null
            )
            """
        )
        db.execute("create index if not exists idx_memories_scope_kind on memories(scope, kind)")
        db.execute("create index if not exists idx_memories_updated on memories(updated_at)")
        db.execute(
            """
            create virtual table if not exists memories_fts using fts5(
                content,
                scope unindexed,
                kind unindexed,
                key unindexed,
                content='memories',
                content_rowid='id'
            )
            """
        )
        db.execute(
            """
            create trigger if not exists memories_ai after insert on memories begin
                insert into memories_fts(rowid, content, scope, kind, key)
                values (new.id, new.content, new.scope, new.kind, new.key);
            end
            """
        )
        db.execute(
            """
            create trigger if not exists memories_ad after delete on memories begin
                insert into memories_fts(memories_fts, rowid, content, scope, kind, key)
                values ('delete', old.id, old.content, old.scope, old.kind, old.key);
            end
            """
        )
        db.execute(
            """
            create trigger if not exists memories_au after update on memories begin
                insert into memories_fts(memories_fts, rowid, content, scope, kind, key)
                values ('delete', old.id, old.content, old.scope, old.kind, old.key);
                insert into memories_fts(rowid, content, scope, kind, key)
                values (new.id, new.content, new.scope, new.kind, new.key);
            end
            """
        )
        fts_count = int(db.execute("select count(*) from memories_fts").fetchone()[0])
        memory_count = int(db.execute("select count(*) from memories").fetchone()[0])
        if memory_count and not fts_count:
            db.execute(
                """
                insert into memories_fts(rowid, content, scope, kind, key)
                select id, content, scope, kind, key from memories
                """
            )


def _row_to_memory(row: sqlite3.Row) -> MemoryItem:
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return MemoryItem(
        id=int(row["id"]),
        scope=str(row["scope"]),
        kind=str(row["kind"]),
        key=str(row["key"]),
        content=str(row["content"]),
        metadata=metadata if isinstance(metadata, dict) else {},
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _memory_to_dict(item: MemoryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "scope": item.scope,
        "kind": item.kind,
        "key": item.key,
        "content": item.content,
        "metadata": item.metadata,
        "createdAt": item.created_at,
        "updatedAt": item.updated_at,
    }


def _sanitize(text: str) -> str:
    redacted = SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", text or "")
    return _compact(redacted, 2000)


def _search_fts(query: str, *, scope: str | None, kind: str | None, limit: int) -> list[MemoryItem]:
    clauses = ["memories_fts match ?"]
    params: list[Any] = [query]
    if scope:
        clauses.append("m.scope = ?")
        params.append(scope)
    if kind:
        clauses.append("m.kind = ?")
        params.append(kind)
    rows = _connect().execute(
        f"""
        select m.id, m.scope, m.kind, m.key, m.content, m.metadata_json, m.created_at, m.updated_at
        from memories_fts
        join memories m on m.id = memories_fts.rowid
        where {' and '.join(clauses)}
        order by bm25(memories_fts), m.updated_at desc
        limit ?
        """,
        [*params, limit],
    )
    return [_row_to_memory(row) for row in rows.fetchall()]


def _search_like(query: str, *, scope: str | None, kind: str | None, limit: int) -> list[MemoryItem]:
    terms = _search_terms(query)
    if not terms:
        return list_memories(scope=scope, kind=kind, limit=limit)
    clauses = ["(" + " or ".join(["content like ?"] * len(terms)) + ")"]
    params: list[Any] = [f"%{term}%" for term in terms]
    if scope:
        clauses.append("scope = ?")
        params.append(scope)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    rows = _connect().execute(
        f"""
        select id, scope, kind, key, content, metadata_json, created_at, updated_at
        from memories
        where {' and '.join(clauses)}
        order by updated_at desc, id desc
        limit ?
        """,
        [*params, limit],
    )
    return [_row_to_memory(row) for row in rows.fetchall()]


def _search_query(text: str) -> str:
    terms = _search_terms(text)
    return " OR ".join(f'"{term}"' for term in terms[:8])


def _search_terms(text: str) -> list[str]:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", str(text or "").lower())
    terms = [term for term in normalized.split() if len(term) >= 2]
    for token in list(terms):
        if re.search(r"[\u4e00-\u9fff]", token) and len(token) > 2:
            terms.extend(token[index : index + 2] for index in range(0, len(token) - 1))
            if len(token) > 3:
                terms.extend(token[index : index + 3] for index in range(0, len(token) - 2))
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            result.append(term)
    return result


def _compact(text: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."

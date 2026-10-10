"""Daily logical export and restore (BUILD_ALL C2 "Backups"; PRD §12; `make restore-drill`).

Format: gzip of JSON lines. Line 1 is a header {format, alembic_revision, exported_at, tables:
{name: rows}}; then one line per row {"t": table, "r": {column: value}} in foreign-key order.
Values that JSON can't carry are tagged ({"$uuid": ...}, {"$dt": ...}, {"$d": ...}, {"$tm": ...},
{"$dec": ...}). Restore runs into an empty database migrated to the same revision, in one
transaction, and checks every table's row count against the header.

Neon keeps its own point-in-time restore; this export is the copy we control, and the drill
proves it can be loaded.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import uuid
from collections.abc import Iterator
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.models import Base

FORMAT = "kitchentab-export/1"
SKIP = {"test_inbox", "rate_limit_hits", "idempotency_keys"}  # disposable, short-lived rows
BATCH = 500


def _encode(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return {"$uuid": str(value)}
    if isinstance(value, datetime):
        return {"$dt": value.isoformat()}
    if isinstance(value, date):
        return {"$d": value.isoformat()}
    if isinstance(value, time):
        return {"$tm": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$dec": str(value)}
    if isinstance(value, bytes):
        raise TypeError("binary columns are not exported")
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and len(value) == 1:
        [(tag, raw)] = value.items()
        if tag == "$uuid":
            return uuid.UUID(raw)
        if tag == "$dt":
            return datetime.fromisoformat(raw)
        if tag == "$d":
            return date.fromisoformat(raw)
        if tag == "$tm":
            return time.fromisoformat(raw)
        if tag == "$dec":
            return Decimal(raw)
    return value


def tables() -> list[Any]:
    return [t for t in Base.metadata.sorted_tables if t.name not in SKIP]


async def _revision(conn: AsyncConnection) -> str:
    return str(await conn.scalar(text("SELECT version_num FROM alembic_version")))


async def export(conn: AsyncConnection, now: datetime) -> tuple[bytes, dict[str, Any]]:
    """A consistent snapshot: run inside REPEATABLE READ (the caller's transaction)."""
    await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    counts: dict[str, int] = {}
    lines: list[str] = []
    for table in tables():
        cols = [c.name for c in table.columns]
        order = [table.c[c.name] for c in table.primary_key.columns] or [table.c[cols[0]]]
        result = await conn.stream(select(table).order_by(*order))
        n = 0
        async for row in result:
            record = {k: _encode(v) for k, v in zip(cols, row, strict=True)}
            lines.append(json.dumps({"t": table.name, "r": record}, separators=(",", ":"), sort_keys=True))
            n += 1
        counts[table.name] = n
    header = {
        "format": FORMAT,
        "alembic_revision": await _revision(conn),
        "exported_at": now.isoformat(),
        "tables": counts,
    }
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write((json.dumps(header, sort_keys=True) + "\n").encode())
        for line in lines:
            gz.write(line.encode() + b"\n")
    data = buf.getvalue()
    return data, {**header, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _read(data: bytes) -> tuple[dict[str, Any], Iterator[tuple[str, dict[str, Any]]]]:
    raw = gzip.decompress(data).decode().splitlines()
    header = json.loads(raw[0])
    if header.get("format") != FORMAT:
        raise ValueError(f"not a KitchenTab export: {header.get('format')!r}")

    def rows() -> Iterator[tuple[str, dict[str, Any]]]:
        for line in raw[1:]:
            item = json.loads(line)
            yield item["t"], {k: _decode(v) for k, v in item["r"].items()}

    return header, rows()


async def restore(conn: AsyncConnection, data: bytes) -> dict[str, int]:
    """Load an export into an empty database at the same Alembic revision (one transaction)."""
    header, rows = _read(data)
    revision = await _revision(conn)
    if revision != header["alembic_revision"]:
        raise ValueError(f"export is at {header['alembic_revision']}, database at {revision}: migrate first")
    by_name = {t.name: t for t in tables()}
    for name, table in by_name.items():
        existing = await conn.scalar(select(func.count()).select_from(table))
        if existing:
            raise ValueError(f"{name} is not empty: restore only into a fresh database")
    current: str | None = None
    batch: list[dict[str, Any]] = []
    for name, row in rows:  # the file is in foreign-key order: flush whenever the table changes
        if name != current or len(batch) >= BATCH:
            if batch and current is not None:
                await conn.execute(by_name[current].insert(), batch)
            current, batch = name, []
        batch.append(row)
    if batch and current is not None:
        await conn.execute(by_name[current].insert(), batch)
    counts = {}
    for name, table in by_name.items():
        counts[name] = int(await conn.scalar(select(func.count()).select_from(table)) or 0)
    expected = {k: v for k, v in header["tables"].items() if k in by_name}
    if counts != expected:
        raise ValueError(f"row counts differ after restore: {counts} != {expected}")
    return counts


def export_name(now: datetime) -> str:
    return f"backups/kitchentab-{now:%Y-%m-%d}.jsonl.gz"


async def export_to_storage(sessions: async_sessionmaker[AsyncSession], storage: Any, now: datetime) -> dict[str, Any]:
    """The daily logical export (BUILD_ALL C2 "Backups"): one gzip per day under `backups/`, private,
    overwritten if the cron runs twice that day (idempotent, VERCEL-09)."""
    async with sessions() as db, db.begin():
        conn = await db.connection()
        data, meta = await export(conn, now)
    name = export_name(now)
    await storage.put(name, data, "application/gzip")
    return {"name": name, "bytes": meta["bytes"], "rows": sum(meta["tables"].values())}

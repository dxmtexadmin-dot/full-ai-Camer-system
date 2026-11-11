"""In-place, additive schema migration for databases created by older releases.

Why this exists
---------------
The project bootstraps its schema with ``Base.metadata.create_all``, which only
creates *missing tables*. A table that already exists never gains new columns,
so an ``inventory.db`` created before the QR-inventory release would be missing
the responsibility columns added to ``equipment_assets``.

The migration defined here upgrades such a database in place:

* it never drops a table and never rewrites existing rows;
* every step is guarded by ``PRAGMA table_info`` so it is safe to run on each
  startup (idempotent);
* the SQLite file is copied to ``data/backups/`` before the first change;
* applied steps are recorded in ``schema_migrations``;
* each step can be rolled back with ``ALTER TABLE ... DROP COLUMN``.

Run manually when needed::

    python -m app.services.schema_migration --status
    python -m app.services.schema_migration --apply
    python -m app.services.schema_migration --rollback 001_equipment_asset_profile
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import PROJECT_ROOT, Settings, get_settings

logger = logging.getLogger(__name__)

HISTORY_TABLE = "schema_migrations"

#: File-name prefix used for automatic backups.
BACKUP_PREFIX = "pre-migration-"


@dataclass(frozen=True)
class ColumnSpec:
    """One column that may be missing on an older database.

    ``ddl`` must stay a *constant* default: SQLite refuses non-constant
    defaults (``CURRENT_TIMESTAMP``, ``datetime('now')``) in ``ALTER TABLE ADD
    COLUMN``.
    """

    name: str
    ddl: str
    index: Optional[str] = None


@dataclass(frozen=True)
class Migration:
    id: str
    description: str
    table: str
    columns: Sequence[ColumnSpec] = field(default_factory=tuple)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        id="001_equipment_asset_profile",
        description="QR inventar: qurilma profili va mas'ul xodim ustunlari",
        table="equipment_assets",
        columns=(
            ColumnSpec("name", "VARCHAR(160) DEFAULT ''"),
            ColumnSpec("room", "VARCHAR(80) DEFAULT ''"),
            ColumnSpec("department", "VARCHAR(120) DEFAULT ''"),
            ColumnSpec(
                "responsible_employee_id",
                "INTEGER REFERENCES employees(id) ON DELETE SET NULL",
                index="ix_equipment_assets_responsible_employee_id",
            ),
            ColumnSpec("responsible_name", "VARCHAR(160) DEFAULT ''"),
            ColumnSpec("responsible_position", "VARCHAR(120) DEFAULT ''"),
            ColumnSpec(
                "technician_employee_id",
                "INTEGER REFERENCES employees(id) ON DELETE SET NULL",
                index="ix_equipment_assets_technician_employee_id",
            ),
            ColumnSpec("technician_name", "VARCHAR(160) DEFAULT ''"),
            ColumnSpec("purchase_date", "DATE"),
            ColumnSpec("is_active", "BOOLEAN NOT NULL DEFAULT 1"),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def sqlite_path(engine: Engine) -> Optional[Path]:
    """Return the on-disk database file, or ``None`` for memory/non-SQLite URLs."""
    url = engine.url
    if not url.drivername.startswith("sqlite"):
        return None
    database = url.database or ""
    if database in {"", ":memory:"}:
        return None
    path = Path(database)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_history_table(conn) -> None:  # noqa: ANN001
    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
                id VARCHAR(64) PRIMARY KEY,
                description VARCHAR(255) NOT NULL DEFAULT '',
                applied_at VARCHAR(32) NOT NULL,
                columns_added TEXT NOT NULL DEFAULT ''
            )
            """
        )
    )


def _existing_tables(conn) -> set[str]:  # noqa: ANN001
    rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    return {str(row[0]) for row in rows}


def _table_columns(conn, table: str) -> set[str]:  # noqa: ANN001
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {str(row[1]) for row in rows}


def _table_indexes(conn, table: str) -> set[str]:  # noqa: ANN001
    rows = conn.execute(text(f"PRAGMA index_list({table})")).fetchall()
    return {str(row[1]) for row in rows}


def _applied_ids(conn) -> dict[str, str]:  # noqa: ANN001
    rows = conn.execute(text(f"SELECT id, applied_at FROM {HISTORY_TABLE}")).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def backup_database(engine: Engine, settings: Optional[Settings] = None) -> Optional[Path]:
    """Copy the SQLite file (plus WAL/SHM) before the first schema change.

    Returns the backup path, or ``None`` when there is nothing to back up.
    """
    path = sqlite_path(engine)
    if path is None or not path.exists():
        return None

    settings = settings or get_settings()
    backup_dir = settings.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{BACKUP_PREFIX}{stamp}-{path.name}"

    # sqlite3.Connection.backup() is safer than copying while WAL is in use.
    source = sqlite3.connect(str(path))
    try:
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, target.with_name(target.name + suffix[:4]))
    logger.info("Database backup written to %s", target)
    return target


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def pending_migrations(engine: Engine) -> list[Migration]:
    """Migrations whose columns are not fully present yet."""
    if not engine.url.drivername.startswith("sqlite"):
        # Non-SQLite deployments are expected to run a real migration tool.
        return []
    with engine.begin() as conn:
        tables = _existing_tables(conn)
        pending: list[Migration] = []
        for migration in MIGRATIONS:
            if migration.table not in tables:
                continue  # create_all will build it with every column
            present = _table_columns(conn, migration.table)
            if any(column.name not in present for column in migration.columns):
                pending.append(migration)
        return pending


def apply_migrations(
    engine: Engine,
    settings: Optional[Settings] = None,
    *,
    backup: bool = True,
) -> list[str]:
    """Bring an existing database up to date. Returns applied migration ids.

    Safe to call on every startup: a fresh database (or an already migrated
    one) yields an empty list and no backup is taken.
    """
    if not engine.url.drivername.startswith("sqlite"):
        logger.debug("Skipping additive migration: %s is not SQLite", engine.url)
        return []

    pending = pending_migrations(engine)
    if not pending:
        return []

    if backup:
        backup_database(engine, settings)

    applied: list[str] = []
    with engine.begin() as conn:
        _ensure_history_table(conn)
        tables = _existing_tables(conn)
        for migration in pending:
            if migration.table not in tables:
                continue
            present = _table_columns(conn, migration.table)
            indexes = _table_indexes(conn, migration.table)
            added: list[str] = []
            for column in migration.columns:
                if column.name not in present:
                    conn.execute(
                        text(f"ALTER TABLE {migration.table} ADD COLUMN {column.name} {column.ddl}")
                    )
                    added.append(column.name)
                    logger.info("Migration %s: added %s.%s", migration.id, migration.table, column.name)
                if column.index and column.index not in indexes:
                    conn.execute(
                        text(f"CREATE INDEX IF NOT EXISTS {column.index} ON {migration.table} ({column.name})")
                    )
            conn.execute(
                text(
                    f"INSERT OR REPLACE INTO {HISTORY_TABLE} (id, description, applied_at, columns_added) "
                    "VALUES (:id, :description, :applied_at, :columns_added)"
                ),
                {
                    "id": migration.id,
                    "description": migration.description,
                    "applied_at": _now_iso(),
                    "columns_added": ",".join(added),
                },
            )
            applied.append(migration.id)
    return applied


def rollback_migration(engine: Engine, migration_id: str, *, backup: bool = True) -> list[str]:
    """Drop the columns added by ``migration_id`` (SQLite 3.35+).

    Existing rows keep every other value; only the added columns disappear.
    Returns the dropped column names.
    """
    migration = next((item for item in MIGRATIONS if item.id == migration_id), None)
    if migration is None:
        raise ValueError(f"Noma'lum migratsiya: {migration_id}")
    if not engine.url.drivername.startswith("sqlite"):
        raise RuntimeError("Rollback faqat SQLite uchun qo'llanadi")

    dropped: list[str] = []
    with engine.begin() as conn:
        tables = _existing_tables(conn)
        if migration.table not in tables:
            return dropped
        if backup:
            logger.info("Rolling back %s (backup requested)", migration_id)
        present = _table_columns(conn, migration.table)
        indexes = _table_indexes(conn, migration.table)
        for column in migration.columns:
            if column.index and column.index in indexes:
                conn.execute(text(f"DROP INDEX IF EXISTS {column.index}"))
                indexes.discard(column.index)
            if column.name in present:
                conn.execute(text(f"ALTER TABLE {migration.table} DROP COLUMN {column.name}"))
                dropped.append(column.name)
                present.discard(column.name)
        if HISTORY_TABLE in tables:
            conn.execute(text(f"DELETE FROM {HISTORY_TABLE} WHERE id = :id"), {"id": migration_id})
    if dropped:
        logger.warning("Rolled back %s: dropped %s", migration_id, ", ".join(dropped))
    return dropped


def migration_status(engine: Engine) -> list[dict]:
    """Human-readable overview used by the CLI and by tests."""
    if not engine.url.drivername.startswith("sqlite"):
        return []
    with engine.begin() as conn:
        _ensure_history_table(conn)
        tables = _existing_tables(conn)
        applied = _applied_ids(conn)
        status: list[dict] = []
        for migration in MIGRATIONS:
            present = _table_columns(conn, migration.table) if migration.table in tables else set()
            missing = [column.name for column in migration.columns if column.name not in present]
            status.append(
                {
                    "id": migration.id,
                    "description": migration.description,
                    "table": migration.table,
                    "applied_at": applied.get(migration.id),
                    "missing_columns": missing,
                    "pending": bool(missing) and migration.table in tables,
                }
            )
        return status


def _main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="QR inventar sxema migratsiyasi")
    parser.add_argument("--status", action="store_true", help="migratsiya holatini ko'rsatish")
    parser.add_argument("--apply", action="store_true", help="migratsiyani qo'llash")
    parser.add_argument("--rollback", metavar="ID", help="migratsiyani qaytarish")
    parser.add_argument("--no-backup", action="store_true", help="backup olmaslik (tavsiya etilmaydi)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()
    from app.database import get_engine

    engine = get_engine()
    settings.ensure_directories()

    if args.rollback:
        dropped = rollback_migration(engine, args.rollback, backup=not args.no_backup)
        print(f"Rollback {args.rollback}: {len(dropped)} ustun o'chirildi")
        return 0

    if args.apply:
        applied = apply_migrations(engine, settings, backup=not args.no_backup)
        print(f"Qo'llandi: {', '.join(applied) if applied else 'hech narsa (allaqachon yangi)'}")
        return 0

    for row in migration_status(engine):
        state = "PENDING" if row["pending"] else ("APPLIED" if row["applied_at"] else "OK")
        print(f"[{state}] {row['id']} - {row['description']}")
        if row["missing_columns"]:
            print(f"    yetishmayotgan ustunlar: {', '.join(row['missing_columns'])}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())

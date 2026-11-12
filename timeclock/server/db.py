"""Database layer. Uses SQLite locally and Postgres in the cloud (set DATABASE_URL)."""
import os
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, MetaData, String, Table, UniqueConstraint, create_engine, func, select,
)
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

metadata = MetaData()

employees = Table(
    "employees", metadata,
    Column("user_id", String(32), primary_key=True),
    Column("device_name", String(128), nullable=False, default=""),   # name stored on the clock
    Column("display_name", String(128), nullable=True),               # optional override set in the dashboard
    Column("updated_at", String(32), nullable=False),
)

punches = Table(
    "punches", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(32), nullable=False, index=True),
    Column("ts", String(19), nullable=False, index=True),   # clock-local time "YYYY-MM-DD HH:MM:SS"
    Column("status", Integer, nullable=False, default=0),
    Column("punch", Integer, nullable=False, default=0),
    Column("device_sn", String(64), nullable=False, default=""),
    UniqueConstraint("user_id", "ts", "device_sn", name="uq_punch"),
)

devices = Table(
    "devices", metadata,
    Column("device_sn", String(64), primary_key=True),
    Column("name", String(128), nullable=False, default=""),
    Column("firmware", String(128), nullable=False, default=""),
    Column("last_seen", String(32), nullable=False),        # UTC ISO time of last agent sync
    Column("last_error", String(512), nullable=True),
)


def _normalize_url(url: str) -> str:
    # Hosting providers often hand out "postgres://"; SQLAlchemy + psycopg 3 wants this form.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def make_engine(url: str | None = None):
    url = _normalize_url(url or os.environ.get("DATABASE_URL", "sqlite:///./timeclock.db"))
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool  # one shared in-memory DB (used by tests)
    engine = create_engine(url, **kwargs)
    metadata.create_all(engine)
    return engine


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _insert(engine, table):
    return (pg_insert if engine.dialect.name == "postgresql" else sqlite_insert)(table)


def upsert_device(conn, engine, sn: str, name: str, firmware: str, error: str | None):
    values = dict(device_sn=sn, name=name, firmware=firmware, last_seen=utcnow_iso(), last_error=error)
    stmt = _insert(engine, devices).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["device_sn"],
        set_={k: stmt.excluded[k] for k in ("name", "firmware", "last_seen", "last_error")},
    )
    conn.execute(stmt)


def upsert_employees(conn, engine, rows: list[dict]):
    if not rows:
        return
    now = utcnow_iso()
    stmt = _insert(engine, employees).values(
        [dict(user_id=r["user_id"], device_name=r.get("name") or "", display_name=None, updated_at=now) for r in rows]
    )
    # Refresh the clock's name but never overwrite a name the admin typed in the dashboard.
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id"],
        set_={"device_name": stmt.excluded.device_name, "updated_at": stmt.excluded.updated_at},
    )
    conn.execute(stmt)


def insert_punches(conn, engine, rows: list[dict], device_sn: str) -> int:
    """Insert punches, silently skipping ones already stored. Returns number of new rows."""
    if not rows:
        return 0
    count = select(func.count()).select_from(punches)
    before = conn.execute(count).scalar_one()
    stmt = _insert(engine, punches).values(
        [dict(user_id=r["user_id"], ts=r["ts"], status=int(r.get("status", 0)),
              punch=int(r.get("punch", 0)), device_sn=device_sn) for r in rows]
    ).on_conflict_do_nothing(index_elements=["user_id", "ts", "device_sn"])
    conn.execute(stmt)
    return conn.execute(count).scalar_one() - before

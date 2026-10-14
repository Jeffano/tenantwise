"""Async Postgres connection pool.

Why a pool: opening a TLS connection to Neon on every request adds latency.
Why prepare_threshold=None: Neon's pooled endpoint runs PgBouncer in transaction mode,
which breaks server side prepared statements. Disabling them avoids random errors.
"""
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pgvector.psycopg import register_vector_async

from app.config import get_settings

_pool: AsyncConnectionPool | None = None


async def _configure(conn: AsyncConnection) -> None:
    await register_vector_async(conn)


async def init_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=get_settings().database_url,
            min_size=0,          # scale to zero friendly: hold no idle connections
            max_size=5,
            open=False,
            configure=_configure,
            kwargs={"prepare_threshold": None, "row_factory": dict_row, "autocommit": True},
        )
        await _pool.open()
    return _pool


def pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised. Call init_pool() first.")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

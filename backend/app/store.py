"""Database reads and writes for caching, rate limiting, budgets, logs and stats."""
import json
from datetime import datetime, timedelta, timezone

from app import db
from app.config import get_settings


async def get_cached(question_hash: str) -> dict | None:
    async with db.pool().connection() as conn:
        return await (await conn.execute(
            "SELECT answer, citations, model FROM answer_cache WHERE question_hash = %s AND expires_at > now()",
            (question_hash,),
        )).fetchone()


async def put_cached(question_hash: str, answer: str, citations: list[dict], model: str) -> None:
    expires = datetime.now(timezone.utc) + timedelta(days=get_settings().cache_ttl_days)
    async with db.pool().connection() as conn:
        await conn.execute(
            """INSERT INTO answer_cache (question_hash, answer, citations, model, expires_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (question_hash) DO UPDATE
               SET answer = EXCLUDED.answer, citations = EXCLUDED.citations,
                   model = EXCLUDED.model, created_at = now(), expires_at = EXCLUDED.expires_at""",
            (question_hash, answer, json.dumps(citations), model, expires),
        )


async def requests_last_hour(client_hash: str) -> int:
    """Rate limit state lives in Postgres, so it is shared across Cloud Run instances."""
    async with db.pool().connection() as conn:
        row = await (await conn.execute(
            "SELECT count(*) AS n FROM query_logs WHERE client_hash = %s AND created_at > now() - interval '1 hour'",
            (client_hash,),
        )).fetchone()
    return int(row["n"])


async def spend_today() -> float:
    async with db.pool().connection() as conn:
        row = await (await conn.execute(
            "SELECT COALESCE(sum(cost_usd), 0) AS total FROM query_logs WHERE created_at >= date_trunc('day', now())"
        )).fetchone()
    return float(row["total"])


async def log_query(**fields) -> int:
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["%s"] * len(fields))
    async with db.pool().connection() as conn:
        row = await (await conn.execute(
            f"INSERT INTO query_logs ({cols}) VALUES ({placeholders}) RETURNING id",
            tuple(fields.values()),
        )).fetchone()
    return int(row["id"])


async def set_feedback(log_id: int, value: int, client_hash: str) -> bool:
    # Only the client that asked can rate the answer.
    async with db.pool().connection() as conn:
        cur = await conn.execute(
            "UPDATE query_logs SET feedback = %s WHERE id = %s AND client_hash = %s",
            (value, log_id, client_hash),
        )
    return cur.rowcount == 1


async def stats(days: int = 30) -> dict:
    async with db.pool().connection() as conn:
        overall = await (await conn.execute(
            """SELECT count(*) AS queries,
                      count(DISTINCT client_hash) AS unique_users,
                      avg(CASE WHEN cache_hit THEN 1.0 ELSE 0.0 END) AS cache_hit_rate,
                      avg(CASE WHEN NOT in_scope THEN 1.0 ELSE 0.0 END) AS out_of_scope_rate,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
                      percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
                      avg(cost_usd) AS avg_cost_usd,
                      sum(cost_usd) AS total_cost_usd,
                      avg(CASE WHEN citations_valid THEN 1.0 WHEN citations_valid IS NOT NULL THEN 0.0 END) AS citation_valid_rate,
                      count(feedback) AS feedback_count,
                      avg(CASE WHEN feedback = 1 THEN 1.0 WHEN feedback = -1 THEN 0.0 END) AS helpful_rate
               FROM query_logs WHERE created_at > now() - make_interval(days => %s)""",
            (days,),
        )).fetchone()
        routes = await (await conn.execute(
            """SELECT route, count(*) AS n, avg(cost_usd) AS avg_cost
               FROM query_logs WHERE created_at > now() - make_interval(days => %s)
               GROUP BY route ORDER BY n DESC""",
            (days,),
        )).fetchall()
        daily = await (await conn.execute(
            """SELECT to_char(date_trunc('day', created_at), 'YYYY-MM-DD') AS day, count(*) AS queries,
                      sum(cost_usd) AS cost
               FROM query_logs WHERE created_at > now() - make_interval(days => %s)
               GROUP BY 1 ORDER BY 1""",
            (days,),
        )).fetchall()

    def clean(d):
        return {k: (float(v) if v is not None and not isinstance(v, (int, str)) else v) for k, v in d.items()}

    return {"window_days": days, "overall": clean(overall),
            "routes": [clean(r) for r in routes], "daily": [clean(r) for r in daily]}

"""Hybrid retrieval: vector search + keyword search, merged with Reciprocal Rank Fusion.

Why hybrid: tenant questions mix meaning ("my landlord won't fix the heat") with exact
identifiers ("N12", "section 82"). Embeddings handle the first, keyword search the second.
Why RRF: it merges ranked lists using only positions, so we never have to calibrate
cosine similarity against ts_rank scores, which live on different scales.
Why OR keywords: plainto_tsquery ANDs every word, so a long natural question almost never
matches. Replacing & with | makes it "match any important word" and lets ts_rank_cd sort.
"""
import asyncio
from dataclasses import dataclass, asdict

from app import db
from app.config import get_settings
from app.embeddings import embed_query

HYBRID_SQL = """
WITH vec AS (
    SELECT id, sim, ROW_NUMBER() OVER (ORDER BY dist) AS r
    FROM (
        SELECT id, embedding <=> %(emb)s AS dist, 1 - (embedding <=> %(emb)s) AS sim
        FROM chunks
        ORDER BY embedding <=> %(emb)s
        LIMIT %(n)s
    ) v
),
q AS (
    SELECT to_tsquery('english', replace(plainto_tsquery('english', %(q)s)::text, '&', '|')) AS query
),
kw AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY rank DESC) AS r
    FROM (
        SELECT c.id, ts_rank_cd(c.tsv, q.query) AS rank
        FROM chunks c, q
        WHERE c.tsv @@ q.query
        ORDER BY rank DESC
        LIMIT %(n)s
    ) k
)
SELECT c.id, c.section_ref, c.heading_path, c.url, c.text, s.title AS source_title,
       COALESCE(1.0 / (%(rrf_k)s + vec.r), 0) + COALESCE(1.0 / (%(rrf_k)s + kw.r), 0) AS rrf,
       vec.sim AS similarity
FROM chunks c
JOIN sources s ON s.id = c.source_id
LEFT JOIN vec ON vec.id = c.id
LEFT JOIN kw ON kw.id = c.id
WHERE vec.id IS NOT NULL OR kw.id IS NOT NULL
ORDER BY rrf DESC
LIMIT %(k)s
"""

VECTOR_ONLY_SQL = """
SELECT c.id, c.section_ref, c.heading_path, c.url, c.text, s.title AS source_title,
       0.0 AS rrf, 1 - (c.embedding <=> %(emb)s) AS similarity
FROM chunks c JOIN sources s ON s.id = c.source_id
ORDER BY c.embedding <=> %(emb)s
LIMIT %(k)s
"""


@dataclass
class Chunk:
    id: str
    section_ref: str
    heading_path: str
    url: str
    text: str
    source_title: str
    rrf: float
    similarity: float | None

    def to_public(self) -> dict:
        d = asdict(self)
        d["rrf"] = round(float(self.rrf), 5)
        d["similarity"] = None if self.similarity is None else round(float(self.similarity), 4)
        return d


@dataclass
class RetrievalResult:
    chunks: list[Chunk]
    top_similarity: float   # best vector similarity, used by the scope check


async def retrieve(question: str, k: int | None = None, mode: str = "hybrid") -> RetrievalResult:
    s = get_settings()
    k = k or s.top_k
    # The embedding model is synchronous CPU work; run it off the event loop.
    emb = await asyncio.to_thread(embed_query, question)
    sql = HYBRID_SQL if mode == "hybrid" else VECTOR_ONLY_SQL
    params = {"emb": emb, "q": question, "n": s.candidates_per_search, "k": k, "rrf_k": s.rrf_k}
    async with db.pool().connection() as conn:
        rows = await (await conn.execute(sql, params)).fetchall()
        # The top vector similarity may belong to a chunk RRF ranked lower, so fetch it directly.
        top = await (await conn.execute(
            "SELECT 1 - (embedding <=> %(emb)s) AS sim FROM chunks ORDER BY embedding <=> %(emb)s LIMIT 1",
            {"emb": emb},
        )).fetchone()
    chunks = [Chunk(**{**r, "rrf": float(r["rrf"]),
                       "similarity": None if r["similarity"] is None else float(r["similarity"])})
              for r in rows]
    return RetrievalResult(chunks=chunks, top_similarity=float(top["sim"]) if top else 0.0)


async def get_section(section_ref: str) -> list[Chunk]:
    async with db.pool().connection() as conn:
        rows = await (await conn.execute(
            """SELECT c.id, c.section_ref, c.heading_path, c.url, c.text, s.title AS source_title,
                      0.0 AS rrf, NULL::float AS similarity
               FROM chunks c JOIN sources s ON s.id = c.source_id
               WHERE lower(c.section_ref) = lower(%(ref)s) ORDER BY c.id""",
            {"ref": section_ref.strip()},
        )).fetchall()
    return [Chunk(**{**r, "rrf": 0.0}) for r in rows]

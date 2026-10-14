"""TenantWise API.

Request pipeline for /api/ask (cheapest checks first, the LLM last):
  validate -> redact PII -> rate limit -> answer cache -> daily budget -> retrieve
  -> scope check -> route -> stream answer (SSE) -> validate citations -> cache -> log
"""
import json
import logging
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import db, store
from app.config import get_settings
from app.embeddings import warm_up
from app.generation import GenerationState, stream_answer
from app.guardrails import (client_hash, is_in_scope, question_hash, redact_pii,
                            validate_citations, validate_question)
from app.retrieval import get_section, retrieve
from app.router import route

# Structured JSON logs: Cloud Logging parses these into searchable fields for free.
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
log = logging.getLogger("tenantwise")


def jlog(event: str, **fields) -> None:
    log.info(json.dumps({"event": event, **fields}, default=str))


OUT_OF_SCOPE_MESSAGE = (
    "I can only answer questions about residential tenancies in Ontario, such as rent increases, "
    "repairs, evictions, deposits and Landlord and Tenant Board forms. Try rephrasing your question, "
    "or contact the Landlord and Tenant Board at tribunalsontario.ca/ltb."
)
BUDGET_MESSAGE = (
    "TenantWise has reached its daily usage limit, so new answers are paused until tomorrow. "
    "Common questions still work from the cache, and you can search the law directly at ontario.ca/laws."
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    warm_up()  # load the embedding model before the first request
    jlog("startup")
    yield
    await db.close_pool()


app = FastAPI(title="TenantWise API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().allowed_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def client_ip(request: Request) -> str:
    # Cloud Run sits behind Google's front end, which sets X-Forwarded-For.
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class AskRequest(BaseModel):
    question: str = Field(..., max_length=2000)


class FeedbackRequest(BaseModel):
    log_id: int
    value: int = Field(..., ge=-1, le=1)


def public_citations(chunks, cited_numbers):
    out = []
    for n in cited_numbers:
        if 1 <= n <= len(chunks):
            c = chunks[n - 1]
            out.append({"n": n, "section_ref": c.section_ref, "heading_path": c.heading_path,
                        "url": c.url, "source_title": c.source_title})
    return out


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/ask")
async def ask(body: AskRequest, request: Request):
    started = time.perf_counter()
    error = validate_question(body.question)
    if error:
        raise HTTPException(status_code=400, detail=error)

    # Redact before anything else so personal details never reach logs, the database, or the LLM.
    question = redact_pii(body.question.strip())
    chash = client_hash(client_ip(request))
    s = get_settings()

    if await store.requests_last_hour(chash) >= s.rate_limit_per_hour:
        raise HTTPException(status_code=429, detail="You have reached the hourly question limit. Please try again later.")

    qhash = question_hash(question)

    async def events():
        base = {"client_hash": chash, "question_redacted": question}

        def elapsed():
            return int((time.perf_counter() - started) * 1000)

        # 1. Exact match cache: repeated questions cost nothing.
        cached = await store.get_cached(qhash)
        if cached:
            yield sse("meta", {"route": "cached"})
            yield sse("token", cached["answer"])
            log_id = await store.log_query(**base, in_scope=True, cache_hit=True, route="cached",
                                           model=cached["model"], latency_ms=elapsed(), citations_valid=True)
            yield sse("done", {"citations": cached["citations"], "log_id": log_id, "cached": True})
            return

        # 2. Daily budget cap: degrade gracefully instead of running up a bill.
        if await store.spend_today() >= s.daily_budget_usd:
            yield sse("meta", {"route": "budget"})
            yield sse("token", BUDGET_MESSAGE)
            log_id = await store.log_query(**base, in_scope=True, route="budget", latency_ms=elapsed())
            yield sse("done", {"citations": [], "log_id": log_id})
            return

        # 3. Retrieve, then the free scope check.
        result = await retrieve(question)
        if not is_in_scope(result.top_similarity):
            yield sse("meta", {"route": "refused"})
            yield sse("token", OUT_OF_SCOPE_MESSAGE)
            log_id = await store.log_query(**base, in_scope=False, route="refused",
                                           route_reason="below_scope_threshold", latency_ms=elapsed(),
                                           top_similarity=result.top_similarity)
            jlog("ask", route="refused", top_similarity=result.top_similarity, latency_ms=elapsed())
            yield sse("done", {"citations": [], "log_id": log_id})
            return

        # 4. Route and stream.
        decision = route(question, result.chunks)
        yield sse("meta", {"route": decision.tier})
        state = GenerationState()
        try:
            async for delta in stream_answer(question, result.chunks, decision.model, state):
                yield sse("token", delta)
        except Exception as exc:  # network errors, API overload after retries
            jlog("generation_error", error=str(exc))
            yield sse("error", "Something went wrong while generating the answer. Please try again.")
            return

        # 5. Deterministic citation check, then cache only answers that pass it.
        valid, cited = validate_citations(state.text, len(result.chunks))
        citations = public_citations(result.chunks, cited)
        if valid and cited:
            await store.put_cached(qhash, state.text, citations, decision.model)

        u = state.usage
        log_id = await store.log_query(
            **base, in_scope=True, route=decision.tier, route_reason=decision.reason, model=u.model,
            input_tokens=u.input_tokens, output_tokens=u.output_tokens, cache_read_tokens=u.cache_read_tokens,
            cost_usd=u.cost_usd, latency_ms=elapsed(), top_similarity=result.top_similarity,
            citations_valid=valid,
        )
        jlog("ask", route=decision.tier, reason=decision.reason, model=u.model, cost_usd=u.cost_usd,
             input_tokens=u.input_tokens, output_tokens=u.output_tokens, latency_ms=elapsed(),
             citations_valid=valid)
        yield sse("done", {"citations": citations, "citations_valid": valid, "log_id": log_id})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/feedback")
async def feedback(body: FeedbackRequest, request: Request):
    if body.value == 0:
        raise HTTPException(status_code=400, detail="Feedback must be 1 or -1.")
    ok = await store.set_feedback(body.log_id, body.value, client_hash(client_ip(request)))
    return {"ok": ok}


# Search endpoints used by the MCP server. No LLM call, so they cost nothing to serve.
_search_hits: dict[str, list[float]] = {}


def _search_rate_limit(request: Request, limit: int = 60) -> None:
    """Simple per instance limiter. Good enough for a free read only endpoint."""
    key = client_hash(client_ip(request))
    now = time.time()
    hits = [t for t in _search_hits.get(key, []) if now - t < 60]
    if len(hits) >= limit:
        raise HTTPException(status_code=429, detail="Too many search requests. Slow down.")
    hits.append(now)
    _search_hits[key] = hits


@app.get("/api/search")
async def search(q: str, request: Request, k: int = 6):
    _search_rate_limit(request)
    if not q.strip() or len(q) > 1000:
        raise HTTPException(status_code=400, detail="Query must be between 1 and 1000 characters.")
    result = await retrieve(redact_pii(q), k=max(1, min(k, 10)))
    return {"top_similarity": result.top_similarity,
            "in_scope": is_in_scope(result.top_similarity),
            "results": [c.to_public() for c in result.chunks]}


@app.get("/api/section")
async def section(ref: str, request: Request):
    _search_rate_limit(request)
    chunks = await get_section(ref)
    if not chunks:
        raise HTTPException(status_code=404, detail=f"No section found for '{ref}'.")
    return {"section_ref": chunks[0].section_ref, "url": chunks[0].url,
            "heading_path": chunks[0].heading_path, "text": "\n\n".join(c.text for c in chunks)}


@app.get("/api/stats")
async def stats(days: int = 30):
    return await store.stats(max(1, min(days, 365)))

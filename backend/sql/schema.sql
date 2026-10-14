-- TenantWise schema. One Postgres database does vectors, keyword search, caching and logs,
-- which keeps the stack free (Neon free tier) and simple to operate.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS sources (
    id            SERIAL PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    retrieved_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id            TEXT PRIMARY KEY,              -- stable id: <source slug>:<section>:<part>
    source_id     INT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    section_ref   TEXT NOT NULL,                 -- what users see and what evals match, e.g. "RTA s. 116"
    heading_path  TEXT NOT NULL,                 -- e.g. "Part VII > Rules Relating to Rent > Notice of rent increase required"
    url           TEXT NOT NULL,
    text          TEXT NOT NULL,
    content_hash  TEXT NOT NULL,                 -- lets ingestion skip unchanged chunks
    -- Generated column: Postgres keeps the keyword index in sync automatically.
    tsv           tsvector GENERATED ALWAYS AS (
                      to_tsvector('english', heading_path || ' ' || section_ref || ' ' || text)
                  ) STORED,
    embedding     vector(384) NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW: fast approximate nearest neighbour search with good recall and no training step.
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
-- GIN: the standard index for full text search.
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_section_ref ON chunks (section_ref);

CREATE TABLE IF NOT EXISTS answer_cache (
    question_hash TEXT PRIMARY KEY,
    answer        TEXT NOT NULL,
    citations     JSONB NOT NULL,
    model         TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS query_logs (
    id                 BIGSERIAL PRIMARY KEY,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    client_hash        TEXT NOT NULL,           -- salted hash of the IP, never the raw IP
    question_redacted  TEXT NOT NULL,           -- PII stripped before it ever reaches the database
    in_scope           BOOLEAN NOT NULL,
    cache_hit          BOOLEAN NOT NULL DEFAULT false,
    route              TEXT,                    -- small | large | refused | cached | budget
    route_reason       TEXT,
    model              TEXT,
    input_tokens       INT NOT NULL DEFAULT 0,
    output_tokens      INT NOT NULL DEFAULT 0,
    cache_read_tokens  INT NOT NULL DEFAULT 0,
    cost_usd           NUMERIC(10, 6) NOT NULL DEFAULT 0,
    latency_ms         INT,
    top_similarity     REAL,
    citations_valid    BOOLEAN,
    feedback           SMALLINT                 -- 1 helpful, -1 not helpful
);

CREATE INDEX IF NOT EXISTS query_logs_client_time ON query_logs (client_hash, created_at);
CREATE INDEX IF NOT EXISTS query_logs_time ON query_logs (created_at);

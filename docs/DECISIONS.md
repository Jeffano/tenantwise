# Design Decisions

Each entry records what was chosen, why, and what was traded away. These are also your interview notes.

## 1. Domain: Ontario residential tenancy law
Small, public, authoritative corpus with numbered sections, which makes precise citations possible. Real users exist and are easy to reach. High stakes, so evaluation and guardrails matter. Unrelated to rail or data platforms, which shows range.

## 2. One Postgres database (Neon) for vectors, keyword search, cache and logs
At a few thousand chunks, pgvector with HNSW is fast enough. One database means one bill (zero on the free tier), one connection pool, and trivial analytics joins. Tradeoff: at millions of chunks or very high query rates, a dedicated vector engine would be worth revisiting.

## 3. Local embeddings (bge small, fastembed, ONNX on CPU)
Zero cost per query, no third party in the request path, 384 dimensions keep the index small, and the model is baked into the image so cold starts never download it. Tradeoff: slightly below the best hosted embedding models; the eval decides whether that matters.

## 4. Structure aware chunking by legal section
The section is the unit of meaning and of citation. Long sections split on paragraph boundaries and repeat the heading path. Content hashes make reingestion incremental and idempotent.

## 5. Hybrid retrieval with Reciprocal Rank Fusion
Embeddings capture meaning; full text search captures identifiers such as N12 or section numbers. RRF merges ranks without calibrating different score scales. Keyword terms are ORed rather than ANDed, because ANDing every word of a natural question almost never matches. A reranker is optional and added only if the eval shows it helps.

## 6. Free scope check before any LLM call
If the best vector similarity is below a threshold tuned on the golden set, return a canned reply. Off topic and abuse requests cost nothing, and the model is never asked to answer from outside the corpus.

## 7. Rules based routing between Haiku and Sonnet
Free, instant, explainable, and logged with a reason. Escalation triggers: multiple sub questions, scenarios with dates or amounts, cross referencing across several close sections, very long questions. Validated by comparing routed, small only and large only on the golden set. The logs become training data for a learned router later.

## 8. Numbered citations plus a deterministic validator
Sources are labelled [1] to [k]. After generation, code checks every cited number points at a retrieved chunk, and an answer without citations is only valid if it is the explicit "not covered" reply. Only answers that pass are cached.

## 9. Exact match answer cache instead of semantic caching
Legal questions that look similar can have different answers. Exact match on a normalized question is safe; the cache key includes the prompt version and model names so changes invalidate it automatically. Ingestion clears the cache when the law changes.

## 10. Privacy and abuse controls
PII is redacted before logging, before the database and before the model. IPs are stored only as salted hashes. Rate limiting state lives in Postgres so it holds across instances. Spend is capped in four layers: Anthropic console limit, GCP budget alert, app daily budget with graceful degradation, and the Cloud Run instance cap.

## 11. Evaluation as a release gate
Hand written golden set across standard, scenario and out of scope categories. Free deterministic retrieval metrics run on every push; the paid LLM judge runs only on pull requests that touch prompts, retrieval or routing, with results cached. The judge is calibrated against human labels.

## 12. MCP server as a thin client over the public search API
No secrets, so it is safe to publish on PyPI. No LLM calls, so MCP users cost almost nothing. Same retrieval as the website, so quality is identical. Tradeoff versus mounting MCP inside the API: one extra network hop, in exchange for a much simpler and safer backend.

## 13. Serverless deployment on Cloud Run, frontend on Vercel
Scale to zero means near zero idle cost; the tradeoff is cold starts, reduced by a baked model and startup CPU boost. Workload Identity Federation means no long lived cloud keys in GitHub. Deploys only run after CI passes, followed by a health check.

## Interview prompts to rehearse
1. Walk me through a request from keystroke to answer.
2. Why hybrid search, and how did you prove it helped?
3. How do you prevent and detect hallucinations?
4. How does routing work, and what would a learned router look like?
5. How do you know your LLM judge is reliable?
6. What stops someone from running up your bill?
7. What are the tradeoffs of scale to zero?
8. How do you keep the data current without reprocessing everything?
9. Why is your MCP server a separate package?
10. What would you change at a thousand times the traffic?

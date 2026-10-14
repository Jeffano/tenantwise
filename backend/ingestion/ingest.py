"""Ingestion: fetch sources, chunk them, embed only what changed, and upsert into Postgres.

Usage (from the backend folder):
    python -m ingestion.ingest --dry-run         # parse and print samples, no database writes
    python -m ingestion.ingest                   # full incremental ingest
    python -m ingestion.ingest --only rta        # one source

Why incremental: every chunk carries a content hash. On a rerun, unchanged chunks are
skipped, so monthly refreshes are fast, cheap and idempotent (safe to run twice).
"""
import argparse
import io
import sys
from pathlib import Path

import httpx
import psycopg
import yaml
from bs4 import BeautifulSoup
from pgvector.psycopg import register_vector
from pypdf import PdfReader

from app.config import get_settings
from app.embeddings import embed_passages
from ingestion.parsers import RawChunk, clean_lines, parse_headed_page, parse_plain_text, parse_statute

HERE = Path(__file__).parent
HEADERS = {"User-Agent": "TenantWise ingestion bot (portfolio project; contact jeffanojohn@gmail.com)"}


def load_bytes(src: dict) -> bytes:
    if src.get("file"):
        return (HERE / "raw" / src["file"]).read_bytes()
    r = httpx.get(src["url"], headers=HEADERS, timeout=60, follow_redirects=True)
    r.raise_for_status()
    return r.content


def main_content(soup: BeautifulSoup):
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "form"]):
        tag.decompose()
    return soup.find("main") or soup.find(id="content") or soup.body or soup


def parse_source(src: dict) -> list[RawChunk]:
    data = load_bytes(src)
    kind, prefix = src["type"], src["ref_prefix"]
    if kind == "pdf":
        reader = PdfReader(io.BytesIO(data))
        text = "\f".join((page.extract_text() or "") for page in reader.pages)
        return parse_plain_text(text, prefix)

    soup = BeautifulSoup(data, "html.parser")
    root = main_content(soup)
    if kind == "statute":
        return parse_statute(clean_lines(root.get_text("\n")), prefix)
    if kind == "page":
        blocks = []
        for el in root.find_all(["h2", "h3", "h4", "p", "li"]):
            text = " ".join(el.get_text(" ").split())
            if not text:
                continue
            if el.name == "li" and el.find_parent("li"):
                continue  # nested list items are already inside their parent's text
            blocks.append(("h" if el.name.startswith("h") else "p", text))
        return parse_headed_page(blocks, prefix)
    raise ValueError(f"Unknown source type: {kind}")


def chunk_id(slug: str, c: RawChunk, index_in_section: int) -> str:
    key = c.section_ref.replace(" ", "")[:60]
    return f"{slug}:{key}:{index_in_section:03d}"


def assign_ids(slug: str, chunks: list[RawChunk]) -> list[tuple[str, RawChunk]]:
    counts: dict[str, int] = {}
    out = []
    for c in chunks:
        n = counts.get(c.section_ref, 0)
        counts[c.section_ref] = n + 1
        out.append((chunk_id(slug, c, n), c))
    return out


def ingest(only: str | None, dry_run: bool, config_path: Path = HERE / "sources.yaml") -> None:
    config = yaml.safe_load(config_path.read_text())
    sources = [s for s in config["sources"] if not only or s["slug"] == only]

    conn = None
    if not dry_run:
        conn = psycopg.connect(get_settings().database_url, autocommit=False, prepare_threshold=None)
        conn.execute((HERE.parent / "sql" / "schema.sql").read_text())  # idempotent schema
        conn.commit()
        register_vector(conn)

    total_new = total_skipped = total_deleted = 0
    for src in sources:
        print(f"\n== {src['slug']} ({src['type']})")
        try:
            chunks = assign_ids(src["slug"], parse_source(src))
        except Exception as exc:
            print(f"   FAILED to fetch or parse: {exc}")
            print("   Tip: save the page manually into ingestion/raw/ and set `file:` in sources.yaml")
            continue

        sections = {c.section_ref for _, c in chunks}
        print(f"   {len(chunks)} chunks across {len(sections)} sections")
        if src["type"] == "statute" and len(sections) < 20:
            print("   WARNING: very few sections parsed. Inspect the samples below and adjust parsers.py.")
        if dry_run:
            for cid, c in chunks[:3] + chunks[len(chunks) // 2: len(chunks) // 2 + 2]:
                print(f"   [{cid}] {c.section_ref} | {c.heading_path}\n      {c.text[:220]}...")
            continue

        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sources (slug, title, url, retrieved_at) VALUES (%s, %s, %s, now())
                   ON CONFLICT (slug) DO UPDATE SET title = EXCLUDED.title, url = EXCLUDED.url,
                   retrieved_at = now() RETURNING id""",
                (src["slug"], src["title"], src.get("url") or src.get("file")),
            )
            source_id = cur.fetchone()[0]
            cur.execute("SELECT id, content_hash FROM chunks WHERE source_id = %s", (source_id,))
            existing = dict(cur.fetchall())

            changed = [(cid, c) for cid, c in chunks if existing.get(cid) != c.content_hash]
            stale = set(existing) - {cid for cid, _ in chunks}
            print(f"   {len(changed)} new or changed, {len(chunks) - len(changed)} unchanged, {len(stale)} removed")

            if changed:
                # Embed heading + reference + text so short chunks still carry their context.
                vectors = embed_passages([f"{c.heading_path}\n{c.section_ref}\n{c.text}" for _, c in changed])
                url = src.get("url") or ""
                cur.executemany(
                    """INSERT INTO chunks (id, source_id, section_ref, heading_path, url, text, content_hash, embedding, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                       ON CONFLICT (id) DO UPDATE SET section_ref = EXCLUDED.section_ref,
                         heading_path = EXCLUDED.heading_path, url = EXCLUDED.url, text = EXCLUDED.text,
                         content_hash = EXCLUDED.content_hash, embedding = EXCLUDED.embedding, updated_at = now()""",
                    [(cid, source_id, c.section_ref, c.heading_path, url, c.text, c.content_hash, v)
                     for (cid, c), v in zip(changed, vectors)],
                )
            if stale:
                cur.execute("DELETE FROM chunks WHERE id = ANY(%s)", (list(stale),))
            # Changed law means cached answers may be stale.
            if changed or stale:
                cur.execute("DELETE FROM answer_cache")
        conn.commit()  # one transaction per source: a failure never leaves a half updated source
        total_new += len(changed)
        total_skipped += len(chunks) - len(changed)
        total_deleted += len(stale)

    if conn:
        conn.close()
        print(f"\nDone. embedded={total_new} unchanged={total_skipped} removed={total_deleted}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="slug of a single source")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--config", type=Path, default=HERE / "sources.yaml")
    args = parser.parse_args()
    ingest(args.only, args.dry_run, args.config)
    sys.exit(0)

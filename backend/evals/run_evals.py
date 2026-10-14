"""Evaluation suite.

Modes:
  --mode retrieval   Free and deterministic. Hit rate at k, MRR, scope accuracy.
                     Runs on every push in CI.
  --mode full        Generates answers and scores them with an LLM judge.
                     Runs on pull requests that touch prompts, retrieval or routing.
Options:
  --strategy routed|small|large   Compare routing against single model baselines.
  --retrieval hybrid|vector       Measure what hybrid search actually buys you.
  --calibrate evals/human_labels.jsonl   Judge agreement with your own labels.
  --sweep-scope                    Print the best scope threshold for your corpus.
Exit code 1 if any threshold fails, which fails the CI job.
"""
import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

from app import db
from app.config import get_settings
from app.generation import SYSTEM_PROMPT, client, generate_answer
from app.guardrails import NOT_COVERED, validate_citations
from app.retrieval import retrieve
from app.router import route

HERE = Path(__file__).parent
CACHE_DIR = HERE / ".cache"
REPORTS = HERE / "reports"

JUDGE_PROMPT = """You are grading an answer from a legal information assistant.

<sources>
{sources}
</sources>
<question>{question}</question>
<answer>{answer}</answer>
<key_facts>{key_facts}</key_facts>

Grade strictly:
- faithful: true only if EVERY factual claim in the answer is supported by the sources.
- key_facts_covered: how many of the key facts the answer states correctly (integer).
- refused: true if the answer declines or says the sources do not cover the question.

Respond with only a JSON object: {{"faithful": bool, "key_facts_covered": int, "refused": bool, "reason": "one sentence"}}"""


def load_golden() -> list[dict]:
    return [json.loads(l) for l in (HERE / "golden_set.jsonl").read_text().splitlines() if l.strip()]


def first_hit_rank(chunks, expected: list[str]) -> int | None:
    exp = {e.lower() for e in expected}
    for i, c in enumerate(chunks, start=1):
        if c.section_ref.lower() in exp:
            return i
    return None


async def eval_retrieval(golden, mode: str, k: int):
    s = get_settings()
    rows = []
    for item in golden:
        res = await retrieve(item["question"], k=k, mode=mode)
        rank = first_hit_rank(res.chunks, item["expected_sections"]) if item["expected_sections"] else None
        rows.append({"id": item["id"], "category": item["category"], "rank": rank,
                     "top_similarity": res.top_similarity,
                     "retrieved": [c.section_ref for c in res.chunks]})
    in_scope = [r for r in rows if r["category"] != "out_of_scope"]
    oos = [r for r in rows if r["category"] == "out_of_scope"]
    hit = sum(1 for r in in_scope if r["rank"]) / max(1, len(in_scope))
    mrr = sum((1 / r["rank"]) if r["rank"] else 0 for r in in_scope) / max(1, len(in_scope))
    scope_correct = (sum(1 for r in in_scope if r["top_similarity"] >= s.scope_threshold)
                     + sum(1 for r in oos if r["top_similarity"] < s.scope_threshold))
    return {"hit_rate": hit, "mrr": mrr, "scope_accuracy": scope_correct / max(1, len(rows)),
            "misses": [r for r in in_scope if not r["rank"]], "rows": rows}


def sweep_scope(rows):
    best = (0, 0.0)
    for t in [x / 100 for x in range(30, 90)]:
        correct = sum(1 for r in rows if (r["category"] != "out_of_scope") == (r["top_similarity"] >= t))
        if correct > best[0]:
            best = (correct, t)
    return {"best_threshold": best[1], "accuracy": best[0] / max(1, len(rows))}


def cache_key(item, strategy, retrieval_mode) -> str:
    s = get_settings()
    raw = json.dumps([item, strategy, retrieval_mode, SYSTEM_PROMPT, s.small_model, s.large_model,
                      s.judge_model, s.top_k, s.scope_threshold], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


async def judge(question, answer, chunks, key_facts) -> dict:
    sources = "\n\n".join(f"[{i}] {c.section_ref}\n{c.text}" for i, c in enumerate(chunks, start=1))
    msg = await client().messages.create(
        model=get_settings().judge_model, max_tokens=300, temperature=0,
        messages=[{"role": "user", "content": JUDGE_PROMPT.format(
            sources=sources, question=question, answer=answer, key_facts=json.dumps(key_facts))}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"faithful": False, "key_facts_covered": 0, "refused": False, "reason": f"unparseable: {text[:80]}"}


async def eval_full(golden, strategy: str, retrieval_mode: str):
    s = get_settings()
    CACHE_DIR.mkdir(exist_ok=True)
    rows = []
    for item in golden:
        cpath = CACHE_DIR / f"{cache_key(item, strategy, retrieval_mode)}.json"
        if cpath.exists():  # unchanged config + question: reuse result, costs nothing
            rows.append(json.loads(cpath.read_text()))
            continue
        t0 = time.perf_counter()
        res = await retrieve(item["question"], mode=retrieval_mode)
        if res.top_similarity < s.scope_threshold:
            row = {"id": item["id"], "category": item["category"], "route": "refused", "answer": "",
                   "refused": True, "faithful": True, "key_facts_covered": 0, "citations_valid": True,
                   "cost_usd": 0.0, "latency_ms": int((time.perf_counter() - t0) * 1000)}
        else:
            force = None if strategy == "routed" else strategy
            decision = route(item["question"], res.chunks, force=force)
            gen = await generate_answer(item["question"], res.chunks, decision.model)
            latency = int((time.perf_counter() - t0) * 1000)
            valid, _ = validate_citations(gen.text, len(res.chunks))
            verdict = await judge(item["question"], gen.text, res.chunks, item["key_facts"])
            refused = verdict.get("refused", False) or NOT_COVERED.lower() in gen.text.lower()
            row = {"id": item["id"], "category": item["category"], "route": decision.tier,
                   "route_reason": decision.reason, "answer": gen.text, "refused": refused,
                   "faithful": bool(verdict.get("faithful")),
                   "key_facts_covered": int(verdict.get("key_facts_covered", 0)),
                   "key_facts_total": len(item["key_facts"]), "judge_reason": verdict.get("reason", ""),
                   "citations_valid": valid, "cost_usd": gen.usage.cost_usd, "latency_ms": latency}
        cpath.write_text(json.dumps(row))
        rows.append(row)

    answered = [r for r in rows if r["category"] != "out_of_scope"]
    oos = [r for r in rows if r["category"] == "out_of_scope"]
    facts_total = sum(r.get("key_facts_total", 0) for r in answered) or 1
    lat = sorted(r["latency_ms"] for r in rows)
    return {
        "faithfulness": sum(r["faithful"] for r in answered) / max(1, len(answered)),
        "key_fact_coverage": sum(min(r["key_facts_covered"], r.get("key_facts_total", 0)) for r in answered) / facts_total,
        "citation_valid_rate": sum(r["citations_valid"] for r in answered) / max(1, len(answered)),
        "refusal_accuracy": sum(r["refused"] for r in oos) / max(1, len(oos)),
        "false_refusal_rate": sum(r["refused"] for r in answered) / max(1, len(answered)),
        "avg_cost_usd": statistics.mean(r["cost_usd"] for r in rows),
        "p50_latency_ms": lat[len(lat) // 2],
        "p95_latency_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))],
        "route_split": {t: sum(1 for r in rows if r["route"] == t) for t in {r["route"] for r in rows}},
        "rows": rows,
    }


def calibrate(full_rows, labels_path: Path):
    labels = {j["id"]: j["faithful"] for j in map(json.loads, labels_path.read_text().splitlines()) if j}
    pairs = [(r["faithful"], labels[r["id"]]) for r in full_rows if r["id"] in labels]
    agree = sum(1 for a, b in pairs if a == b)
    return {"labelled": len(pairs), "agreement": agree / max(1, len(pairs))}


def check(metrics: dict, mode: str, baseline: dict | None) -> list[str]:
    t = json.loads((HERE / "thresholds.json").read_text())
    fails = []
    if mode == "retrieval":
        if metrics["hit_rate"] < t["retrieval_hit_rate_min"]:
            fails.append(f"hit_rate {metrics['hit_rate']:.3f} < {t['retrieval_hit_rate_min']}")
        if baseline and metrics["hit_rate"] < baseline["hit_rate"] - t["max_hit_rate_drop"]:
            fails.append(f"hit_rate dropped from {baseline['hit_rate']:.3f} to {metrics['hit_rate']:.3f}")
    else:
        for key, th in [("faithfulness", "faithfulness_min"), ("key_fact_coverage", "key_fact_coverage_min"),
                        ("citation_valid_rate", "citation_valid_rate_min"), ("refusal_accuracy", "refusal_accuracy_min")]:
            if metrics[key] < t[th]:
                fails.append(f"{key} {metrics[key]:.3f} < {t[th]}")
    return fails


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["retrieval", "full"], default="retrieval")
    p.add_argument("--strategy", choices=["routed", "small", "large"], default="routed")
    p.add_argument("--retrieval", choices=["hybrid", "vector"], default="hybrid")
    p.add_argument("--k", type=int, default=6)
    p.add_argument("--calibrate", type=Path)
    p.add_argument("--sweep-scope", action="store_true")
    p.add_argument("--no-gate", action="store_true", help="report only, never fail")
    args = p.parse_args()

    await db.init_pool()
    golden = load_golden()
    REPORTS.mkdir(exist_ok=True)
    try:
        if args.mode == "retrieval":
            metrics = await eval_retrieval(golden, args.retrieval, args.k)
            if args.sweep_scope:
                metrics["scope_sweep"] = sweep_scope(metrics["rows"])
            name = f"retrieval_{args.retrieval}"
        else:
            metrics = await eval_full(golden, args.strategy, args.retrieval)
            if args.calibrate:
                metrics["judge_calibration"] = calibrate(metrics["rows"], args.calibrate)
            name = f"full_{args.strategy}_{args.retrieval}"
    finally:
        await db.close_pool()

    baseline_path = REPORTS / f"{name}_baseline.json"
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    (REPORTS / f"{name}_latest.json").write_text(json.dumps(metrics, indent=2, default=str))

    summary = {k: v for k, v in metrics.items() if k not in ("rows", "misses")}
    print(json.dumps(summary, indent=2, default=str))
    if args.mode == "retrieval" and metrics["misses"]:
        print("\nMisses (expected section not retrieved):")
        for m in metrics["misses"]:
            print(f"  {m['id']}: got {m['retrieved']}")

    fails = [] if args.no_gate else check(metrics, args.mode, baseline)
    if fails:
        print("\nEVAL GATE FAILED:\n  " + "\n  ".join(fails))
        sys.exit(1)
    print("\nEval gate passed.")


if __name__ == "__main__":
    asyncio.run(main())

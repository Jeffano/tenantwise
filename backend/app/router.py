"""Rules based model router.

Why rules first: they are free, instant and explainable, and every decision is logged
with its reason. Those logs become training data for a learned router later.
Default is the small model; we escalate only when the question needs more reasoning.
"""
import re
from dataclasses import dataclass

from app.config import get_settings
from app.retrieval import Chunk

_SCENARIO = re.compile(
    r"(\$\s?\d|\d+\s?%|\b\d+\s*(?:day|days|week|weeks|month|months|year|years)\b|"
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b|"
    r"\b(?:what if|if i|if my|can i still|am i owed|how much)\b)",
    re.IGNORECASE,
)
_MULTI = re.compile(r"\b(?:and also|also,|another question|second question|as well as)\b", re.IGNORECASE)


@dataclass
class RouteDecision:
    tier: str        # "small" or "large"
    model: str
    reason: str


def route(question: str, chunks: list[Chunk], force: str | None = None) -> RouteDecision:
    s = get_settings()
    if force in ("small", "large"):
        return RouteDecision(force, s.small_model if force == "small" else s.large_model, f"forced:{force}")

    reasons = []
    if question.count("?") >= 2 or _MULTI.search(question):
        reasons.append("multi_part")
    if len(_SCENARIO.findall(question)) >= 2:
        reasons.append("scenario_with_specifics")
    if chunks:
        top = chunks[0].rrf
        close = {c.section_ref for c in chunks if top and c.rrf >= 0.8 * top}
        if len(close) >= 3:
            reasons.append("cross_reference")
    if len(question.split()) > 80:
        reasons.append("long_question")

    if reasons:
        return RouteDecision("large", s.large_model, "+".join(reasons))
    return RouteDecision("small", s.small_model, "default_simple")

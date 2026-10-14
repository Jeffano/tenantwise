"""Guardrails: PII redaction, scope check, input hardening, citation validation.

All of these are deterministic and free. The expensive model is the last line of
defence, not the first.
"""
import hashlib
import re

from app.config import get_settings

# PII patterns. Renters paste their own details ("I live at 12 King St, unit 4...").
# We never store those, which is privacy by design rather than privacy by policy.
_PII_PATTERNS = [
    (re.compile(r"[\w.+]+@[\w]+(?:\.[\w]+)+"), "[EMAIL]"),
    (re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b[A-Za-z]\d[A-Za-z][ ]?\d[A-Za-z]\d\b"), "[POSTAL_CODE]"),
    (re.compile(
        r"\b\d{1,5}\s+(?:[A-Za-z0-9']+\s){1,4}"
        r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Boulevard|Blvd|Court|Crt|Ct|Crescent|Cres|"
        r"Lane|Ln|Way|Place|Pl|Trail|Terrace|Parkway|Pkwy|Circle|Cir)\b\.?",
        re.IGNORECASE), "[ADDRESS]"),
    (re.compile(r"\b(?:unit|apt|apartment|suite)\s*#?\s*\d+\w?\b", re.IGNORECASE), "[UNIT]"),
]


def redact_pii(text: str) -> str:
    for pattern, token in _PII_PATTERNS:
        text = pattern.sub(token, text)
    return text


def client_hash(ip: str) -> str:
    """Salted hash so we can rate limit per client without storing IP addresses."""
    salt = get_settings().ip_hash_salt
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:16]


def normalize_question(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def question_hash(text: str) -> str:
    s = get_settings()
    # Prompt version and model names are part of the key so a prompt change invalidates the cache.
    key = f"{s.prompt_version}|{s.small_model}|{s.large_model}|{normalize_question(text)}"
    return hashlib.sha256(key.encode()).hexdigest()


def is_in_scope(top_similarity: float) -> bool:
    """If nothing in the corpus is close to the question, do not call the LLM at all."""
    return top_similarity >= get_settings().scope_threshold


def validate_question(text: str) -> str | None:
    """Returns an error message, or None if the input is acceptable."""
    if not text or not text.strip():
        return "Please type a question."
    if len(text) > 1200:
        return "Please keep your question under 1,200 characters."
    return None


def wrap_user_question(text: str) -> str:
    """Delimit untrusted input so the model treats it as data, not instructions.
    Strip anything that looks like our own tags to prevent tag injection."""
    cleaned = re.sub(r"</?\s*(question|sources|source)[^>]*>", "", text, flags=re.IGNORECASE)
    return f"<question>\n{cleaned.strip()}\n</question>"


_CITATION = re.compile(r"\[(\d{1,2})\]")
NOT_COVERED = "The sources I have do not cover this question."


def extract_citations(answer: str) -> list[int]:
    return sorted({int(n) for n in _CITATION.findall(answer)})


def validate_citations(answer: str, num_sources: int) -> tuple[bool, list[int]]:
    """Every cited number must point at a chunk we actually retrieved.
    An answer with no citations is only valid if it is the explicit 'not covered' reply."""
    cited = extract_citations(answer)
    if not cited:
        return (NOT_COVERED.lower() in answer.lower()), []
    valid = all(1 <= n <= num_sources for n in cited)
    return valid, cited

"""Pure parsing and chunking functions (no network, no database) so they are unit testable.

Why structure aware chunking: in legal text the section is the unit of meaning and the
unit people cite. Fixed size windows would cut rules in half and point citations at the
wrong place. Long sections are split, but each piece repeats the heading path for context.
"""
import hashlib
import re
from dataclasses import dataclass

MAX_WORDS = 450        # roughly 600 tokens
TARGET_WORDS = 300     # window size when we must split

# e.g. "116 (1) A landlord shall not..." or "48.1 A landlord shall..." at the start of a line
_SECTION_START = re.compile(r"^(\d{1,3}(?:\.\d{1,2})?)\s+(?:\(1\)\s+)?(?=[A-Z“\"(])")
_PART = re.compile(r"^PART\s+([IVXLC]+(?:\.\d+)?)\b", re.IGNORECASE)
_NOISE = re.compile(
    r"^(Section Amendments with date in force|Skip to|Français|Print|Download|Versions|"
    r"Back to top|Table of Contents|\[?\d{4}, c\. \d+.*\]$)",
    re.IGNORECASE,
)


@dataclass
class RawChunk:
    section_ref: str
    heading_path: str
    text: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.section_ref}|{self.heading_path}|{self.text}".encode()).hexdigest()


def clean_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line and not _NOISE.match(line):
            lines.append(line)
    return lines


def _is_headnote(line: str) -> bool:
    return (len(line) < 110 and not line.endswith((".", ";", ":", ","))
            and not _SECTION_START.match(line) and not line.startswith("("))


def split_long(text: str, max_words: int = MAX_WORDS, target: int = TARGET_WORDS) -> list[str]:
    """Split on paragraph boundaries so pieces stay readable."""
    words = text.split()
    if len(words) <= max_words:
        return [text]
    paras = [p for p in text.split("\n") if p.strip()]
    pieces, current = [], []
    for p in paras:
        if current and len(" ".join(current).split()) + len(p.split()) > target:
            pieces.append("\n".join(current))
            current = []
        current.append(p)
    if current:
        pieces.append("\n".join(current))
    # A single giant paragraph: fall back to word windows.
    final = []
    for piece in pieces:
        w = piece.split()
        if len(w) <= max_words:
            final.append(piece)
        else:
            final.extend(" ".join(w[i:i + target]) for i in range(0, len(w), target))
    return final


def parse_statute(lines: list[str], ref_prefix: str) -> list[RawChunk]:
    """Walk the statute line by line, tracking Part titles and section headnotes."""
    chunks: list[RawChunk] = []
    part = ""
    pending_headnote = ""
    current_num, current_head, buf = None, "", []

    def flush():
        if current_num and buf:
            path = " > ".join(x for x in (part, current_head) if x)
            body = "\n".join(buf)
            for piece in split_long(body):
                chunks.append(RawChunk(f"{ref_prefix} s. {current_num}", path, piece))

    i = 0
    while i < len(lines):
        line = lines[i]
        m_part = _PART.match(line)
        if m_part:
            flush()
            current_num, buf = None, []
            title = lines[i + 1] if i + 1 < len(lines) and lines[i + 1].isupper() else ""
            part = f"Part {m_part.group(1).upper()}" + (f" {title.title()}" if title else "")
            i += 2 if title else 1
            continue
        m_sec = _SECTION_START.match(line)
        if m_sec:
            flush()
            current_num = m_sec.group(1)
            current_head = pending_headnote
            pending_headnote = ""
            buf = [line]
        elif _is_headnote(line) and i + 1 < len(lines) and (
                _SECTION_START.match(lines[i + 1]) or lines[i + 1].startswith("(")):
            # A short title line directly before a section or subsection is a headnote.
            if _SECTION_START.match(lines[i + 1]):
                pending_headnote = line
            elif current_num:
                buf.append(line)
        elif current_num:
            buf.append(line)
        i += 1
    flush()
    return chunks


def parse_headed_page(blocks: list[tuple[str, str]], ref_prefix: str) -> list[RawChunk]:
    """blocks: (kind, text) where kind is 'h' for headings and 'p' for body text."""
    chunks, heading, buf = [], "Overview", []

    def flush():
        if buf:
            for piece in split_long("\n".join(buf)):
                chunks.append(RawChunk(f"{ref_prefix}: {heading}", heading, piece))

    for kind, text in blocks:
        if kind == "h":
            flush()
            heading, buf = text, []
        else:
            buf.append(text)
    flush()
    return chunks


def parse_plain_text(text: str, ref_prefix: str) -> list[RawChunk]:
    """For PDFs: paragraph windows with a page aware reference."""
    chunks = []
    for page_no, page in enumerate(text.split("\f"), start=1):
        body = "\n".join(clean_lines(page))
        if not body:
            continue
        for piece in split_long(body):
            chunks.append(RawChunk(f"{ref_prefix} p. {page_no}", ref_prefix, piece))
    return chunks

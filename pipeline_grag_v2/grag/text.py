"""Text normalization, character chunking, and lexical tokenization."""

from __future__ import annotations

import re
from html import unescape
from typing import Iterable, List


SPACE_RE = re.compile(r"\s+")
LINE_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'/-]*")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "i", "in", "is", "it", "of", "on", "or", "that", "the",
    "this", "to", "was", "what", "when", "where", "which", "who", "will",
    "with", "you", "your",
}

# These terms are intentionally removed only from lexical matching. They remain
# in the complete query passed to the embedding model.
DOMAIN_COMMON_TERMS = {
    "ms", "msads", "applied", "data", "science", "program", "uchicago",
    "university", "chicago", "master", "masters", "master's",
}


def clean_text(text: str) -> str:
    return SPACE_RE.sub(" ", unescape(text or "")).strip()


def clean_multiline_text(text: str) -> str:
    lines = [LINE_SPACE_RE.sub(" ", unescape(line)).strip() for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line).strip()


def tokenize(text: str, *, remove_domain_terms: bool = False) -> List[str]:
    terms = [
        token
        for token in (m.group(0).lower() for m in TOKEN_RE.finditer(text or ""))
        if token not in STOPWORDS and len(token) > 1
    ]
    if remove_domain_terms:
        terms = [term for term in terms if term not in DOMAIN_COMMON_TERMS]
    return terms


def _choose_boundary(text: str, start: int, hard_end: int, target_size: int) -> int:
    """Choose a natural boundary near target_size without exceeding hard_end."""
    if hard_end >= len(text):
        return len(text)
    lower = min(hard_end, start + max(1, int(target_size * 0.62)))
    window = text[lower:hard_end]
    candidates = []
    for pattern in (r"\n\n", r"(?<=[.!?])\s+", r"\n", r"\s+"):
        matches = list(re.finditer(pattern, window))
        if matches:
            candidates.append(lower + matches[-1].end())
            break
    return candidates[-1] if candidates else hard_end


def chunk_characters(
    text: str,
    target_size: int = 900,
    overlap: int = 90,
    min_tail: int = 80,
) -> Iterable[str]:
    """Split text near natural boundaries using a character-based overlap."""
    text = clean_multiline_text(text)
    if not text:
        return
    if len(text) <= target_size:
        yield text
        return

    start = 0
    while start < len(text):
        hard_end = min(len(text), start + target_size)
        end = _choose_boundary(text, start, hard_end, target_size)
        piece = text[start:end].strip()
        if piece:
            yield piece
        if end >= len(text):
            break
        next_start = max(start + 1, end - overlap)
        # Start at a word boundary while preserving approximately the requested overlap.
        match = re.search(r"\s", text[next_start:min(end, next_start + 24)])
        if match:
            next_start += match.end()
        start = next_start

"""
Shared text-processing utilities used by the KG builder, BM25 index, and graph scorer.

Provides clean_text, tokenize, and chunk_words to keep text handling consistent
across the build and retrieval stages.
"""

import re
from html import unescape
from typing import Iterable, List


SPACE_RE = re.compile(r"\s+")
LINE_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'/-]*")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "will",
    "with",
    "you",
    "your",
}


def clean_text(text: str) -> str:
    text = unescape(text or "")
    text = text.replace("\xa0", " ")
    text = SPACE_RE.sub(" ", text)
    return text.strip()


def clean_multiline_text(text: str) -> str:
    text = unescape(text or "")
    text = text.replace("\xa0", " ")
    lines = [LINE_SPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def tokenize(text: str) -> List[str]:
    return [
        token
        for token in (m.group(0).lower() for m in TOKEN_RE.finditer(text or ""))
        if token not in STOPWORDS and len(token) > 1
    ]


def chunk_words(text: str, max_words: int = 180, overlap: int = 35) -> Iterable[str]:
    text = clean_multiline_text(text)
    words = text.split()
    if not words:
        return
    if len(words) <= max_words:
        yield text
        return
    step = max(1, max_words - overlap)
    for start in range(0, len(words), step):
        part = words[start : start + max_words]
        if len(part) < 25 and start:
            break
        yield " ".join(part)

"""
Stage 2 chunker for UChicago MS-ADS RAG chatbot.
Cleaned text sections → metadata-rich chunks in data/chunks/size_N/.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cleaner import clean_page, redact_pii  # noqa: E402

sys.path.insert(0, str(Path(__file__).parents[1] / "scraping"))
from scraper import url_to_filename  # noqa: E402

try:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
except ImportError:
    from langchain_text_splitters import RecursiveCharacterTextSplitter  # type: ignore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")


def _merge_short(splits: list[str], threshold: int = 15) -> list[str]:
    """Merge splits with word_count <= threshold into the preceding split.
    If no preceding split exists yet, carry forward and prepend to the next one.
    """
    result: list[str] = []
    carry = ""
    for text in splits:
        if not text.strip():
            continue
        if carry:
            text = carry + " " + text
            carry = ""
        if len(text.split()) <= threshold:
            if result:
                result[-1] = result[-1] + " " + text
            else:
                carry = text  # no preceding chunk; defer to next
        else:
            result.append(text)
    if carry:  # last chunk was short with no successor
        if result:
            result[-1] = result[-1] + " " + carry
        else:
            result.append(carry)
    return result


def _overview_to_text(data, indent: int = 0) -> str:
    """Recursively convert an accordion overview dict/list to readable natural language."""
    prefix = "  " * indent
    if isinstance(data, list):
        return ", ".join(str(v) for v in data)
    if isinstance(data, dict):
        lines = []
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{prefix}{k}:")
                lines.append(_overview_to_text(v, indent + 1))
            else:
                lines.append(f"{prefix}{k}: {v}")
        return "\n".join(lines)
    return str(data)


def _split_accordion_overview(
    data: dict,
    section: str,
    section_breadcrumb: str,
    max_size: int,
    base_meta: dict,
) -> list[dict]:
    """Split an accordion overview dict into natural-language chunks.

    - Whole overview fits within max_size → one chunk with section/breadcrumb as-is.
    - Oversized → one chunk per top-level key; breadcrumb records the hierarchy:
        section          = top_key
        section_breadcrumb = f"{section_breadcrumb} > {top_key}"
    chunk_id is NOT set here; the caller assigns it.
    """
    def _make(sub_data: dict, sec: str, bc: str) -> dict:
        text = redact_pii(_overview_to_text(sub_data))
        return {
            **base_meta,
            "text":               text,
            "section":            sec,
            "section_breadcrumb": bc,
            "content_type":       "accordion_overview",
            "word_count":         len(text.split()),
        }

    # Use JSON size to decide whether splitting is needed (accurate measure of data volume)
    json_size = len(json.dumps(data, ensure_ascii=False))

    if json_size <= max_size:
        return [_make(data, section, section_breadcrumb)]

    # Split by top-level key; breadcrumb preserves the hierarchy relationship
    chunks: list[dict] = []
    for top_key, top_val in data.items():
        sub    = {top_key: top_val}
        sub_bc = f"{section_breadcrumb} > {top_key}"
        chunks.append(_make(sub, top_key, sub_bc))
    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_page(
    cleaned: dict,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> list[dict]:
    """
    Split a cleaned page record into chunks with full metadata.
    Each chunk is ready to be loaded into ChromaDB.

    chunk_size and chunk_overlap are measured in CHARACTERS (length_function=len).
    For all-MiniLM-L6-v2 (256 token limit), keep chunk_size ≤ 900 chars (~225 tokens).

    url_aliases is stored as a list; serialize to JSON string before passing
    to ChromaDB (which requires scalar metadata values).
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
    )

    url          = cleaned["url"].rstrip("/") + "/"
    url_stem     = url_to_filename(url).removesuffix(".json")
    program_type = cleaned.get("program_type", "general")

    chunks: list[dict] = []
    for block in cleaned["sections"]:
        section_name      = block["section"]
        text              = block["text"]
        content_type      = block["content_type"]
        section_breadcrumb = block.get("section_breadcrumb", section_name)
        block_program_type = block.get("program_type", program_type)
        section_slug      = _slugify(section_name)

        if content_type == "accordion_overview":
            base_meta = {
                "source_url":  url,
                "page_title":  cleaned["page_title"],
                "page_class":  cleaned["page_class"],
                "scraped_at":  cleaned["scraped_at"],
                "url_aliases": cleaned["url_aliases"],
                "program_type": block_program_type,
            }
            sub_chunks = _split_accordion_overview(
                data=text,
                section=section_name,
                section_breadcrumb=section_breadcrumb,
                max_size=chunk_size,
                base_meta=base_meta,
            )
            for sc in sub_chunks:
                sc["chunk_id"] = f"{url_stem}__{_slugify(sc['section'])}__{len(chunks)}"
                chunks.append(sc)

        else:
            for split_text in _merge_short(splitter.split_text(text)):
                if not split_text.strip():
                    continue
                clean_text = redact_pii(split_text)
                chunks.append({
                    "chunk_id":         f"{url_stem}__{section_slug}__{len(chunks)}",
                    "text":             clean_text,
                    "source_url":       url,
                    "page_title":       cleaned["page_title"],
                    "section":          section_name,
                    "section_breadcrumb": section_breadcrumb,
                    "content_type":     content_type,
                    "page_class":       cleaned["page_class"],
                    "word_count":       len(clean_text.split()),
                    "scraped_at":       cleaned["scraped_at"],
                    "url_aliases":      cleaned["url_aliases"],
                    "program_type":     block_program_type,
                })

    # Deduplicate by (section, text) content within this page (first-wins)
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for chunk in chunks:
        key = (chunk["section"], chunk["text"])
        if key not in seen:
            seen.add(key)
            deduped.append(chunk)
    return deduped


def process_all(
    raw_dir: Path = None,
    out_dir: Path = None,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> None:
    """
    Process all raw files → cleaned JSON (data/cleaned/) + chunked JSON (out_dir).

    Cleaned files are always written to data/cleaned/ relative to project root.
    Chunked files go to out_dir (defaults to data/chunks/size_{chunk_size}/).
    """
    base = Path(__file__).resolve().parents[2]
    if raw_dir is None:
        raw_dir = base / "data" / "raw"
    if out_dir is None:
        out_dir = base / "data" / "chunks" / f"size_{chunk_size}"
    raw_dir = Path(raw_dir)
    out_dir = Path(out_dir)

    cleaned_dir = base / "data" / "cleaned"
    cleaned_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_files = sorted(
        f for f in raw_dir.glob("*.json") if f.name != "scrape_errors.jsonl"
    )

    total_chunks = 0
    zero_content: list[str] = []

    for f in raw_files:
        raw = json.loads(f.read_text(encoding="utf-8"))
        cleaned = clean_page(raw)

        (cleaned_dir / f.name).write_text(
            json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        chunks = chunk_page(cleaned, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

        if not chunks:
            zero_content.append(raw.get("url", f.name))

        record = {
            "source_url": cleaned["url"],
            "page_title": cleaned["page_title"],
            "page_class": cleaned["page_class"],
            "chunk_count": len(chunks),
            "chunks": chunks,
        }
        (out_dir / f.name).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        total_chunks += len(chunks)

    print(f"\n=== chunk_size={chunk_size} complete ===")
    print(f"Pages processed : {len(raw_files)}")
    print(f"Total chunks    : {total_chunks}")
    if zero_content:
        print(f"WARNING — {len(zero_content)} page(s) produced 0 chunks:")
        for u in zero_content:
            print(f"  {u}")


# ---------------------------------------------------------------------------
# CLI entry point — produce all four configurations
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    base = Path(__file__).resolve().parents[2]
    raw_dir = base / "data" / "raw"

    for chunk_size, chunk_overlap, out_rel in [
        (512,  51,  "data/chunks/size_512"),
        (900,  90,  "data/chunks/size_900"),
        (1536, 154, "data/chunks/size_1536"),
        (1900, 190, "data/chunks/size_1900"),
    ]:
        process_all(
            raw_dir=raw_dir,
            out_dir=base / out_rel,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

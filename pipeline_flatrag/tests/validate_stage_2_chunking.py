"""
Stage 2 chunking validation.
Run from project root: python tests/validate_stage_2_chunking.py
Validates all four chunk configs in data/chunks/size_{512,900,1536,1900}/.
Exits with code 1 if any check fails.

Checks:
  (1) chunk file count matches raw scraped file count
  (2) every chunk carries all required metadata fields (including new
      program_type and section_breadcrumb added in the retrieval-optimization pass)
  (3) no chunk text contains PII (email/phone, except two whitelisted addresses)
  (4) chunk counts are ordered: size_512 ≥ size_900 ≥ size_1536 ≥ size_1900
  (5) program_type values are restricted to the three allowed values
  (6) in-person and online pages have at least one accordion_overview chunk
"""

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
RAW_DIR = BASE / "data" / "raw"
CHUNKS_DIR = BASE / "data" / "chunks"
CONFIGS = ["size_512", "size_900", "size_1536", "size_1900"]

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
SAFE_EMAILS = frozenset({
    "applieddatascience-advising@uchicago.edu",
    "applieddatascience-admissions@uchicago.edu",
})
REQUIRED_FIELDS = {
    "chunk_id", "text", "source_url", "page_title", "section",
    "section_breadcrumb", "content_type", "page_class", "word_count",
    "scraped_at", "url_aliases", "program_type",
}
VALID_PROGRAM_TYPES = {"in_person", "online", "general"}


def _load_config(config: str) -> list[tuple[str, dict]]:
    config_dir = CHUNKS_DIR / config
    records = []
    for f in sorted(config_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            records.append((f.name, data))
        except Exception as exc:
            print(f"  WARN: could not parse {f.name}: {exc}")
    return records


def check_files(config: str, records: list) -> bool:
    raw_count = len([f for f in RAW_DIR.glob("*.json") if f.name != "scrape_errors.jsonl"])
    if raw_count != len(records):
        print(f"  FAIL {config} check_files: raw={raw_count}, chunks={len(records)}")
        return False
    return True


def check_metadata(config: str, records: list) -> bool:
    bad: list[str] = []
    for name, data in records:
        for chunk in data.get("chunks", []):
            missing = REQUIRED_FIELDS - set(chunk.keys())
            if missing:
                bad.append(f"{name}/{chunk.get('chunk_id', '?')}: missing {missing}")
    if bad:
        print(f"  FAIL {config} check_metadata: {len(bad)} chunk(s) missing fields")
        for b in bad[:3]:
            print(f"    {b}")
        return False
    return True


def check_pii(config: str, records: list) -> bool:
    violations: list[str] = []
    for name, data in records:
        for chunk in data.get("chunks", []):
            text = chunk.get("text", "")
            for m in _EMAIL_RE.finditer(text):
                if m.group(0).lower() not in SAFE_EMAILS:
                    violations.append(
                        f"{name}/{chunk.get('chunk_id', '?')}: email {m.group(0)!r}"
                    )
                    break
            if _PHONE_RE.search(text):
                violations.append(
                    f"{name}/{chunk.get('chunk_id', '?')}: phone number found"
                )
    if violations:
        print(f"  FAIL {config} check_pii: {len(violations)} violation(s)")
        for v in violations[:3]:
            print(f"    {v}")
        return False
    return True


def check_program_types(config: str, records: list) -> bool:
    bad: list[str] = []
    for name, data in records:
        for chunk in data.get("chunks", []):
            pt = chunk.get("program_type", "")
            if pt not in VALID_PROGRAM_TYPES:
                bad.append(f"{name}/{chunk.get('chunk_id', '?')}: program_type={pt!r}")
    if bad:
        print(f"  FAIL {config} check_program_types: {len(bad)} invalid value(s)")
        for b in bad[:3]:
            print(f"    {b}")
        return False
    return True


def check_accordion_overviews(config: str, records: list) -> bool:
    """in-person and online pages must each have ≥ 1 accordion_overview chunk."""
    needs_overview = {"in-person-program", "online-program"}
    found: set[str] = set()
    for _, data in records:
        url = data.get("source_url", "")
        has_ov = any(
            c.get("content_type") == "accordion_overview"
            for c in data.get("chunks", [])
        )
        for key in needs_overview:
            if key in url and has_ov:
                found.add(key)
    missing = needs_overview - found
    if missing:
        print(f"  FAIL {config} check_accordion_overviews: no overview for {missing}")
        return False
    return True


def _config_stats(records: list) -> dict:
    total = sum(len(d.get("chunks", [])) for _, d in records)
    faq_chunks = cp_chunks = 0
    for _, data in records:
        url = data.get("source_url", "")
        if "faqs" in url:
            faq_chunks = len(data.get("chunks", []))
        elif "course-progressions" in url:
            cp_chunks = len(data.get("chunks", []))
    return {"total": total, "faqs": faq_chunks, "courses": cp_chunks}


def main() -> None:
    for config in CONFIGS:
        config_dir = CHUNKS_DIR / config
        if not config_dir.exists():
            print(f"ERROR: {config_dir} does not exist. Run chunker.py first.")
            sys.exit(1)

    results: list[bool] = []
    stats: dict[str, dict] = {}
    checks_per_config = 5  # files, metadata, pii, program_types, accordion_overviews

    for config in CONFIGS:
        records = _load_config(config)
        r_files    = check_files(config, records)
        r_metadata = check_metadata(config, records)
        r_pii      = check_pii(config, records)
        r_pt       = check_program_types(config, records)
        r_ov       = check_accordion_overviews(config, records)
        results.extend([r_files, r_metadata, r_pii, r_pt, r_ov])
        stats[config] = _config_stats(records)

    # Cross-config ordering check
    totals = [stats[c]["total"] for c in CONFIGS]
    ordering_ok = all(totals[i] >= totals[i + 1] for i in range(len(totals) - 1))
    if not ordering_ok:
        print(
            f"  FAIL check_ordering: expected 512 ≥ 900 ≥ 1536 ≥ 1900, "
            f"got {' / '.join(str(t) for t in totals)}"
        )
    results.append(ordering_ok)

    # Summary table
    print(f"\n{'Config':<12} {'Files':>6} {'Chunks':>7} {'FAQs':>6} {'Courses':>8} {'PII':>5} {'PT':>5} {'OV':>5}")
    print("-" * 58)
    raw_count = len([f for f in RAW_DIR.glob("*.json") if f.name != "scrape_errors.jsonl"])
    for idx, config in enumerate(CONFIGS):
        s = stats[config]
        base = idx * checks_per_config
        pii_ok = results[base + 2]
        pt_ok  = results[base + 3]
        ov_ok  = results[base + 4]
        print(
            f"{config:<12} {raw_count:>6} {s['total']:>7} {s['faqs']:>6} "
            f"{s['courses']:>8} {'PASS' if pii_ok else 'FAIL':>5} "
            f"{'PASS' if pt_ok else 'FAIL':>5} {'PASS' if ov_ok else 'FAIL':>5}"
        )

    ordering_label = "PASS" if ordering_ok else "FAIL"
    print(f"\nOrdering (512 ≥ 900 ≥ 1536 ≥ 1900): {ordering_label}")

    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 40}")
    print(f"Results: {passed}/{total} checks passed")
    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
Stage 1 scraping validation.
Run from project root:  python tests/validate_stage_1_scraping.py
Exits with code 1 if any check fails.

Validates that the raw scraper output in data/raw/ is complete and correct:
  (1) all required ("must") URLs from url_class_reference.json are present,
  (2) no raw JSON files are empty,
  (3) every file is valid JSON and contains a non-empty html field,
  (4) all must URLs returned HTTP 200,
  (5) the FAQs page captured ≥ 2500 words (confirming accordion content was not missed),
  (6) the Course Progressions page captured ≥ 1800 words for the same reason,
  (7) none of the must URLs appear in the scrape error log.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "scraping"))
from scraper import extract_main_text

BASE = Path(__file__).resolve().parents[1]
RAW_DIR = BASE / "data" / "raw"
URL_CLASS_REF = BASE / "docs" / "url_class_reference.json"
ERRORS_FILE = RAW_DIR / "scrape_errors.jsonl"

# CLAUDE.md word-count thresholds for accordion pages
WORD_COUNT_THRESHOLDS = {
    "faqs": (
        "https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/faqs",
        2500,
    ),
    "course-progressions": (
        "https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/course-progressions",
        1800,
    ),
}


def _load_records() -> dict[str, dict]:
    records: dict[str, dict] = {}
    for f in RAW_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if "url" in data:
                records[data["url"].rstrip("/")] = data
        except Exception as exc:
            print(f"  WARN: could not parse {f.name}: {exc}")
    return records


def check_1_must_urls_present(records: dict, must_urls: list[str]) -> bool:
    missing = [u for u in must_urls if u.rstrip("/") not in records]
    if missing:
        print(f"FAIL check_1: {len(missing)} must URL(s) not found in data/raw/:")
        for u in missing:
            print(f"  {u}")
        return False
    print(f"PASS check_1: all {len(must_urls)} must URLs present")
    return True


def check_2_no_empty_files() -> bool:
    empty = [f for f in RAW_DIR.glob("*.json") if f.stat().st_size == 0]
    if empty:
        print(f"FAIL check_2: {len(empty)} empty file(s):")
        for f in empty:
            print(f"  {f.name}")
        return False
    print("PASS check_2: no empty files")
    return True


def check_3_valid_json_with_html() -> bool:
    bad: list[str] = []
    for f in RAW_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not data.get("html"):
                bad.append(f"{f.name}: empty html field")
        except json.JSONDecodeError as exc:
            bad.append(f"{f.name}: JSON parse error: {exc}")
    if bad:
        print(f"FAIL check_3: {len(bad)} invalid file(s):")
        for b in bad:
            print(f"  {b}")
        return False
    print("PASS check_3: all files are valid JSON with non-empty html")
    return True


def check_4_all_must_200(records: dict, must_urls: list[str]) -> bool:
    bad = [
        f"{u}: status={records[u.rstrip('/')].get('http_status')}"
        for u in must_urls
        if u.rstrip("/") in records and records[u.rstrip("/")].get("http_status") != 200
    ]
    if bad:
        print(f"FAIL check_4: {len(bad)} must URL(s) with non-200 status:")
        for b in bad:
            print(f"  {b}")
        return False
    print("PASS check_4: all must URLs returned HTTP 200")
    return True


def _check_word_count(records: dict, label: str, url: str, threshold: int) -> bool:
    r = records.get(url.rstrip("/"))
    if not r:
        print(f"FAIL check_{label}: raw file not found for {url}")
        return False
    _, wc = extract_main_text(r.get("html", ""))
    method = r.get("fetch_method", "?")
    if wc < threshold:
        print(f"FAIL check_{label}: {label} word count {wc} < {threshold} (method={method})")
        if "fallback" in method:
            print(f"  → Playwright was unavailable; run: pip install playwright && python -m playwright install chromium")
        return False
    print(f"PASS check_{label}: {label} word count {wc} >= {threshold} (method={method})")
    return True


def check_5_faq_word_count(records: dict) -> bool:
    url, threshold = WORD_COUNT_THRESHOLDS["faqs"]
    return _check_word_count(records, "5_faqs", url, threshold)


def check_6_course_progressions_word_count(records: dict) -> bool:
    url, threshold = WORD_COUNT_THRESHOLDS["course-progressions"]
    return _check_word_count(records, "6_course_progressions", url, threshold)


def check_7_no_must_url_errors(must_urls: list[str]) -> bool:
    if not ERRORS_FILE.exists():
        print("PASS check_7: no scrape_errors.jsonl (no errors logged)")
        return True
    must_set = {u.rstrip("/") for u in must_urls}
    errors: list[dict] = []
    for line in ERRORS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("url", "").rstrip("/") in must_set:
                errors.append(entry)
        except json.JSONDecodeError:
            pass
    if errors:
        print(f"FAIL check_7: {len(errors)} must URL(s) in error log:")
        for e in errors:
            print(f"  {e['url']}: {e.get('error')}")
        return False
    print("PASS check_7: no must URLs in scrape_errors.jsonl")
    return True


def main() -> None:
    if not RAW_DIR.exists():
        print(f"ERROR: {RAW_DIR} does not exist. Run the scraper first.")
        sys.exit(1)

    print(f"Validating {RAW_DIR} against {URL_CLASS_REF.name}\n")

    url_data = json.loads(URL_CLASS_REF.read_text(encoding="utf-8"))
    must_urls: list[str] = url_data.get("must", [])
    records = _load_records()

    results = [
        check_1_must_urls_present(records, must_urls),
        check_2_no_empty_files(),
        check_3_valid_json_with_html(),
        check_4_all_must_200(records, must_urls),
        check_5_faq_word_count(records),
        check_6_course_progressions_word_count(records),
        check_7_no_must_url_errors(must_urls),
    ]

    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 40}")
    print(f"Results: {passed}/{total} checks passed")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()

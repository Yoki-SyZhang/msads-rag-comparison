"""
Stage 1 scraper for this MSADS Graph RAG project.

Default behavior in this project folder:
1. Read URLs from ./docs/url_class_reference.json.
2. Fetch the "must" URLs by default.
3. Save one raw HTML JSON file per page into ./raw/.

The output schema is the one expected by grag/kg_builder.py:
url, fetched_at, http_status, page_class, fetch_method, url_aliases, html.
"""

import argparse
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALIAS_MAP = {
    "https://datascience.uchicago.edu/how-to-apply":
        "https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/how-to-apply",
    "https://datascience.uchicago.edu/about-us":
        "https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/instructors-staff",
    "https://datascience.uchicago.edu/capstone-projects":
        "https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/capstone-projects",
    "https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/in-person-program":
        "https://datascience.uchicago.edu/education/masters-programs/in-person-program",
    "https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/online-program":
        "https://datascience.uchicago.edu/education/masters-programs/online-program",
}

# Accordion content on FAQs and Course Progressions is present in the static HTML
# (BeautifulSoup extracts it even though it's CSS-hidden). Playwright is not needed.
PLAYWRIGHT_URLS: set[str] = set()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; UChicago-MSADS-RAG-Scraper/1.0; student research project)"
    )
}

INTER_REQUEST_DELAY = 1.5   # seconds between requests
REQUEST_TIMEOUT = 15         # seconds
PLAYWRIGHT_TIMEOUT = 30_000  # ms

# Selectors for structural noise to strip before text extraction.
# Kept specific to avoid accidentally stripping content (e.g. accordion divs whose
# class happens to contain "menu" or "widget").
_NOISE_SELECTORS = [
    "header",
    "footer",
    "aside",
    "nav",
    ".sidebar",
    "#sidebar",
    ".widget-area",
    ".breadcrumb",
    ".breadcrumbs",
    ".skip-link",
    ".social-share",
    ".share-links",
    ".site-header",
    ".site-footer",
]

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _normalize(url: str) -> str:
    return url.rstrip("/")


def url_to_filename(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    path = path.replace("/", "__").replace("\\", "__")  # path separators → __
    path = path.replace("-", "_")                        # hyphens → _
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", path)          # anything else → _
    safe = re.sub(r"_{3,}", "__", safe).strip("_")       # collapse 3+ underscores to __
    if len(safe) > 120:
        suffix = hashlib.md5(safe.encode()).hexdigest()[:8]
        safe = safe[:112] + "_" + suffix
    return safe + ".json"


def get_url_aliases(url: str) -> list[str]:
    canonical = _normalize(url)
    return [alias for alias, target in ALIAS_MAP.items() if _normalize(target) == canonical]


def needs_playwright(url: str) -> bool:
    return _normalize(url) in PLAYWRIGHT_URLS


# ---------------------------------------------------------------------------
# Text extraction (lightweight — for word_count_approx only)
# ---------------------------------------------------------------------------

def extract_main_text(html: str) -> tuple[str, int]:
    soup = BeautifulSoup(html, "lxml")

    for sel in _NOISE_SELECTORS:
        for el in soup.select(sel):
            el.decompose()

    # Try content containers from most to least specific.
    # Include role="main" and common id patterns because UChicago's theme
    # may use <div id="main"> rather than the HTML5 <main> element.
    content = (
        soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.find(id=re.compile(r"^main$|^primary$|^content$|^main-content$"))
        or soup.find("article")
        or soup.find("div", class_=re.compile(r"entry-content|post-content|site-main|content-area"))
    )

    if content:
        text = content.get_text(separator="\n", strip=True)
    else:
        body = soup.find("body") or soup
        text = body.get_text(separator="\n", strip=True)

    words = len(text.split())
    # Safety net: if still suspiciously short, use full body text
    if words < 50:
        body = soup.find("body")
        if body:
            text = body.get_text(separator="\n", strip=True)
            words = len(text.split())

    return text, words


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_with_requests(url: str, retries: int = 3) -> tuple[int, str]:
    wait = 2
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            return resp.status_code, resp.text
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries - 1:
                logging.warning(f"Retry {attempt + 1}/{retries} for {url}: {exc}")
                time.sleep(wait)
                wait *= 2
    raise last_exc


def expand_and_fetch_playwright(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_extra_http_headers(HEADERS)
        page.goto(url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT)

        # Click every closed accordion toggle
        toggles = page.query_selector_all(
            "button, [role='button'], [class*='accordion'], [id*='accordion-tab']"
        )
        for toggle in toggles:
            try:
                if toggle.get_attribute("aria-expanded") != "true":
                    toggle.click()
                    page.wait_for_timeout(500)
            except Exception:
                pass

        html = page.content()
        browser.close()
        return html


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_raw(
    output_dir: Path,
    url: str,
    page_class: str,
    status: int,
    html: str,
    fetch_method: str,
    extraction_warning: bool = False,
) -> None:
    record: dict = {
        "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "http_status": status,
        "page_class": page_class,
        "fetch_method": fetch_method,
        "url_aliases": get_url_aliases(url),
        "html": html,
    }
    if extraction_warning:
        record["extraction_warning"] = True

    path = output_dir / url_to_filename(url)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.debug(f"Saved → {path.name}")


def log_error(errors_path: Path, url: str, page_class: str, error: str) -> None:
    entry = {
        "url": url,
        "page_class": page_class,
        "error": error,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(errors_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Main scrape loop
# ---------------------------------------------------------------------------

def scrape_all(
    url_class_reference_path: str,
    output_dir: str,
    include_optional: bool = False,
) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    errors_path = output / "scrape_errors.jsonl"

    with open(url_class_reference_path, encoding="utf-8") as f:
        url_data = json.load(f)

    queue: list[tuple[str, str]] = []
    for url in url_data.get("must", []):
        queue.append((_normalize(url), "must"))
    if include_optional:
        for url in url_data.get("optional", []):
            queue.append((_normalize(url), "optional"))
    # dsi-general and footer-only are intentionally skipped

    total = len(queue)
    fetched = failed = 0
    logging.info(f"Starting scrape: {total} URLs (must={len(url_data.get('must', []))}, "
                 f"optional={len(url_data.get('optional', [])) if include_optional else 0})")

    for i, (url, page_class) in enumerate(queue, 1):
        logging.info(f"[{i}/{total}] {page_class} — {url}")
        try:
            if needs_playwright(url):
                if PLAYWRIGHT_AVAILABLE:
                    html = expand_and_fetch_playwright(url)
                    status, fetch_method = 200, "playwright"
                else:
                    logging.warning(f"Playwright unavailable, using requests fallback for {url}")
                    status, html = fetch_with_requests(url)
                    fetch_method = "requests_fallback"
            else:
                status, html = fetch_with_requests(url)
                fetch_method = "requests"

            if status != 200:
                logging.warning(f"HTTP {status} for {url}")
                log_error(errors_path, url, page_class, f"HTTP {status}")
                failed += 1

            save_raw(output, url, page_class, status, html, fetch_method)
            fetched += 1

        except Exception as exc:
            logging.error(f"Error fetching {url}: {exc}")
            log_error(errors_path, url, page_class, str(exc))
            failed += 1

        if i < total:
            time.sleep(INTER_REQUEST_DELAY)

    print(f"\n=== Scrape complete ===")
    print(f"Total: {total} | Fetched: {fetched} | Failed: {failed}")
    if errors_path.exists():
        print(f"Errors logged → {errors_path}")


# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Scrape MSADS pages into project raw/ JSON files.")
    parser.add_argument(
        "--url-ref",
        default=str(project_root / "docs" / "url_class_reference.json"),
        help="Path to url_class_reference.json. Defaults to ./docs/url_class_reference.json.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(project_root / "raw"),
        help="Directory for raw page JSON files. Defaults to ./raw.",
    )
    parser.add_argument(
        "--include-optional",
        action="store_true",
        help="Also fetch optional URLs if the reference file contains an optional list.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    scrape_all(args.url_ref, args.output_dir, include_optional=args.include_optional)

# Stage 1 — Web Scraping

`scraper.py` fetches 53 pages from the UChicago MS-ADS website and saves the full raw HTML to `data/raw/`, one JSON file per page.

```
docs/url_class_reference.json  (URL list with must / optional classification)
        │
        ▼  scraper.py
data/raw/*.json  (full HTML + fetch metadata per page)
```

---

## Input — `docs/url_class_reference.json`

Contains three buckets of URLs:

| Bucket | Meaning | Scraped? |
|--------|---------|---------|
| `must` | Core MS-ADS program pages | Yes |
| `optional` | Faculty profiles, capstone projects, research pages | Yes |
| `dsi-general` / `footer-only` | Site-wide pages unrelated to MS-ADS | No — skipped intentionally |

`scrape_all()` builds a queue of `(url, page_class)` pairs from `must` + `optional` and iterates through it with a 1.5 s delay between requests.

---

## URL normalization and filename generation

### `_normalize(url)`

Strips trailing slashes. Used throughout to compare URLs consistently.

### `url_to_filename(url)`

Converts a URL path to a safe filename:

```
https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/how-to-apply
→  education__masters_programs__ms_in_applied_data_science__how_to_apply.json
```

- Path segments separated by `__` (double underscore)
- Hyphens → underscores
- Non-alphanumeric characters → underscores
- 3+ consecutive underscores collapsed to `__`
- Paths longer than 120 characters are truncated and suffixed with an 8-char MD5 hash to avoid collisions

### `get_url_aliases(url)`

Looks up `ALIAS_MAP` (see §Redirect handling) and returns any alias URLs whose canonical target matches the given URL. These are stored in the `url_aliases` field so Stage 3 can match user queries referencing either form of a URL without duplicate chunks.

---

## Redirect handling — `ALIAS_MAP`

Five page pairs on the UChicago site were confirmed as HTTP 301 redirects (verified 2026-04-19). The scraper always fetches the canonical (200) URL and records alias → canonical relationships:

| Alias (301) | Canonical (200) |
|-------------|-----------------|
| `.../how-to-apply` (short) | `.../ms-in-applied-data-science/how-to-apply` |
| `.../about-us` | `.../ms-in-applied-data-science/instructors-staff` |
| `.../capstone-projects` | `.../ms-in-applied-data-science/capstone-projects` |
| `.../ms-in-applied-data-science/in-person-program` (long) | `.../masters-programs/in-person-program` |
| `.../ms-in-applied-data-science/online-program` (long) | `.../masters-programs/online-program` |

Note: pairs 1–3 redirect short → long; pairs 4–5 redirect long → short. `source_url` in every downstream record always contains the canonical URL.

---

## Fetching — two strategies

### `fetch_with_requests(url)` — default path

Uses `requests.get()` with a custom `User-Agent` header, 15 s timeout, and up to 3 retries with exponential backoff (2 s → 4 s → 8 s).

### `expand_and_fetch_playwright(url)` — accordion override

Originally added for pages where accordion content might be JS-injected. In practice, the UChicago site includes accordion HTML in the static page (CSS-hidden, not JS-rendered), so `PLAYWRIGHT_URLS` is currently empty and Playwright is never invoked. The implementation is retained as a fallback:

1. Launch headless Chromium via Playwright
2. Navigate to the URL and wait for `networkidle`
3. Query all accordion toggle buttons and click any with `aria-expanded != "true"`
4. Return the fully-rendered HTML

### `needs_playwright(url)`

Returns `True` if the normalized URL is in `PLAYWRIGHT_URLS`. Currently always `False`.

---

## Noise stripping — `_NOISE_SELECTORS`

Used by `extract_main_text()` (word count estimation only — not the main cleaning step, which happens in Stage 1 `cleaner.py`):

```python
["header", "footer", "aside", "nav", ".sidebar", "#sidebar",
 ".widget-area", ".breadcrumb", ".breadcrumbs", ".skip-link",
 ".social-share", ".share-links", ".site-header", ".site-footer"]
```

This list is imported by `cleaner.py` so both stages strip the same structural noise.

---

## Output — `data/raw/*.json`

One file per page. The HTML field contains the complete page source.

```json
{
  "url": "https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/how-to-apply",
  "fetched_at": "2026-04-25T10:32:14.123456+00:00",
  "http_status": 200,
  "page_class": "must",
  "fetch_method": "requests",
  "url_aliases": ["https://datascience.uchicago.edu/how-to-apply"],
  "html": "<!DOCTYPE html>..."
}
```

Failed fetches are not saved as page files. Instead, an error entry is appended to `data/raw/scrape_errors.jsonl`:

```json
{"url": "...", "page_class": "must", "error": "HTTP 404", "logged_at": "..."}
```

---

## Results

| Metric | Value |
|--------|-------|
| Pages scraped | 53 |
| Must pages | ~20 |
| Optional pages (faculty profiles, capstone, research) | ~33 |
| Failed fetches | 0 |
| Fetch method | `requests` (Playwright not needed) |

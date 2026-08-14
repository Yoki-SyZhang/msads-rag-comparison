# src/retrieval — Cleaning, Chunking, Embedding & Retrieval

Five scripts form the complete data preparation and retrieval pipeline, turning 53 raw HTML pages into searchable ChromaDB collections.

```
data/raw/*.json
      │
      ▼  cleaner.py  →  data/cleaned/*.json       (structured section dicts)
      ▼  chunker.py  →  data/chunks/size_N/*.json  (metadata-rich chunks, 4 configs)
      ▼  embedding_DB.py  →  data/chroma_db/       (5 ChromaDB collections)
                                    │
                  query_router.py ──┤
                                    ▼
                              retriever.py  →  top-k chunks to QA layer
```

---

## cleaner.py — HTML → structured sections

### Noise removal

Before content extraction, the parser strips elements that pollute the text: `footer`, `aside`, `nav`, `.sidebar`, `.breadcrumb`, `.social-share`, `.site-header`, `.site-footer`, `.mobile-sidebar-container`, `.no-page-hero-spacer`, etc. The bare `"header"` selector is excluded because each page has a `<header class="page-header">` inside `div.main-content` that contains the page `<h1>` and intro paragraph.

### Main content extraction

After noise removal, the parser locates the primary content block using a fallback chain:  
`<main>` → `role="main"` → `#main` / `#content` → `<article>` → `.entry-content` → `<body>`

### Tree walk — HTML → section dicts

`_walk()` recurses through the content tree and produces `{"section", "section_breadcrumb", "text", "content_type", "program_type"}` dicts. A `heading_stack: list[tuple[int, str]]` is maintained throughout the walk — when a heading `hN` is seen, all stack entries with level ≥ N are popped, then `(N, heading_text)` is pushed. This stack drives `section_breadcrumb` for all emitted sections.

| Node type | Action |
|-----------|--------|
| `h1`–`h4` | Flush pending text, update `current_section`, update `heading_stack` |
| `p` | Process inline links (see below), append text to pending buffer |
| `table` | Convert to markdown rows, emit as `content_type="table"` |
| `ul` / `ol` (plain) | Convert to `• bullet` lines, emit as `content_type="list"` |
| `div.list-accordion` | Skip subtree recursion; delegate to `_extract_list_accordion()` (see §Two-level accordion) |
| `li.accordion-item` / `li.accordion__item` | Flush pending text, set `current_section` from title link, walk inner content via `_accordion_content()` |
| `li` (plain) | Recurse normally |
| `figcaption` | Emit as `content_type="caption"` |
| `div`, `section`, `article` | Recurse |
| `script`, `style`, `svg`, `button` | Skip |

An `_accordion_depth` counter tracks whether the walk is currently inside an accordion item. In non-accordion context, `section_breadcrumb` is set to `current_section` only (avoiding spurious heading inheritance); inside an accordion, the full `_breadcrumb()` from the heading stack is used.

Pending text is flushed at every structural boundary. Sections with fewer than 5 words are discarded.

### program_type tagging

`_derive_program_type(url)` classifies each page by URL:

| URL contains | `program_type` |
|---|---|
| `in-person-program` | `"in_person"` |
| `online-program` | `"online"` |
| anything else | `"general"` |

Every section dict inherits the page-level `program_type`. This field enables metadata filtering at query time (see `query_router.py`).

### Two-level accordion — `_extract_list_accordion()`

In-person and online program pages use a two-level `div.list-accordion` structure that the generic walk cannot handle correctly:

```
div.list-accordion
  ul.accordion (outer — program tracks)
    li.accordion-item  ×3  ("Noncredit Courses", "1-Year Program", "2-Year Thesis Track")
      div.accordion-content
        ul.accordion (inner — individual courses)
          li.accordion__item  ×27–30
            div.accordion__content > div.textblock  ← course description
```

When `_walk()` encounters a `div.list-accordion` that has both outer (`li.accordion-item`) and inner (`li.accordion__item`) levels, it delegates to `_extract_list_accordion()`, which produces:

- **One `accordion_overview` section**: a Python dict mapping each track name to its list of course names, e.g. `{"Noncredit Courses": ["Career Seminar (Seminar, required)", ...], "1-Year 12 Course Program": [...]}`. This overview is stored as a raw dict in `text` — `chunker.py` converts it to natural language.
- **One section per course**: `section = course name`, `section_breadcrumb = "Track > Course Name"`, `content_type = "text"`.

The overview section name is derived from the URL: `"{URL Page Title} - Accordion Overview"` (e.g., `"In Person Program - Accordion Overview"`), providing page context in the embedding prefix.

`_extract_list_accordion()` is called with an empty heading stack (`[]`) so that course chunk breadcrumbs are never polluted by page-level h2/h3 headings.

### FAQs and Course Progressions accordion

Both pages use nested accordions for Q&A and course scheduling respectively. These are handled by the generic `_walk()` accordion path (not `_extract_list_accordion`), since their outer `li` elements use class `accordion-item` but the structure differs in depth and purpose. `_accordion_content(li)` scans only direct children of each `<li>` for content divs, distinguishing `accordion-content` (hyphen, outer) from `accordion__content` (underscore, inner).

### Link handling

`_inline_links()` rewrites `<a>` tags before text extraction:

- Links in `EXTERNAL_ACTION_DOMAINS` (apply-psd, financialaid, grad, internationalaffairs) → formatted as `[link text](href)`, preserving call-to-action destinations
- All other links → link text only, href discarded

### PII redaction

`redact_pii()` is applied in `chunker.py` at the point chunks are emitted (not in `cleaner.py`), so all chunk text — including accordion overview natural language — goes through the same redaction path:

1. All email matches are replaced with null-byte placeholders
2. Placeholders for `SAFE_EMAILS` (the two program advising/admissions addresses) are restored
3. Remaining placeholders → `[REDACTED]`
4. Phone numbers → `[REDACTED]`

### Page-specific specialized cleaners

Three pages have structures the generic walk cannot handle:

**`_parse_dataviz_scripts()`** — extracts `datavizData_XXXXX = {...}` JavaScript variables from `<script>` blocks, then injects the chart data when `_walk()` encounters the matching `<div data-dataviz-id>` container. Handles donut, bar, and geomap formats. The `our-students` page yields 9 chart chunks this way.

**`_clean_course_progressions()`** — traverses the 3-tab accordion hierarchy explicitly (tab → quarter → course-group → course), stripping `span.grading` text from section names. Produces one chunk per course with `section = "{tab} — {quarter}"` and an `accordion_overview` dict with quarter-level course lists. Also emits a `section_breadcrumb` at each level.

**`_clean_instructors_staff()`** — maps `div.gridder-content[id]` bios to their corresponding person name and group via `ul.gridder li` items. Emits one bio chunk per person (`section = "{group} — {name}"`), one summary list chunk per group, and runs the generic cleaner on the page intro text. Also produces an `accordion_overview` dict (`{"Faculty": ["Alice Chen", ...], "Staff": [...]}`) for the full roster overview.

### Events & Deadlines

All sections from pages whose URL contains `events-deadlines` get `content_type="time_sensitive"`. The QA system prompt uses this flag to caveat deadline-related answers with the scrape date.

### Output — `data/cleaned/*.json`

```json
{
  "url": "https://...",
  "page_title": "How to Apply",
  "page_class": "must",
  "program_type": "general",
  "url_aliases": [],
  "scraped_at": "2026-04-25T...",
  "sections": [
    {
      "section": "Application Requirements",
      "section_breadcrumb": "Application Requirements",
      "text": "...",
      "content_type": "text",
      "program_type": "general"
    },
    {
      "section": "In Person Program - Accordion Overview",
      "section_breadcrumb": "In Person Program - Accordion Overview",
      "text": {"Noncredit Courses": [...], "1-Year 12 Course Program": [...]},
      "content_type": "accordion_overview",
      "program_type": "in_person"
    }
  ]
}
```

---

## chunker.py — sections → metadata-rich chunks

### Splitter

```python
RecursiveCharacterTextSplitter(chunk_size=N, chunk_overlap=N*0.1)
```

Applied **per section** — never across section boundaries. Four configurations are produced at once:

| Config | chunk_size | overlap | Approx. tokens (MiniLM / BGE) |
|--------|-----------|---------|-------------------------------|
| `size_512` | 512 chars | 51 | ~128 / ~128 |
| `size_900` | 900 chars | 90 | ~225 / ~225 |
| `size_1536` | 1536 chars | 154 | ~384 / ~384 |
| `size_1900` | 1900 chars | 190 | ~475 / ~475 |

MiniLM (256-token limit) is used only with `size_512` and `size_900`. BGE-small (512-token limit) handles all four configs safely.

Short splits (≤ 15 words) are merged into the preceding or following chunk via `_merge_short()`.

### accordion_overview handling

When `block["content_type"] == "accordion_overview"`, the block's `text` is a Python dict (not a string). `_split_accordion_overview()` converts it to natural language via `_overview_to_text()` (which formats nested dicts/lists as indented key: value lines), applies `redact_pii()`, and decides whether to emit one chunk or split by top-level key:

- **Whole dict fits within `chunk_size`** (measured as JSON byte length) → 1 chunk, `section` and `section_breadcrumb` from the parent block
- **Oversized** → one chunk per top-level key; `section = top_key`, `section_breadcrumb = f"{parent_bc} > {top_key}"`

All other content types go through `RecursiveCharacterTextSplitter` followed by `redact_pii()` per split.

### chunk_id format

```
{url_stem}__{section_slug}__{page_counter}
```

`page_counter` is a single page-level counter (not reset per section), so IDs are globally unique even when multiple sections share similar names.

### Metadata per chunk (12 fields)

```json
{
  "chunk_id":           "education__masters_...__how_to_apply__application_requirements__0",
  "text":               "...",
  "source_url":         "https://datascience.uchicago.edu/.../",
  "page_title":         "How to Apply",
  "section":            "Application Requirements",
  "section_breadcrumb": "Application Requirements",
  "content_type":       "text",
  "page_class":         "must",
  "program_type":       "general",
  "word_count":         82,
  "scraped_at":         "2026-04-25T...",
  "url_aliases":        ["https://datascience.uchicago.edu/how-to-apply/"]
}
```

`url_aliases` is stored as a list here; `embedding_DB.py` serializes it to a JSON string before loading into ChromaDB (which requires scalar metadata values).

Chunks are deduplicated within each page by `(section, text)` content — first occurrence wins.

### Output — `data/chunks/size_N/*.json`

```json
{
  "source_url":  "...",
  "page_title":  "...",
  "page_class":  "must",
  "chunk_count": 26,
  "chunks":      [ { ...12-field chunk dict... } ]
}
```

---

## embedding_DB.py — chunks → ChromaDB

### Five collections

| Collection | Embedding model | Chunk config | Chunks |
|------------|----------------|-------------|--------|
| `msads_minilm_size_512` | all-MiniLM-L6-v2 | size_512 | 753 |
| `msads_minilm_size_900` | all-MiniLM-L6-v2 | size_900 | 561 |
| `msads_bgesmall_size_900` | BAAI/bge-small-en-v1.5 | size_900 | 561 |
| `msads_bgesmall_size_1536` | BAAI/bge-small-en-v1.5 | size_1536 | 447 |
| `msads_bgesmall_size_1900` | BAAI/bge-small-en-v1.5 | size_1900 | 441 |

### Embedded text format

Each chunk is embedded with a contextual prefix prepended to the body:

```
[Page: {page_title} | Section: {section_breadcrumb}]

{chunk text}
```

For `accordion_overview` chunks, `{chunk text}` is the natural-language output of `_overview_to_text()`. For all other chunks, it is the plain text body.

### Idempotency

`embed_and_load()` checks the existing collection count before embedding:
- Count matches expected → skip (already loaded)
- Count is 0 → embed and load
- Count mismatch → delete collection and rebuild from scratch

### Metadata stored in ChromaDB

9 fields: `source_url`, `page_title`, `section`, `section_breadcrumb`, `content_type`, `program_type`, `word_count`, `scraped_at`, `url_aliases` (JSON string).

`source_url` is always normalized to end with `/` to prevent URL string mismatch during eval.

---

## query_router.py — LLM-based query classification

Classifies an incoming user query into one of three program-type labels using DeepSeek and returns the corresponding ChromaDB `where` filter.

```python
where = route_query("What core courses are in the in-person program?", client)
# → {"program_type": {"$ne": "online"}}
```

| Query label | ChromaDB filter | Effect |
|---|---|---|
| `in_person` | `{"program_type": {"$ne": "online"}}` | Keeps `in_person` + `general` chunks |
| `online` | `{"program_type": {"$ne": "in_person"}}` | Keeps `online` + `general` chunks |
| `general` | `None` | All chunks visible |

Using `$ne` (not-equal) rather than `$eq` ensures that cross-program content tagged `"general"` (faculty, tuition, admissions) is always retrievable regardless of which program the user asks about.

The caller constructs the OpenAI-compatible client and passes it in:
```python
from openai import OpenAI
client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
```

> **Note (Stage 3):** `query_router.py` is **not called** by the QA pipeline (`src/qa/qa_pipeline.py`). The agent loop handles page selection autonomously via `list_page_sections` / `fetch_section_chunks` / `semantic_search` tool calls. `query_router.py` remains available for direct retrieval use cases and evaluation notebooks.

---

## retriever.py — semantic retrieval

Wraps a ChromaDB collection and a sentence-transformers model into a single `Retriever` object. The class exposes three public methods: one for vector similarity search and two for exact metadata lookups added in Stage 3 to support the agent tool-calling interface.

### `retrieve(query, top_k, where)` — vector similarity search

```python
r = Retriever("msads_bgesmall_size_900", "BAAI/bge-small-en-v1.5")
hits = r.retrieve("What courses are required?", top_k=7)

# Stage 2 usage — program_type filter from query_router:
where = route_query(query, client)           # {"program_type": {"$ne": "online"}}
hits  = r.retrieve(query, top_k=5, where=where)

# Stage 3 usage — URL-scoped search from agent semantic_search tool:
hits  = r.retrieve(query, top_k=7, where={"source_url": {"$in": ["url1", "url2"]}})
```

BGE models require a task-specific prefix on queries (not on indexed passages):
```python
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
```

Each result dict contains: `text`, `chunk_id`, `source_url`, `page_title`, `section`, `section_breadcrumb`, `content_type`, `program_type`, `distance`.

### `list_sections(url)` — page structure inspection (Stage 3)

Returns all unique `section_breadcrumb` values and their chunk counts for a given page URL. Used by the agent's `list_page_sections` tool to let the agent inspect page structure before targeting a specific section.

```python
sections = r.list_sections("https://datascience.uchicago.edu/.../how-to-apply/")
# → [{"section_breadcrumb": "Application Requirements", "chunk_count": 3}, ...]
```

Internally calls `collection.get(where={"source_url": {"$eq": url}}, include=["metadatas"])` and aggregates breadcrumb counts.

### `get_by_section(url, section_breadcrumb)` — exact section fetch (Stage 3)

Retrieves all chunks from a specific section of a page using an exact `(url, section_breadcrumb)` match. Used by the agent's `fetch_section_chunks` tool, with fuzzy breadcrumb matching applied in `agent_tools.py` before calling this method.

```python
chunks = r.get_by_section(
    "https://datascience.uchicago.edu/.../how-to-apply/",
    "Application Requirements > Transcripts"
)
```

Returns the same dict schema as `retrieve()`, with `distance=0.0` (not a similarity search).

### ChromaDB `where` clause requirements (ChromaDB 1.5.5)

ChromaDB 1.5.5 enforces that every `where` dict has **exactly one top-level key**. This means:

- **Single-field filter** — must use explicit `$eq` operator:
  ```python
  # correct
  where={"source_url": {"$eq": url}}
  # incorrect (bare string value causes TypeError in some versions)
  where={"source_url": url}
  ```

- **Multi-field filter** — must use `$and` wrapper:
  ```python
  # correct
  where={"$and": [
      {"source_url":         {"$eq": url}},
      {"section_breadcrumb": {"$eq": section_breadcrumb}},
  ]}
  # incorrect (two top-level keys → ValueError)
  where={"source_url": url, "section_breadcrumb": section_breadcrumb}
  ```

  The `get_by_section()` method was originally written with two bare top-level keys (the incorrect form). This caused a crash whenever the agent called `fetch_section_chunks`. It was fixed to use `$and` with explicit `$eq` operators.

- **Set membership filter** (Stage 3 `semantic_search` with `include_urls`):
  ```python
  where={"source_url": {"$in": ["url1", "url2"]}}
  ```

---

## Stage 3 integration — how the QA pipeline uses retrieval

The QA pipeline (`src/qa/qa_pipeline.py`) selects the `msads_bgesmall_size_900` collection and initializes a single `Retriever` instance shared across all three agent tools.

```
Agent loop (up to 9 steps)
  │
  ├─ list_page_sections(url)
  │       → retriever.list_sections(url)
  │         ChromaDB .get() — reads metadatas only, no embeddings
  │
  ├─ fetch_section_chunks(url, section_breadcrumb)
  │       → agent_tools._fetch_section_chunks()
  │         → fuzzy breadcrumb matching (exact → case-insensitive → difflib cutoff=0.6)
  │         → retriever.get_by_section(url, matched_breadcrumb)
  │           ChromaDB .get() — exact metadata lookup, no vector search
  │
  └─ semantic_search(query, include_urls, exclude_urls, exclude_chunk_ids, top_k)
          → retriever.retrieve(query, top_k=7, where={"source_url": {"$in": include_urls}})
            ChromaDB .query() — vector similarity search with optional URL scope
```

Key differences from Stage 2 retrieval:

| Aspect | Stage 2 | Stage 3 |
|--------|---------|---------|
| Collection | Any of 5 | Fixed: `msads_bgesmall_size_900` |
| Page selection | `query_router.py` (`$ne` filter) | Agent tool calls (autonomous) |
| Retrieval modes | `retrieve()` only | `retrieve()` + `list_sections()` + `get_by_section()` |
| Deduplication | None | `exclude_chunk_ids` passed across agent steps |
| Judgment | None | Judger LLM evaluates completeness per step |

The `query_router.py` `$ne` filter approach (exclude one program type) is replaced by the agent's `$in` filter approach (include only specific page URLs). This gives the agent finer-grained control: it can target individual pages rather than broad program categories.

### Stage 4 note

The `src/retrieval/` layer was **not modified** in Stage 4. All Stage 4 changes live in `src/qa/` (pipeline upgrades: multi-question fan-out, history/stream/progress_cb) and `src/ui/` (Streamlit chatbot). The `Retriever`, `PageSelector`, and `ChromaDB` collection remain unchanged.

---

## Running the pipeline

```bash
conda activate adsp-nlp-backup

# Stage 2a — generate all chunk configs from raw data
python src/retrieval/chunker.py

# Stage 2b — embed and load into ChromaDB (all 5 collections)
python src/retrieval/embedding_DB.py
```

To force a full rebuild, delete `data/chroma_db/` before running `embedding_DB.py`.

Validation:
```bash
python tests/validate_stage_2_chunking.py   # 21/21 checks
```

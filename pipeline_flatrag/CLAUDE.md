# CLAUDE.md — MS Applied Data Science RAG Chatbot

## Project Context

This project is the Midterm Project for the GEN AI Principles course. The goal is to build a RAG-based question-answering system that answers user questions about the University of Chicago MS in Applied Data Science program, based on content from the program's webpage and its sub-pages.

Knowledge base source: https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/

Although the course framing treats this as a team project, I am completing it independently. Keep workflows simple — no branching strategy, no multi-contributor conventions.

## Deliverables

1. A runnable RAG chatbot with a UI
2. Technical documentation, at least 5 pages, in the style of a medium article
3. A 10-minute presentation (PPT)
4. Evaluation report covering retrieval accuracy and response relevance

## Tech Stack

### 🔒 Locked (do not change without strong reason)

- **Language**: Python 3.11.14
- **Runtime environment**: conda env `adsp-nlp-backup` (torch 2.11.0+cu128, CUDA 12.8, GPU-enabled)
  - To activate: `conda activate adsp-nlp-backup`
  - Packages already present: `torch`, `sentence-transformers`, `chromadb`, `faiss-cpu`, `langchain` (full suite), `openai`, `beautifulsoup4`, `requests`
  - Still needed: `pip install streamlit` (install before Stage 4)
- **Dependency management**: `pip + requirements.txt` (switch to `uv` only if explicitly requested)
- **Version control**: git, commit at the end of each stage
- **Project structure**: see "Project Structure" section below

### 🎯 Decisions (finalized through Stage 3)

- **Scraping**: `requests` + `BeautifulSoup4` for 51 pages; `Playwright` for FAQs and Course Progressions (accordion content is CSS-hidden in static HTML, not JS-injected — BS4 extracts it, but Playwright was added as a reliability fallback and is in requirements.txt).
  - Crawl depth: main page (depth 0) → direct sub-links (depth 1) → their direct children (depth 2). Domain-bounded to `datascience.uchicago.edu`. No BFS beyond depth 2.
  - In-scope content: visible text, `<table>` content, `<figure>` captions
  - Out-of-scope: `<img>` tags with no `alt` attribute or empty alt text; JavaScript-only content
- **Embedding model**: `BAAI/bge-small-en-v1.5` ✅ finalized after Stage 2 eval — outperformed `all-MiniLM-L6-v2` on Hit@5 across the 10-query test set. BGE-small has a 512-token limit (≈ 2048 chars), safely covering all chunk configs. Requires a task-specific query prefix: `"Represent this sentence for searching relevant passages: "` prepended to queries at retrieval time (not at indexing time).
- **Active ChromaDB collection**: `msads_bgesmall_size_900` ✅ finalized — BGE-small embeddings, 900-char chunks, 561 chunks total. Five collections were built and evaluated; this config gave the best retrieval quality.
- **Chunking strategy**: `RecursiveCharacterTextSplitter`, chunk_size=**900 characters**, overlap=90 chars ✅ finalized after Stage 2 eval across four configs (512 / 900 / 1536 / 1900 chars). Applied per section (never across section boundaries). Short splits (≤ 15 words) merged into adjacent chunks.
- **Top-k retrieval**: `top_k=7` per `semantic_search` call ✅ finalized in Stage 3 (`_TOP_K = 7` in `qa_pipeline.py`).
- **Vector database**: `ChromaDB` 1.5.5 (local, zero-config, supports metadata filtering via `$eq` / `$ne` / `$in` / `$and` operators)
  - Note: ChromaDB 1.5.5 requires exactly one top-level key in every `where` dict; multi-field filters must use `$and: [{$eq},{$eq}]`.
- **LLM**: `deepseek-chat` via DeepSeek API (OpenAI-compatible endpoint). Used for all four LLM calls in the QA pipeline: query rewriting, agent loop, judger, and answer generation.
  - Alternative for final demo comparison: `deepseek-reasoner`
- **RAG orchestration**: hand-written, no framework. Implemented as a 5-step pipeline in `src/qa/qa_pipeline.py` (~360 lines): query rewrite → agent loop (tool-calling, max 9 steps) → judger (completeness per step) → context assembly → generation. LangChain is installed in the env but intentionally not used.
- **UI**: Streamlit (Stage 4, not yet implemented) — planned as multi-turn conversation with full session history in `st.session_state.messages`. Retrieval uses the current query only; the LLM prompt includes the last N turns for conversational context.
  - Install before Stage 4: `pip install streamlit`
- **Link handling in cleaning**: preserve `<a>` href values **only for out-of-corpus external action URLs** (`apply-psd.uchicago.edu`, `financialaid.uchicago.edu`, `grad.uchicago.edu`, `internationalaffairs.uchicago.edu`), formatted as `[link text](href)`. All other hrefs discarded; link text retained.
- **System prompt wording** — Grounding rules: scope guard, insufficient-context refusal, TIME-SENSITIVE disclaimer, Career Outcomes caveat, numbered inline `[N]` citations + Sources list, no-fabrication / no-PII rule.
- **Conversational context window** — finalized at `_HISTORY_KEEP_TURNS = 3` recent full exchanges (6 messages) kept uncompressed; older turns LLM-summarized into a rolling system-role summary. Compression triggered once `pipeline_history` exceeds `_COMPRESS_AFTER = 3` full exchanges.

### Responsible AI Requirements (from project spec)

- **Grounding**: system prompt must refuse off-topic or clearly harmful queries
- **Hallucination mitigation**: LLM must cite the source chunk(s) used; must respond "I don't have enough information about that" when retrieved context is insufficient, rather than fabricating
- **PII redaction**: strip faculty/staff email addresses and phone numbers from scraped text before storing in the vector DB

## Project Structure

```
.
├── CLAUDE.md                        # This file
├── README.md                        # Public-facing documentation
├── requirements.txt
├── .env                             # API key template 
├── data/
│   ├── raw/                         # Raw scraper output
│   ├── cleaned/                     # After cleaning artifacts removed
│   └── structured/                  # Structured JSON with metadata
├── src/
│   ├── scraping/                    # Stage 1 
│   ├── retrieval/                   # Stage 2 
│   ├── qa/                          # Stage 3 
│   └── ui/                          # Stage 4 
├── tests/                           # Validation scripts per stage
├── eval/
│   ├── questions.json               # Hand-crafted evaluation questions (target: 20)
│   └── results/
├── docs/
│   ├── requirements/
│   │   └── Class project-1 Midterm Project.pdf
│   └── writeup/                     # Source material for the 5-page writeup
└── notebooks/                       # Exploratory analysis, not in production path
```


## Known Site-Specific Concerns

All five concerns were identified in Stage 1 and are fully addressed in the current codebase.

| # | Concern | Resolution |
|---|---------|------------|
| 1 | **Career Outcomes** — employer data is logo images, not text | Only ~250 words of body text scraped; image content is a known corpus gap, documented in eval report |
| 2 | **Accordion content** on FAQs and Course Progressions | Content is CSS-hidden in static HTML; BS4 extracts it. Playwright added as fallback for these two pages |
| 3 | **URL redirects** — 5 verified 301 alias→canonical pairs | `source_url` always set to canonical (200) URL; aliases stored in `url_aliases` metadata field |
| 4 | **Time-sensitive content** on Events & Deadlines | All sections tagged `content_type: "time_sensitive"`; QA prompt caveats answers with scrape date |
| 5 | **PII on faculty profile pages** (~50 `/people/` pages) | `redact_pii()` in `chunker.py` strips emails, phones, and office addresses before vector DB storage |


## Stage Completion Criteria

Each stage must meet the following before moving on:

1. Code runs end-to-end via a demo script located at `src/<stage_name>/demo.py` — a minimal, standalone runnable script with no test assertions
2. A `tests/validate_stage_N.py` validation script exists and passes
3. Git commit with message format: `[Stage N] short description`
4. If any default was changed, this document is updated accordingly


## Collaboration Rules with Claude Code

1. **Read this file at the start of every new session**
2. **Plan before acting**: for any task touching more than one file, use `/plan` first
3. **Conservative edits**: do not refactor existing code unless explicitly requested
4. **No undeclared dependencies**: do not introduce packages outside the listed tech stack without asking
5. **Update progress**: at the end of each stage, update the "Progress" section below

## Progress

- [x] Stage 0: CLAUDE.md finalized
- [x] Stage 1: Data preparation (scraping, cleaning): 
> src/retrieval/cleaner.py + chunker.py; 53 pages → 864 chunks; 6/6 validation checks pass. 

> Three page-specific specialized cleaners: `_parse_dataviz_scripts` (chart data injection for our-students and similar), `_clean_course_progressions` (tab→quarter→course hierarchy, grading span stripped from section names), `_clean_instructors_staff` (per-person bio chunks, group summary lists, decompose-then-reparse pattern for intro text).
- [x] Stage 2 (chunking + embedding + vector DB + retrieval optimization):
> **Chunking**: chunker.py produces chunks across four size configurations (512 / 900 / 1536 / 1900 chars); deduplicates within each page by (section, text) content; globally unique chunk_ids via page-level counter. 21/21 validation checks pass.

> **Metadata**: two new fields added to every chunk — `program_type` (`in_person` / `online` / `general`, derived from URL) and `section_breadcrumb` (full heading path, e.g. `"Curriculum > Core Courses"`). Accordion-structured pages (in-person, online, instructors, course progressions) also produce an `accordion_overview` chunk containing a structured natural-language summary of all items.

> **Embedding**: embedding_DB.py uses contextual prefix `[Page: {page_title} | Section: {breadcrumb}]` prepended to chunk body. Five ChromaDB collections: msads_minilm_size_512 (753), msads_minilm_size_900 (561), msads_bgesmall_size_900 (561), msads_bgesmall_size_1536 (447), msads_bgesmall_size_1900 (441). source_url always normalized with trailing slash.

> **Retrieval**: retriever.py supports optional `where` metadata filter. query_router.py classifies queries via DeepSeek LLM and returns `$ne` ChromaDB filters to exclude the wrong program scope while preserving `general` chunks.

> **Eval**: eval/retrieval_eval.ipynb updated to compare all 5 collections with and without query routing; top-5 chunk display per query.
- [x] Stage 3: QA generation (prompt design, grounding, evaluation)
> **Pipeline** (`src/qa/`): 5-step — ① Query rewriting (one LLM call); ② Agent loop (max 9 steps): three tools — `list_page_sections`, `fetch_section_chunks`, `semantic_search`; Judger LLM evaluates completeness per step, chunks accumulate; ③ Context assembly with `[TIME-SENSITIVE]` markers; ④ Generation (grounding rules: scope guard, insufficient-context, source citations, Career-page caveat); ⑤ JSON log to `data/qa_log/`. Active collection: `msads_bgesmall_size_900`. 9/9 validation checks pass.

> **Stage 4-driven pipeline upgrades** (`qa_pipeline.py` + `prompt_templates.py`): `run()` gains `history` (conversation history for coreference resolution in rewrite + conversational context in generation), `stream` (returns raw streaming iterator for token-by-token UI display), and `progress_cb` (callable invoked at each step for live status). New `_detect_subquestions()` splits compound queries into independent sub-questions, each processed through its own rewrite + agent loop before a single merged generation call. `SYSTEM_PROMPT` Rule 5 updated to require numbered inline `[N]` citations + Sources list. `build_generation_messages()` added to `prompt_templates.py`. validate_stage_3.py extended to 10 checks (new Check 9: multi-turn coreference resolution).
- [x] Stage 4: UI (Streamlit interface)
> **Streamlit UI** (`src/ui/app.py`): multi-turn chatbot with `st.chat_message` history rendering, `st.status` live progress display during retrieval, token-streaming with `▌` cursor, `_linkify_sources()` post-processing `[N]` inline markers into clickable `[[N]](url)` hyperlinks, rolling history compression via `_compress_history()` / `_maybe_compress()` (LLM summarization once >3 full exchanges), low-confidence `st.info` banner. Sidebar: DSI logo (base64 SVG) + "New Conversation" button (saves current conversation to sidebar history before clearing) + past conversations list (click to switch back). 14/14 validate_stage_4.py checks pass.
- [ ] Stage 5: Documentation and presentation

## Change Log

Record any modification to defaults or locked items here. Format: `YYYY-MM-DD HH:MM — description`.

- 2026-04-18 00:00 — Initial draft
- 2026-04-18 10:30 — Environment pinned to `adsp-nlp-backup` (Python 3.11.14, torch+cu128, CUDA 12.8); scraping depth and content rules added; metadata schema split into Core/Extension; Responsible AI requirements added; evaluation section added; demo script location clarified; multi-turn UI noted; change log format updated to include HH:MM; docs/requirements/ added to project structure
2026-04-19 11:30 — Added “Known Site-Specific Concerns” section covering six Stage 1 findings: exclude image-only employer logos on Career Outcomes; validate accordion expansion on FAQs/Course Progressions with Playwright fallback only if needed; enforce five verified 301 alias→canonical URL mappings and canonical source_url; mark Events & Deadlines as time_sensitive; redact PII from faculty profile pages; and treat the Capstone Archive as a 10-project featured subset.
2026-04-25 — Stage 1 scraper implemented: src/scraping/scraper.py (requests+BS4 for 51 pages, Playwright for FAQs+Course Progressions), src/scraping/demo.py, tests/validate_stage_1_scraping.py. playwright added to requirements.txt. Stage 1 link-handling rule added to Defaults: preserve hrefs only for out-of-corpus external action URLs.
2026-04-28 — Stage 1 cleaning+chunking implemented: src/retrieval/cleaner.py (section-aware HTML extraction, accordion handling for two-level nested accordions on FAQs/Course Progressions, PII redaction with SAFE_EMAILS whitelist), src/retrieval/chunker.py (RecursiveCharacterTextSplitter 512/50, per-section splitting), tests/validate_stage_2_cleaning.py. 53 pages → 794 chunks. depth field dropped from metadata (not needed for RAG).
2026-04-29 — Stage 1 specialized cleaners added: `_parse_dataviz_scripts` injects embedded chart data (JS datavizData_ vars) into walk() for pages like our-students; `_clean_course_progressions` traverses 3-tab accordion hierarchy explicitly (tab→quarter→course-group→course), strips span.grading from section names, deduplicates meta parts; `_clean_instructors_staff` emits one chunk per person from div.gridder-content with section="{group} — {name}", builds name/group map from ul.gridder li items, runs generic cleaner on intro text after decomposing div.people. Final chunk count: 864 (up from 794).
2026-04-30 — Stage 2 chunking.py is revised and outputs are saved to data\chunks\ with three chunk-size configurations (256 / 512 / 900 chars).
2026-05-02 — Stage 2 embedding pipeline finalized: embedding_DB.py embeds each chunk as "section\ntext" (prepending section name to body for richer retrieval context); chunker.py deduplicates by (section, text) content and generates globally unique chunk_ids via page-level counter; _slugify truncation removed (full section name used in slug). Collections rebuilt: msads_minilm_size_512 (803), msads_minilm_size_900 (594), msads_bgesmall_size_900 (594). eval/retrieval_test_set.json created with 10 queries (easy/medium/hard/edge).
2026-05-02 — Bug fix + chunk expansion: chunker.py source_url normalized to always end with "/" (was missing trailing slash, caused Hit@5=0 in eval due to URL string mismatch). Chunk configs expanded from {256,512,900} to {512,900,1536,1900}; size_256 dropped. Two new BGE collections added: msads_bgesmall_size_1536 (485 chunks) and msads_bgesmall_size_1900 (478 chunks). MiniLM capped at 900 chars (~225 tokens); BGE handles 1536/1900 chars safely within its 512-token limit. ChromaDB fully rebuilt from data/raw/.
2026-05-04 — Stage 3 complete: QA pipeline implemented in src/qa/ — page_summaries.json (14 groups, 53 URLs), page_selector.py (LLM picks 2-3 groups → $in filter), qa_pipeline.py (query rewrite + agent loop max 9 steps + completeness judgment + generation), prompt_templates.py, demo.py. retriever.py docstring updated to document $in operator support. 9/9 validate_stage_3.py checks pass.
2026-05-05 — Bug fix (retriever.py): get_by_section() multi-field ChromaDB where clause changed to $and [{$eq},{$eq}] format (ChromaDB 1.5.5 rejects two top-level keys); list_sections() where changed to explicit $eq. demo.py display "/3" corrected to "/9" (matches _MAX_STEPS). System prompt Rule 1/2 clarified: Rule 1 (scope refusal) now only triggers for completely off-topic queries; Rule 2 (no info found) now includes combined "not found + scope" response so MSADS-related but uncovered questions get both messages.
2026-05-06 — Stage 4 complete: pipeline upgraded (history/stream/progress_cb params, multi-question fan-out via _detect_subquestions, inline [N] citations); Streamlit UI in src/ui/app.py (streaming, history compression, linkified citations, DSI sidebar logo, New Conversation button); validate_stage_3.py extended to 10 checks; validate_stage_4.py added (14/14 pass).
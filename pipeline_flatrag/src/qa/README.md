# Stage 3 — QA Pipeline (upgraded in Stage 4)

Hand-written RAG orchestration: no LangChain. The pipeline detects sub-questions, rewrites each query, runs an LLM agent loop that calls retrieval tools, assembles the retrieved context, and generates an answer via DeepSeek-chat.

**Entry point:** `python src/qa/demo.py`

### Stage 4 upgrades to the pipeline

| Feature | Details |
|---|---|
| **Multi-question fan-out** | `_detect_subquestions()` splits compound queries into independent sub-questions (one LLM call). Each sub-question gets its own rewrite + agent loop. Chunks are merged by `chunk_id` dedup before a single generation call. |
| **Multi-turn history** | `run(history=)` accepts a compressed conversation history (summary + recent turns). Passed to `_rewrite_query()` for coreference resolution and to `_generate()` for conversational context. |
| **Streaming generation** | `run(stream=True)` returns the raw OpenAI streaming iterator instead of a string. The Streamlit UI consumes it token-by-token. |
| **Progress callback** | `run(progress_cb=)` accepts a callable invoked at each key step (rewrite, tool call, judger, generation) so the UI can show live status. |
| **Inline citations** | `SYSTEM_PROMPT` Rule 5 updated: LLM outputs `[1]`, `[2]` inline markers and a numbered `Sources:` list. The UI post-processes these into clickable hyperlinks. |
| **Conversation history sidebar** | "New Conversation" saves the current session (title = first 40 chars of first user message) to `st.session_state.conversations`; past sessions appear as buttons in the sidebar and can be restored by clicking. |

---

## Pipeline Flow

```
run(query, history=None, stream=False, progress_cb=None) → dict
    │
    ▼
⓪ _detect_subquestions    [LLM call — deepseek-chat]
    Split compound queries into independent sub-questions.
    e.g. "What are the core courses? And the tuition?"
       → ["What are the core courses?", "What is the tuition?"]
    Single questions returned unchanged as [query].
    │
    ▼  (loop over each sub-question)
① _rewrite_query          [LLM call — deepseek-chat, per sub-question]
    Rephrase question as a declarative statement to improve embedding similarity.
    Resolves coreferences using conversation history if provided.
    e.g. "What about the online version?" + history →
         "The core courses required in the online MSADS program are..."
    │
    ▼
② _run_agent_loop         [LLM agent — up to MAX_STEPS=9 tool calls, per sub-question]
    Agent autonomously calls three tools to gather relevant chunks:

    list_page_sections(url)
        → shows all section_breadcrumbs + chunk counts for a page
        → no retrieval; used to understand page structure before targeting a section

    fetch_section_chunks(url, section_breadcrumb)
        → calls retriever.get_by_section() — exact ChromaDB metadata lookup
        → fuzzy-matches breadcrumb if LLM provides a slightly wrong name
        → returns all chunks from that section

    semantic_search(query, include_urls, exclude_urls, exclude_chunk_ids, top_k)
        → calls retriever.retrieve() — vector similarity search
        → include_urls restricts to specific pages (uses $in filter)
        → exclude_* used to avoid already-retrieved chunks

    After each fetch_section_chunks / semantic_search call, the Judger LLM evaluates:
        • completeness: "full" | "partial" | "none"
        • useful_indices: which of the new chunks are actually relevant
    Useful chunks are accumulated across steps. Loop exits when:
        • completeness == "full"
        • completeness == "partial" and accumulated ≥ MIN_PARTIAL_CHUNKS=2
        • MAX_STEPS reached
    │
    ▼  (merge chunks from all sub-questions, deduplicated by chunk_id)
③ format_context           [prompt_templates.py]
    Format accumulated chunks as:
        [Source: Page Title | Section Breadcrumb]
        [TIME-SENSITIVE]  ← injected if content_type == "time_sensitive"
        chunk text
    Chunks separated by "---"
    │
    ▼
④ _generate               [LLM call — deepseek-chat, single call for all sub-questions]
    Receives merged context + optional conversation history.
    When stream=True: returns raw streaming iterator (UI consumes token-by-token).
    System prompt enforces:
        • Scope guard: off-topic questions → canned refusal
        • Insufficient context: MSADS-related but not in corpus → combined "not found" message
        • TIME-SENSITIVE disclaimer when context includes deadline/event info
        • Career Outcomes image-limitation disclaimer
        • Numbered inline citations [1][2] + matching numbered Sources: list
        • No fabrication; no PII (emails, phone numbers)
    │
    ▼
⑤ _write_log              [data/qa_log/<timestamp>_<slug>.json]
    One JSON file per run: query, rewritten query, agent steps, useful chunks, result.
    Log failure never breaks the pipeline (silent except).
    │
    ▼
result dict:
    answer        str | streaming iterator  — str when stream=False (default); raw iterator when stream=True
    sources       list[{page_title, source_url}]   — deduped by URL, across all sub-questions
    completeness  "full" | "partial" | "none"       — best across all sub-questions
    low_confidence bool
    attempts      int   — total agent steps taken across all sub-questions
```

---

## Key Configuration (qa_pipeline.py)

| Constant | Value | Meaning |
|---|---|---|
| `_COLLECTION` | `msads_bgesmall_size_900` | ChromaDB collection used for retrieval |
| `_MODEL_NAME` | `BAAI/bge-small-en-v1.5` | Embedding model (BGE prefix applied to queries) |
| `_LLM_MODEL` | `deepseek-chat` | LLM for rewrite, agent, judger, and generation |
| `_TOP_K` | 7 | Chunks per semantic_search call |
| `_MAX_STEPS` | 9 | Maximum agent tool-call steps per query |
| `_MIN_PARTIAL_CHUNKS` | 2 | Min chunks to accept a "partial" completeness verdict |

---

## File Descriptions

### `qa_pipeline.py`
Main pipeline class `QAPipeline`. Public API: `pipeline.run(query, history=None, stream=False, progress_cb=None) → dict`.

- `__init__`: loads Retriever, PageSelector (for page group data), builds agent system prompt
- `_detect_subquestions(query)`: one LLM call; returns list of independent sub-questions, or `[query]` if single
- `_rewrite_query(query, history)`: one LLM call to convert question → declarative statement; resolves coreferences from history
- `_run_agent_loop(rewritten, original, log_steps, progress_cb)`: drives the agent with OpenAI-compatible tool-calling; accumulates useful chunks; calls `progress_cb` at each tool call and after each judger
- `_judge`: one LLM call per retrieval step; parses `{"completeness": ..., "useful_indices": [...]}` JSON
- `_generate(query, context_str, confidence_note, history, stream)`: final answer generation; returns str or streaming iterator
- `_write_log`: writes JSON log to `data/qa_log/`
- `build_pipeline()`: convenience factory; reads `DEEPSEEK_API_KEY` from `.env`

### `agent_tools.py`
Tool schemas (OpenAI function-calling format) and execution logic.

- `TOOL_SCHEMAS`: list of 3 tool definitions passed to the LLM as `tools=`
- `execute_tool(name, args, retriever, useful_ids, summaries)`: dispatcher
- `_list_page_sections(url, retriever)`: calls `retriever.list_sections(url)`
- `_fetch_section_chunks(url, bc, retriever)`: calls `retriever.get_by_section(url, bc)` with fuzzy-match fallback (exact → case-insensitive → difflib, cutoff=0.6)
- `_semantic_search(args, retriever, useful_ids)`: calls `retriever.retrieve()` with optional `$in` filter; post-filters excluded URLs and chunk IDs
- `_resolve_single_url / _resolve_url_list`: expand page group labels (e.g. `"in_person"`) to canonical URLs using `page_summaries.json`

### `page_selector.py`
`PageSelector` class — loads `page_summaries.json` and exposes two things used by the pipeline:

- `_summaries`: the raw list of page groups, consumed by `build_agent_system_prompt()` to tell the agent which page groups exist
- `urls_for_labels(labels)`: expand label list to flat URL list (used if `select()` is called externally)
- `select(query, client)`: LLM call that picks 2-3 most relevant page groups for a query — **not currently called in the main pipeline loop**; the agent handles page selection autonomously via tool calls

### `page_summaries.json`
Static data file: 14 page groups covering all 53 corpus URLs.

Each entry:
```json
{
  "label": "in_person",
  "description": "...",
  "urls": ["https://...", ...]
}
```
Used to build the agent system prompt (group list) and to resolve group labels in tool calls.

### `prompt_templates.py`
All prompt strings and formatting functions. No LLM calls here.

- `SYSTEM_PROMPT`: grounding rules for the final generation step (scope guard, insufficient-context response, TIME-SENSITIVE disclaimer, Career Outcomes caveat, numbered inline citations `[N]` + Sources list, no-fabrication rule)
- `build_agent_system_prompt(summaries)`: dynamically generates the agent loop system prompt from page group data
- `format_rewrite_prompt(query, history=None)`: prompt for Step ①; when `history` is provided, prepends conversation history block for coreference resolution
- `build_generation_messages(query, context_str, confidence_note, history=None) → list[dict]`: builds the `messages` list for the generation LLM call; inserts history turns (user/assistant/summary-system) between the system prompt and the final user message
- `format_judgment_prompt(query, new_chunks, accumulated)`: prompt for the per-step judger; returns JSON `{completeness, useful_indices}`
- `format_context(chunks)`: formats chunk list into context string with [TIME-SENSITIVE] markers; returns `(context_str, has_time_sensitive)`

### `demo.py`
Interactive CLI loop. Prints answer, completeness, attempt count, and sources. No test assertions — pure demo.

Run:
```bash
conda activate adsp-nlp-backup
python src/qa/demo.py
```

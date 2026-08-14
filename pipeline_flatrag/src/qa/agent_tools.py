"""
Tool schemas and execution logic for the QA agent loop.

Three tools are exposed to the LLM agent:
  list_page_sections   — inspect a page's section structure (no retrieval)
  fetch_section_chunks — targeted retrieval by exact section breadcrumb
  semantic_search      — vector similarity retrieval with optional URL filters
"""

import difflib

from src.retrieval.retriever import Retriever

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_page_sections",
            "description": (
                "List all section breadcrumbs and chunk counts for a page. "
                "Use this to understand a page's structure before deciding which section to target."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "Page group label (e.g. 'career_outcomes') OR canonical page URL. "
                            "Labels are resolved automatically to their canonical URL."
                        ),
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_section_chunks",
            "description": (
                "Retrieve all text chunks from a specific section of a page. "
                "Use when you know which section is most likely to contain the answer. "
                "Fuzzy-matches the breadcrumb if not exact."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "Page group label (e.g. 'career_outcomes') OR canonical page URL. "
                            "Labels are resolved automatically to their canonical URL."
                        ),
                    },
                    "section_breadcrumb": {
                        "type": "string",
                        "description": "Section breadcrumb as returned by list_page_sections.",
                    },
                },
                "required": ["url", "section_breadcrumb"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": (
                "Vector similarity search over the knowledge base. "
                "Use include_urls to restrict to specific pages. "
                "Use exclude_urls and exclude_chunk_ids to avoid already-retrieved content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string.",
                    },
                    "include_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "If set, only search within these pages. "
                            "Each entry can be a page group label or a canonical URL; "
                            "labels are expanded to all their URLs automatically."
                        ),
                    },
                    "exclude_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "If set, exclude chunks from these URLs (full URL only).",
                    },
                    "exclude_chunk_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Exclude specific chunk IDs already accumulated.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def execute_tool(
    name: str,
    args: dict,
    retriever: Retriever,
    useful_ids: set[str],
    summaries: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """Execute a tool call. Returns (formatted_result_str, candidate_chunks).

    candidate_chunks are new chunks (not yet in useful_ids) that the agent
    retrieved. The caller (pipeline) decides whether to promote them to
    useful_chunks based on the judger's verdict.

    summaries: list of page group dicts from page_summaries.json, used to
    resolve group labels to canonical URLs transparently.
    """
    label_map = _build_label_map(summaries or [])

    if name == "list_page_sections":
        url = _resolve_single_url(args.get("url", ""), label_map)
        return _list_page_sections(url, retriever)
    if name == "fetch_section_chunks":
        url = _resolve_single_url(args.get("url", ""), label_map)
        return _fetch_section_chunks(url, args.get("section_breadcrumb", ""), retriever)
    if name == "semantic_search":
        resolved_args = dict(args)
        if args.get("include_urls"):
            resolved_args["include_urls"] = _resolve_url_list(args["include_urls"], label_map)
        return _semantic_search(resolved_args, retriever, useful_ids)
    return f"Unknown tool: {name}", []


# ---------------------------------------------------------------------------
# Label → URL resolution helpers
# ---------------------------------------------------------------------------

def _build_label_map(summaries: list[dict]) -> dict[str, list[str]]:
    """Build a mapping from group label to list of canonical URLs."""
    return {g["label"]: g.get("urls", []) for g in summaries}


def _resolve_single_url(value: str, label_map: dict[str, list[str]]) -> str:
    """Return a canonical URL for value. If value is a label, returns its first URL."""
    if value.startswith("http"):
        return value
    urls = label_map.get(value, [])
    return urls[0] if urls else value


def _resolve_url_list(values: list[str], label_map: dict[str, list[str]]) -> list[str]:
    """Expand a list of labels/URLs to a flat list of canonical URLs."""
    result: list[str] = []
    for v in values:
        if v.startswith("http"):
            result.append(v)
        else:
            result.extend(label_map.get(v, [v]))
    return result


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _list_page_sections(url: str, retriever: Retriever) -> tuple[str, list[dict]]:
    if not url:
        return "Error: url is required.", []
    sections = retriever.list_sections(url)
    if not sections:
        return f"No chunks found for URL: {url}", []
    lines = [f"Sections in {url}:"]
    for s in sections:
        n = s["chunk_count"]
        lines.append(f'  - "{s["section_breadcrumb"]}" ({n} chunk{"s" if n != 1 else ""})')
    return "\n".join(lines), []


def _fetch_section_chunks(
    url: str, section_breadcrumb: str, retriever: Retriever
) -> tuple[str, list[dict]]:
    if not url or not section_breadcrumb:
        return "Error: url and section_breadcrumb are required.", []

    sections = retriever.list_sections(url)
    all_breadcrumbs = [s["section_breadcrumb"] for s in sections]

    if not all_breadcrumbs:
        return f"No sections found for URL: {url}", []

    matched_bc: str | None = None

    # 1. Exact match
    if section_breadcrumb in all_breadcrumbs:
        matched_bc = section_breadcrumb
    else:
        # 2. Case-insensitive match
        lower_map = {bc.lower(): bc for bc in all_breadcrumbs}
        if section_breadcrumb.lower() in lower_map:
            matched_bc = lower_map[section_breadcrumb.lower()]
        else:
            # 3. Fuzzy match
            close = difflib.get_close_matches(section_breadcrumb, all_breadcrumbs, n=1, cutoff=0.6)
            if close:
                matched_bc = close[0]

    if matched_bc is None:
        available = "\n".join(f'  - "{bc}"' for bc in all_breadcrumbs)
        return (
            f'Section not found: "{section_breadcrumb}"\n'
            f"Available sections for this page:\n{available}"
        ), []

    chunks = retriever.get_by_section(url, matched_bc)
    if not chunks:
        return f'Section "{matched_bc}" exists but contains no chunks.', []

    note = f' (fuzzy-matched to: "{matched_bc}")' if matched_bc != section_breadcrumb else ""
    lines = [f'Fetched {len(chunks)} chunk(s) from "{matched_bc}"{note}:']
    for i, c in enumerate(chunks):
        preview = c["text"][:300] + ("..." if len(c["text"]) > 300 else "")
        lines.append(f'\n[{i}] {c["page_title"]} | {c["section_breadcrumb"]}\n{preview}')
    return "\n".join(lines), chunks


def _semantic_search(
    args: dict, retriever: Retriever, useful_ids: set[str]
) -> tuple[str, list[dict]]:
    query = args.get("query", "")
    if not query:
        return "Error: query is required.", []

    include_urls = args.get("include_urls") or None
    exclude_urls = set(args.get("exclude_urls") or [])
    exclude_chunk_ids = set(args.get("exclude_chunk_ids") or [])
    top_k = int(args.get("top_k") or 5)

    where: dict | None = None
    if include_urls:
        where = {"source_url": {"$in": include_urls}}

    try:
        chunks = retriever.retrieve(query, top_k=top_k, where=where)
    except Exception:
        chunks = retriever.retrieve(query, top_k=top_k)

    # Post-filter excluded URLs and IDs
    all_excluded_ids = useful_ids | exclude_chunk_ids
    chunks = [
        c for c in chunks
        if c["source_url"] not in exclude_urls and c["chunk_id"] not in all_excluded_ids
    ]

    if not chunks:
        return "Semantic search returned no new results.", []

    lines = [f"Semantic search returned {len(chunks)} result(s):"]
    for i, c in enumerate(chunks):
        preview = c["text"][:300] + ("..." if len(c["text"]) > 300 else "")
        lines.append(
            f'\n[{i}] {c["page_title"]} | {c["section_breadcrumb"]} '
            f'(distance: {c["distance"]:.3f})\n{preview}'
        )
    return "\n".join(lines), chunks

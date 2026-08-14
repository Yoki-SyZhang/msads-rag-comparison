"""Prompt contracts for GRAG v2: rewrite, graph-navigation decision, per-node Judge,
Summarizer workflow, fallback page selection, and final answer generation.

See PLAN.md §3 for the full design rationale behind each prompt's scope.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .schemas import AgentContext, EvidenceItem, ToolExecution


# ----------------------------------------------------------------------
# Query rewrite (unchanged from pipeline_grag_fixed)
# ----------------------------------------------------------------------

QUERY_REWRITE_SYSTEM = """You rewrite the current university-program question into declarative retrieval queries.
Use conversation history only to resolve references. Split genuinely compound questions when useful.
Return JSON only with key rewritten_queries; never answer the question."""


def query_rewrite_user(query: str, history: List[Dict[str, Any]]) -> str:
    history_text = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history) or "(none)"
    return f"""Conversation history:
{history_text}

Current question: {query}

Return:
{{"rewritten_queries":[{{"id":"q1","query":"...","target":"..."}}]}}"""


# ----------------------------------------------------------------------
# Decision agent — 2 tools only (inspect_page, fetch_graph_content), page
# summaries baked in as long-term memory, no "finish" action (§3.5/§3.6).
# ----------------------------------------------------------------------

_AGENT_DECISION_TEMPLATE = """ROLE
You are the graph-navigation planner for a UChicago MS in Applied Data Science RAG system.
This system answers questions primarily by navigating a structured knowledge graph, not by
generic full-text search.

TASK
Choose exactly one next action every turn.

LONG-TERM MEMORY: ALL PROGRAM PAGES
{page_summaries_block}

TOOLS
- inspect_page(page_ref): show a page's structural outline as N refs. Large branches are
  collapsed with a content preview. You may inspect any page, including ones you've already
  inspected before, at any point — re-inspecting is cheap and useful after learning new
  information about what's NOT relevant elsewhere.
- fetch_graph_content(node_refs): fetch content from 1-5 available N refs at once.

STRATEGY
1. Pick the 1-3 most relevant pages directly from the long-term memory list; inspect the most
   likely one first.
2. Use path names and preview hints in the outline to pick specific nodes; do not fetch a large
   branch when a smaller labeled child is already visible.
3. Batch multiple sibling nodes (up to 5) in one fetch_graph_content call when plausibly relevant.
4. Read the investigation summary before deciding — it tells you what has already been found
   useful/not useful by path, and what the decision history has already tried.
5. If the summary says a page/branch was unproductive, re-inspect a different page from the
   long-term memory list, or re-inspect the same page if you now have new context that changes
   which branch looks promising.

RULES
- Use only P/N/E refs actually shown in this context. Never invent or transform a ref.
- Tool arguments must contain the bare ref token only, e.g. P4 or N7.
- Always state your reasoning in "reason" — every decision is recorded for audit.

OUTPUT FORMAT
Return JSON only:
{{"action":"inspect_page|fetch_graph_content","args":{{}},"reason":"..."}}
For inspect_page use args.page_ref (a single bare P ref, e.g. "P3").
For fetch_graph_content use args.node_refs (a list of 1-5 bare N refs, e.g. ["N7","N9"]).
Never omit args — every action requires its argument key to be present."""


def build_agent_decision_system(context: AgentContext) -> str:
    pages = context.refs.entries("page")
    lines = [f"- [{p.ref}] {p.label}: {p.payload.get('description', '')}" for p in pages]
    return _AGENT_DECISION_TEMPLATE.format(page_summaries_block="\n".join(lines) or "(none)")


def _available_node_refs(context: AgentContext) -> str:
    nodes = context.refs.entries("node")
    if not nodes:
        return "(no nodes inspected yet)"
    lines = []
    for entry in nodes:
        count = entry.payload.get("content_count", 0)
        page_ref = entry.payload.get("page_ref", "")
        line = f"- [{entry.ref}] {entry.label} ({count} content blocks; page {page_ref})"
        if entry.ref in context.too_broad_node_refs:
            children = entry.payload.get("child_node_refs", [])
            redirect = f" — try {', '.join(children)} instead" if children else ""
            line += f" [ALREADY REJECTED: too broad{redirect}]"
        lines.append(line)
    return "\n".join(lines)


def _accepted_evidence_catalog(context: AgentContext) -> str:
    accepted = context.evidence.accepted()
    if not accepted:
        return "(none yet)"
    lines = []
    for item in accepted:
        preview = " ".join(item.text.split())[:180]
        lines.append(f"- [{item.evidence_ref}] {item.page_title} > {' > '.join(item.path)}: {preview}")
    return "\n".join(lines)


def _latest_progress_summary(context: AgentContext) -> str:
    if not context.progress_summary_history:
        return "(no investigation yet — this is the first decision)"
    latest = context.progress_summary_history[-1]
    return (
        f"Coverage so far:\n{latest.get('summary_covering', '')}\n\n"
        f"Decision history so far:\n{latest.get('decision_trace_narrative', '')}"
    )


def agent_decision_user(context: AgentContext) -> str:
    rewrites = "\n".join(
        f"- {item.get('query', '')}" for item in context.query_state.rewritten_queries
    ) or f"- {context.query_state.original_query}"
    remaining = context.max_turns - context.turn
    return f"""Original question:
{context.query_state.original_query}

Rewritten retrieval queries:
{rewrites}

Investigation summary:
{_latest_progress_summary(context)}

Available node refs (from pages already inspected):
{_available_node_refs(context)}

Accepted evidence so far:
{_accepted_evidence_catalog(context)}

Agent turns remaining after this decision: {remaining}

Choose one action and return the required JSON object."""


# ----------------------------------------------------------------------
# Per-node Judge — assesses only the current fetch batch; never decides
# overall sufficiency (that is the Summarizer's job, §3.4/§3.5).
# ----------------------------------------------------------------------

EVIDENCE_JUDGE_SYSTEM = """You are the per-node evidence Judge for a university RAG system.
You receive one or more fetched top-level nodes, each with its own sub-structure and leaf
content blocks (E refs). For every top-level node requested, decide which E refs directly
support answering the question.

Rules:
- Judge every requested top-level node ref, even if none of its content is useful.
- keep_evidence may contain only E refs actually shown under that node.
- A block marked "(structure summary)" can support directory/membership/ordering claims.
- Do not judge overall sufficiency across the whole investigation — that is not your job.
- The comment must state concretely what information the kept content provides, and/or what
  this node covers that is not useful for the question — never a generic verdict alone.
Return JSON only: {"node_assessments": [{"node_ref": "N9", "keep_evidence": ["E12"],
"comment": "..."}]}"""


def evidence_judge_user(context: AgentContext, execution: ToolExecution) -> str:
    return f"""Original question: {context.query_state.original_query}

Fetched content (grouped by requested node):
{execution.llm_visible_result}

Return one node_assessment per requested top-level node ref: {', '.join(execution.candidate_groups)}"""


# ----------------------------------------------------------------------
# Summarizer workflow — maintains the cumulative investigation state and
# decides sufficiency; invoked by code every turn, never by the LLM (§3.5).
# ----------------------------------------------------------------------

PROGRESS_SUMMARY_SYSTEM = """You maintain the cumulative investigation state for a graph-navigation
retrieval agent and decide whether the question is now answered.

You receive: the page-grouped node evaluation log so far (which pages/paths have been checked
and what was found), this turn's raw tool status (fetch success / already-fetched / invalid
reference / scope too broad / etc), and the full accepted-evidence catalog.

Return two separate pieces of text:
1. summary_covering — organized BY PAGE. For each page touched so far, list which node paths
   were checked and whether they were useful or not, in one or two sentences per page. Then
   state what is still missing.
2. decision_trace_narrative — a short narrative of what the decision agent has tried, turn by
   turn, and why (drawn from each turn's stated reason and outcome) — this is a running audit
   trail of the agent's own behavior, not information content.

Rules:
- Never invent facts not present in the node evaluation log or accepted evidence.
- If a turn re-queried an already-fetched node, say so explicitly in decision_trace_narrative
  and do not count it as new information in summary_covering.
- sufficient=true only if every part of the question is covered by accepted evidence.
Return JSON: {"summary_covering": "...", "decision_trace_narrative": "...",
"sufficient": false, "missing": "..."}"""


def _render_node_evaluation_log(context: AgentContext) -> str:
    if not context.node_evaluation_log:
        return "(nothing checked yet)"
    blocks = []
    for page_key, nodes in context.node_evaluation_log.items():
        lines = [f"Page: {page_key}"]
        for node_key, evaluation in nodes.items():
            has_evidence = "yes" if evaluation.eff_evidence else "no"
            lines.append(
                f"  - {node_key} (path: {' > '.join(evaluation.path)}) — useful evidence: {has_evidence}"
                f" — {evaluation.comment}"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def progress_summary_user(
    context: AgentContext, this_turn_tool: str, this_turn_status: str, this_turn_reason: str,
) -> str:
    return f"""Original question: {context.query_state.original_query}

This turn: {this_turn_tool} -> {this_turn_status} (agent's stated reason: {this_turn_reason})

Page-grouped node evaluation log so far:
{_render_node_evaluation_log(context)}

Accepted evidence catalog:
{_accepted_evidence_catalog(context)}

Return the updated investigation state now."""


# ----------------------------------------------------------------------
# Fallback page selection (only invoked once the main loop trips a
# boundary condition — see PLAN.md §3.8).
# ----------------------------------------------------------------------

FALLBACK_PAGE_SELECT_SYSTEM = """Graph navigation did not produce sufficient evidence.
Pick up to 5 pages from the program page list most likely to contain the answer, given the
question and the investigation summary so far.
Return JSON: {"page_refs": ["P1", ...], "reason": "..."}"""


def fallback_page_select_user(context: AgentContext) -> str:
    pages = context.refs.entries("page")
    lines = [f"- [{p.ref}] {p.label}: {p.payload.get('description', '')}" for p in pages]
    return f"""Original question: {context.query_state.original_query}

All program pages:
{chr(10).join(lines)}

Investigation summary so far:
{_latest_progress_summary(context)}

Return up to 5 page refs."""


# ----------------------------------------------------------------------
# Final answer generation (unchanged in shape from pipeline_grag_fixed)
# ----------------------------------------------------------------------

ANSWER_GENERATION_SYSTEM = """You answer questions about the UChicago MS in Applied Data Science program.
Use only the supplied accepted evidence. Cite supporting statements with [1], [2], and so on.
Do not expose internal P/N/E refs, graph IDs, chunk IDs, page IDs, URLs, scores, or metadata.
Never use square brackets for prose labels; every bracketed marker must be a numeric citation.
SCOPE: If the question is completely unrelated to the UChicago MS in Applied Data Science program
(e.g. general knowledge, other universities, unrelated topics like cooking or weather), reply
exactly: "I can only answer questions about the UChicago MS in Applied Data Science program." —
do not attempt to answer it even if some of the supplied evidence looks superficially related.
NO EVIDENCE: If no evidence was supplied at all, first judge whether the question is in scope:
- Off-topic/unrelated to the program: reply exactly with the SCOPE message above.
- On-topic but simply not covered by the supplied evidence: reply exactly: "I couldn't find
  relevant information about this in the available program materials. I can only answer
  questions about the UChicago MS in Applied Data Science program."
If the supplied completeness note says evidence is incomplete, explicitly identify the unsupported gap.
Write a direct, concise answer."""


def _full_evidence(items: List[EvidenceItem], numbered: bool = False) -> str:
    if not items:
        return "(none)"
    blocks = []
    for index, item in enumerate(items, start=1):
        ref = str(index) if numbered else item.evidence_ref
        kind = f" ({item.visible_type})" if item.visible_type == "structure summary" else ""
        blocks.append(f"[{ref}] {item.page_title} > {' > '.join(item.path)}{kind}\n{item.text}")
    return "\n\n".join(blocks)


def answer_generation_user(context: AgentContext) -> str:
    completeness = (
        "Evidence is complete."
        if context.judgment.sufficient
        else f"Evidence may be incomplete. Missing: {context.judgment.missing}"
    )
    return f"""Question: {context.query_state.original_query}

Completeness note: {completeness}

Investigation coverage summary (organized by page, from the graph-navigation workflow):
{context.judgment.comment or "(no investigation was recorded — evidence came directly from a fallback retrieval.)"}

Accepted evidence:
{_full_evidence(context.evidence.accepted(), numbered=True)}

Answer using only this evidence and numeric inline citations."""

"""GRAG v2 agent loop: graph-navigation-only main loop (inspect_page / fetch_graph_content),
a per-node Judge, a Summarizer workflow step that owns the sufficiency verdict, and a
bounded scoped-hybrid-retrieve fallback phase. See PLAN.md §1-3 for the full design —
this is deliberately a workflow with one bounded agentic decision point (§1), not a
free-roaming multi-tool agent.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .prompts import (
    EVIDENCE_JUDGE_SYSTEM,
    FALLBACK_PAGE_SELECT_SYSTEM,
    PROGRESS_SUMMARY_SYSTEM,
    agent_decision_user,
    build_agent_decision_system,
    evidence_judge_user,
    fallback_page_select_user,
    progress_summary_user,
)
from .query_rewriter import rewrite_query
from .references import ReferenceRegistry
from .schemas import (
    AgentContext,
    ChatMessage,
    Judgment,
    NodeEvaluation,
    QueryState,
    ToolCallRecord,
    ToolExecution,
)


MAX_AGENT_TURNS = 9

# Fallback boundary conditions — see PLAN.md §3.8. Together these three conditions are
# a complete union: (1) catches "kept trying to fetch new nodes but never got there",
# (2) catches "kept failing/repeating fetch attempts", (3) catches "never even
# attempted a fetch" (a gap in a naive two-condition design, since valid_fetch_count
# and invalid_fetch_count would both stay 0 forever in that case).
FALLBACK_VALID_FETCH_LIMIT = 5
FALLBACK_INVALID_FETCH_LIMIT = 3
FALLBACK_STUCK_TURN_LIMIT = 5


def _fallback_decision(context: AgentContext) -> Dict[str, Any]:
    """Used only if the decision LLM call itself raises (network/parse error) — picks
    a safe, deterministic next step so a transient API failure doesn't stall a turn."""
    fetched_real_ids = {
        real_id
        for record in context.tool_history
        if record.tool == "fetch_graph_content" and record.status in {"OK", "OK_WITH_ALREADY_CHECKED"}
        for real_id in record.resolved_real_ids
    }
    unfetched = [n for n in context.refs.entries("node") if n.real_id not in fetched_real_ids]
    if unfetched:
        unfetched.sort(key=lambda n: n.payload.get("content_count", 0))
        return {"action": "fetch_graph_content", "args": {"node_refs": [unfetched[0].ref]}, "reason": "decision fallback"}
    pages = context.refs.entries("page")
    target = pages[0].ref if pages else ""
    return {"action": "inspect_page", "args": {"page_ref": target}, "reason": "decision fallback"}


def _sanitize_assessments(raw: Any, execution: ToolExecution) -> List[Dict[str, Any]]:
    supplied_groups = set(execution.candidate_groups)
    supplied_evidence = set(execution.candidate_evidence_refs)
    assessments: List[Dict[str, Any]] = []
    seen = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            group = str(item.get("node_ref", ""))
            if group not in supplied_groups or group in seen:
                continue
            seen.add(group)
            keep = [str(ref).upper() for ref in item.get("keep_evidence", []) if str(ref).upper() in supplied_evidence]
            assessments.append({
                "node_ref": group,
                "keep_evidence": list(dict.fromkeys(keep)),
                "comment": str(item.get("comment", "")).strip() or "No directional comment returned.",
            })
    for group in execution.candidate_groups:
        if group not in seen:
            assessments.append({
                "node_ref": group, "keep_evidence": [],
                "comment": "Judge returned no assessment for this group.",
            })
    return assessments


def _judge(context: AgentContext, execution: ToolExecution, client: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    try:
        result = client.chat_json(EVIDENCE_JUDGE_SYSTEM, evidence_judge_user(context, execution))
        assessments = _sanitize_assessments(result.get("node_assessments", []), execution)
    except Exception as exc:
        assessments = [
            {
                "node_ref": group,
                "keep_evidence": group_refs[:10],
                "comment": f"Judge was unavailable; retained a bounded candidate set for cautious fallback ({exc}).",
            }
            for group, group_refs in execution.candidate_groups.items()
        ]

    requested_keeps: List[str] = []
    for assessment in assessments:
        requested_keeps.extend(assessment["keep_evidence"])
    requested_keeps = list(dict.fromkeys(requested_keeps))

    kept: List[str] = []
    newly_accepted: List[str] = []
    for ref in requested_keeps:
        item = context.evidence.get(ref)
        if not item:
            continue
        if item.status == "accepted":
            kept.append(ref)
            continue
        if len(newly_accepted) >= 10:
            continue
        item.status = "accepted"
        newly_accepted.append(ref)
        kept.append(ref)
    for ref in execution.candidate_evidence_refs:
        item = context.evidence.get(ref)
        if item and item.status == "candidate":
            item.status = "discarded"

    kept_set = set(kept)
    for assessment in assessments:
        assessment["keep_evidence"] = [ref for ref in assessment["keep_evidence"] if ref in kept_set]
    return assessments, kept


def _update_node_evaluation_log(context: AgentContext, assessments: List[Dict[str, Any]]) -> None:
    for assessment in assessments:
        node_ref = assessment["node_ref"]
        node_entry = context.refs.resolve(node_ref, "node")
        if node_entry is None:
            continue  # e.g. the fallback phase's "hybrid" group_ref is not a node ref
        page_ref = node_entry.payload.get("page_ref", "")
        page_entry = context.refs.resolve(page_ref, "page") if page_ref else None
        page_key = f"{page_ref} ({page_entry.label})" if page_entry else (page_ref or "(unknown page)")
        node_key = f"{node_ref} ({node_entry.label})"
        eff_evidence = []
        for ref in assessment.get("keep_evidence", []):
            item = context.evidence.get(ref)
            if item:
                eff_evidence.append({"evidence_ref": ref, "chunk_id": item.chunk_id, "text_preview": item.text[:150]})
        context.node_evaluation_log.setdefault(page_key, {})[node_key] = NodeEvaluation(
            node_id=node_entry.real_id,
            path=list(node_entry.payload.get("path", [])),
            eff_evidence=eff_evidence,
            comment=assessment.get("comment", ""),
        )


def _run_summarizer(context: AgentContext, tool: str, status: str, reason: str, client: Any) -> None:
    try:
        result = client.chat_json(
            PROGRESS_SUMMARY_SYSTEM,
            progress_summary_user(context, tool, status, reason),
        )
        summary_covering = str(result.get("summary_covering", "")).strip()
        decision_trace_narrative = str(result.get("decision_trace_narrative", "")).strip()
        sufficient = bool(result.get("sufficient", False))
        missing = str(result.get("missing", "")).strip()
    except Exception as exc:
        previous = context.progress_summary_history[-1] if context.progress_summary_history else {}
        summary_covering = previous.get("summary_covering", "")
        decision_trace_narrative = previous.get("decision_trace_narrative", "")
        sufficient = False
        missing = f"Summarizer was unavailable this turn ({exc})."

    context.progress_summary_history.append({
        "turn": context.turn,
        "summary_covering": summary_covering,
        "decision_trace_narrative": decision_trace_narrative,
        "sufficient": sufficient,
        "missing": missing,
    })
    context.judgment = Judgment(sufficient=sufficient, missing=missing, comment=summary_covering)


def _should_fallback(context: AgentContext) -> bool:
    if context.judgment.sufficient:
        return False
    if context.valid_fetch_count > FALLBACK_VALID_FETCH_LIMIT:
        return True
    if context.valid_fetch_count <= FALLBACK_VALID_FETCH_LIMIT and context.invalid_fetch_count > FALLBACK_INVALID_FETCH_LIMIT:
        return True
    if context.turn >= FALLBACK_STUCK_TURN_LIMIT and context.valid_fetch_count == 0 and context.invalid_fetch_count == 0:
        return True
    return False


def _run_fallback(context: AgentContext, tools: Any, client: Any, progress) -> None:
    try:
        decision = client.chat_json(FALLBACK_PAGE_SELECT_SYSTEM, fallback_page_select_user(context))
        page_refs = decision.get("page_refs", []) if isinstance(decision.get("page_refs", []), list) else []
        select_reason = str(decision.get("reason", "")).strip()
    except Exception as exc:
        page_refs = []
        select_reason = f"fallback page-select unavailable ({exc}); defaulting to first 5 pages"

    real_page_ids: set = set()
    resolved_refs: List[str] = []
    for ref in page_refs[:5]:
        entry = context.refs.resolve(str(ref).strip().upper(), "page")
        if entry:
            real_page_ids.add(entry.real_id)
            resolved_refs.append(entry.ref)
    if not real_page_ids:
        for entry in context.refs.entries("page")[:5]:
            real_page_ids.add(entry.real_id)
            resolved_refs.append(entry.ref)

    context.fallback = {"triggered": True, "pages_selected": resolved_refs, "reason": select_reason}

    query = context.query_state.rewritten_queries[0].get("query", context.query_state.original_query)
    progress("Running scoped fallback hybrid retrieval...")
    execution = tools.hybrid_retrieve(query, real_page_ids, context)
    kept: List[str] = []
    assessments: List[Dict[str, Any]] = []
    if execution.status == "OK" and execution.candidate_evidence_refs:
        assessments, kept = _judge(context, execution, client)

    context.tool_history.append(ToolCallRecord(
        turn=context.turn + 1,
        tool="hybrid_retrieve",
        normalized_arguments=execution.normalized_arguments,
        resolved_real_ids=execution.resolved_real_ids,
        canonical_signature=execution.canonical_signature,
        status=execution.status,
        cached_result=execution.llm_visible_result,
        llm_visible_result=execution.llm_visible_result,
        decision_reason="fallback: scoped hybrid retrieve",
        candidate_evidence_refs=execution.candidate_evidence_refs,
        kept_evidence_refs=kept,
        node_assessments=assessments,
    ))
    context.stop_reason = "fallback_hybrid_retrieve"


def run(
    query: str,
    history: List[Dict[str, Any]],
    tools: Any,
    client: Any,
    *,
    max_turns: int = MAX_AGENT_TURNS,
    progress_cb: Optional[Any] = None,
) -> AgentContext:
    def progress(message: str) -> None:
        if progress_cb:
            progress_cb(message)

    messages = [
        ChatMessage(role=message.get("role", "user"), content=message.get("content", ""))
        for message in history
        if message.get("role") in {"user", "assistant"}
    ]
    context = AgentContext(
        query_state=QueryState(original_query=query, conversation_history=messages),
        refs=ReferenceRegistry(),
        max_turns=max_turns,
    )

    progress("Rewriting the current query...")
    context.query_state.rewritten_queries = rewrite_query(query, history, client)

    progress("Loading page summaries as long-term memory...")
    tools.load_page_summaries(context)

    fell_back = False
    for turn in range(1, max_turns + 1):
        context.turn = turn
        progress(f"Choosing the next graph-navigation action ({turn}/{max_turns})...")
        try:
            decision = client.chat_json(build_agent_decision_system(context), agent_decision_user(context))
        except Exception:
            decision = _fallback_decision(context)
        action = str(decision.get("action", "")).strip()
        args = decision.get("args", {}) if isinstance(decision.get("args", {}), dict) else {}
        reason = str(decision.get("reason", "")).strip()

        progress(f"Running {action or '(invalid)'} ({turn}/{max_turns})...")
        execution: ToolExecution = tools.dispatch(action, args, context)

        assessments: List[Dict[str, Any]] = []
        kept: List[str] = []
        if execution.fact_producing and execution.executed and execution.status in {"OK", "OK_WITH_ALREADY_CHECKED"}:
            progress(f"Judging fetched content ({turn}/{max_turns})...")
            assessments, kept = _judge(context, execution, client)

        context.tool_history.append(ToolCallRecord(
            turn=turn,
            tool=execution.tool,
            normalized_arguments=execution.normalized_arguments,
            resolved_real_ids=execution.resolved_real_ids,
            canonical_signature=execution.canonical_signature,
            status=execution.status,
            cached_result=execution.llm_visible_result,
            llm_visible_result=execution.llm_visible_result,
            decision_reason=reason,
            candidate_evidence_refs=execution.candidate_evidence_refs,
            kept_evidence_refs=kept,
            node_assessments=assessments,
            debug=execution.debug,
        ))
        _update_node_evaluation_log(context, assessments)

        if execution.tool == "fetch_graph_content":
            if execution.is_valid_fetch:
                context.valid_fetch_count += 1
            else:
                context.invalid_fetch_count += 1

        progress(f"Updating the investigation summary ({turn}/{max_turns})...")
        _run_summarizer(context, execution.tool, execution.status, reason, client)

        if context.judgment.sufficient:
            context.stop_reason = "sufficient_evidence"
            break
        if _should_fallback(context):
            fell_back = True
            break
    else:
        context.stop_reason = "max_agent_turns"

    if fell_back:
        _run_fallback(context, tools, client, progress)
    elif not context.evidence.accepted():
        context.stop_reason = "empty_evidence"

    progress(f"Agent stopped: {context.stop_reason}")
    return context

"""Main agent loop: rewrite → tool calls → judge → evidence accumulation."""

from __future__ import annotations

import uuid
import re
from typing import Any, Dict, List, Optional

from agent.deepseek_client import DeepSeekClient
from agent.prompts import (
    AGENT_DECISION_SYSTEM,
    EVIDENCE_JUDGE_SYSTEM,
    agent_decision_user,
    evidence_judge_user,
)
from agent.query_rewriter import rewrite_query
from agent.schemas import AgentMemory, ChatMessage, EvidenceItem, ToolCallRecord
from agent.tools import AgentTools


MAX_TOOL_CALLS = 9


def _tool_history_summary(tool_calls: List[ToolCallRecord]) -> str:
    if not tool_calls:
        return "(none)"
    lines = []
    for tc in tool_calls:
        lines.append(f"  step {tc.step}: {tc.tool_name}({tc.arguments}) -> {tc.result_summary[:120]}")
    return "\n".join(lines)


def _judge_summary(judge_history: List[Dict[str, Any]]) -> str:
    if not judge_history:
        return "(not yet judged)"
    last = judge_history[-1]
    return (
        f"sufficient={last.get('sufficient')}, "
        f"confidence={last.get('confidence')}, "
        f"missing={last.get('missing', '')}, "
        f"reason={last.get('reason', '')[:150]}"
    )


def _normalize_chunk_id(cid: Any) -> str:
    """Strip 'chunk:' prefix; also handles dicts with chunk_id key."""
    if isinstance(cid, dict):
        cid = cid.get("chunk_id", "")
    return str(cid).removeprefix("chunk:")


def _absorb_judge_result(
    judge: Dict[str, Any],
    tool_result: Dict[str, Any],
    memory: AgentMemory,
    retrieval_query: str,
) -> None:
    """Merge judge keep/discard decisions into evidence_container."""
    # Normalize: accept strings, "chunk:"-prefixed strings, or dicts with chunk_id
    keep_ids = {_normalize_chunk_id(k) for k in judge.get("keep_chunks", [])}
    all_chunks = tool_result.get("chunks", [])

    for chunk in all_chunks:
        cid = chunk.get("chunk_id", "")
        if _normalize_chunk_id(cid) not in keep_ids:
            continue
        if memory.already_have_chunk(cid):
            continue
        page_id = chunk.get("page_id", "")
        # Try to find page_id from node if missing
        if not page_id:
            page_id = ""
        ev = EvidenceItem(
            evidence_id=memory.next_evidence_id(),
            source_type="chunk",
            page_id=page_id,
            page_title=chunk.get("page_title", ""),
            source_url=chunk.get("source_url", ""),
            text=chunk.get("text", ""),
            why_kept=f"judge kept (step {len(memory.tool_calls)})",
            chunk_id=cid,
            owner_node_id=chunk.get("owner_node_id", ""),
            owner_node_name=chunk.get("owner_node_name", ""),
            path=chunk.get("path", []),
            score=chunk.get("score"),
            retrieval_query=retrieval_query,
        )
        memory.add_evidence(ev)

    # Structural keeps (from inspect_page)
    for structural in judge.get("keep_structural", []):
        page_id = structural.get("page_id", "")
        why = structural.get("why_kept", "")
        # Already stored as evidence?
        if any(e.page_id == page_id and e.source_type == "node_structure" for e in memory.evidence_container):
            continue
        # Retrieve the full markdown from last inspect result
        markdown = memory._last_inspect_results.get(page_id, {}).get("_full_markdown", "")
        page_title = memory._last_inspect_results.get(page_id, {}).get("page_title", "")
        source_url = memory._last_inspect_results.get(page_id, {}).get("source_url", "")
        if not markdown:
            continue
        page_label = page_title.replace(" ", "_") if page_title else page_id
        ev = EvidenceItem(
            evidence_id=memory.next_evidence_id(),
            source_type="node_structure",
            page_id=page_id,
            page_title=page_title,
            source_url=source_url,
            text=markdown,
            why_kept=why,
            path=[f"{page_label}_page_structure"],
        )
        memory.add_evidence(ev)


def _first_retrieval_query(rewritten_queries: List[Dict[str, Any]]) -> str:
    if rewritten_queries:
        return rewritten_queries[0].get("query", "")
    return ""


def _node_ids_from_markdown(markdown: str) -> List[tuple[str, str]]:
    """Return [(label, node_id)] from inspect_page markdown."""
    matches = []
    pattern = re.compile(r"- (?:[A-Z_]+ -> )?(.*?) \[node_id: ([^\]]+)\]")
    for line in markdown.splitlines():
        m = pattern.search(line.strip())
        if m:
            matches.append((m.group(1).strip(), m.group(2).strip()))
    return matches


def _choose_node_from_last_inspect(memory: AgentMemory, query: str) -> str:
    """Pick a concrete node_id from the most recent inspected page."""
    if not memory._last_inspect_results:
        return ""
    page_id = memory.inspected_pages[-1] if memory.inspected_pages else next(reversed(memory._last_inspect_results))
    markdown = memory._last_inspect_results.get(page_id, {}).get("_full_markdown", "")
    candidates = _node_ids_from_markdown(markdown)
    if not candidates:
        return page_id

    q = query.lower()
    preferred_terms = [
        "application requirements",
        "letters of recommendation",
        "candidate statement",
        "resume",
        "programming supplement",
        "transcripts",
        "application fee",
        "english language",
        "international students",
        "how to apply",
    ]
    if "application material" in q or "materials" in q or "apply" in q:
        for term in preferred_terms:
            for label, node_id in candidates:
                if term in label.lower():
                    return node_id

    query_terms = {t for t in re.findall(r"[a-z0-9]+", q) if len(t) > 3}
    best_node = ""
    best_score = -1
    for label, node_id in candidates:
        terms = set(re.findall(r"[a-z0-9]+", label.lower()))
        score = len(query_terms & terms)
        if score > best_score:
            best_score = score
            best_node = node_id
    return best_node or candidates[0][1]


def _has_recent_repeated_inspect(memory: AgentMemory, action: str) -> bool:
    if action != "inspect_page":
        return False
    if not memory.tool_calls:
        return False
    if memory.tool_calls[-1].tool_name == "inspect_page":
        return True
    inspect_count = sum(1 for tc in memory.tool_calls if tc.tool_name == "inspect_page")
    fetch_count = sum(1 for tc in memory.tool_calls if tc.tool_name in {"fetch_node_chunks", "fetch_chunk"})
    return inspect_count >= 2 and fetch_count == 0


def run(
    query: str,
    history: List[Dict[str, Any]],
    tools: AgentTools,
    client: DeepSeekClient,
    max_tool_calls: int = MAX_TOOL_CALLS,
    progress_cb: "callable | None" = None,
) -> AgentMemory:
    def _progress(message: str) -> None:
        if progress_cb:
            progress_cb(message)

    run_id = uuid.uuid4().hex[:12]
    history_msgs = [ChatMessage(role=m.get("role", "user"), content=m.get("content", "")) for m in history]
    memory = AgentMemory(run_id=run_id, original_query=query, history=history_msgs)

    # 1. Query rewrite
    _progress("Rewriting query...")
    rewritten = rewrite_query(query, history, client)
    memory.rewritten_queries = rewritten
    rewritten_strs = [r.get("query", query) for r in rewritten]

    step = 0
    judge_result: Dict[str, Any] = {}

    while step < max_tool_calls:
        step += 1
        _progress(f"Planning next tool call... (step {step}/{max_tool_calls})")

        # 2. Agent decision
        decision_user = agent_decision_user(
            rewritten_queries=rewritten_strs,
            tool_call_count=step - 1,
            max_tool_calls=max_tool_calls,
            judge_summary=_judge_summary(memory.judge_history),
            tool_history_summary=_tool_history_summary(memory.tool_calls),
        )
        # First step: force hybrid_retrieve on first rewritten query
        if step == 1:
            decision = {
                "action": "hybrid_retrieve",
                "args": {"query": rewritten_strs[0], "top_k": 8},
                "reason": "default first action",
            }
        else:
            try:
                decision = client.chat_json(AGENT_DECISION_SYSTEM, decision_user)
            except Exception as exc:
                memory.stop_reason = f"decision_error: {exc}"
                _progress(f"Decision failed: {exc}")
                break

        action = decision.get("action", "hybrid_retrieve")
        args: Dict[str, Any] = decision.get("args", {})
        if action == "list_page_summaries":
            args = {"query": args.get("query", "")}
        # For hybrid_retrieve, fill missing query with first rewritten query
        if action == "hybrid_retrieve" and not args.get("query"):
            args["query"] = rewritten_strs[0]
        if _has_recent_repeated_inspect(memory, action):
            node_id = _choose_node_from_last_inspect(memory, query)
            if node_id:
                action = "fetch_node_chunks"
                args = {
                    "node_id": node_id,
                    "fetch_depth": 1,
                    "_forced_after_repeated_inspect": True,
                }
                _progress(f"Repeated inspect detected; fetching node {node_id}.")

        # 3. Execute tool
        requested_node_id = args.get("node_id") if action == "fetch_node_chunks" else None
        _progress(f"Running {action}... (step {step}/{max_tool_calls})")
        tool_result = tools.dispatch(action, args)
        if action == "fetch_node_chunks" and tool_result.get("resolved_node_id"):
            resolved_node_id = tool_result["resolved_node_id"]
            if requested_node_id and requested_node_id != resolved_node_id:
                args["requested_node_id"] = requested_node_id
                args["node_id"] = resolved_node_id
                _progress(f"Resolved node_id to {resolved_node_id}.")

        # Cache inspect_page results for judge to reference
        if action == "inspect_page" and tool_result.get("ok"):
            page_id = tool_result.get("page_id", "")
            if page_id:
                memory._last_inspect_results[page_id] = tool_result
                memory.inspected_pages.append(page_id)
        if action == "list_page_summaries":
            memory.candidate_pages.extend(tool_result.get("summaries", []))

        # 4. Record tool call
        tc = ToolCallRecord(
            step=step,
            tool_name=action,
            arguments=args,
            result_summary=tool_result.get("llm_visible_result", "")[:200],
            returned_chunk_ids=tool_result.get("returned_chunk_ids", []),
            returned_page_ids=tool_result.get("returned_page_ids", []),
            returned_node_ids=tool_result.get("returned_node_ids", []),
            llm_visible_result=tool_result.get("llm_visible_result", ""),
        )
        memory.tool_calls.append(tc)

        # list_page_summaries skips judge
        if action == "list_page_summaries":
            continue

        # 5. Judge
        _progress(f"Judging evidence from {action}... (step {step}/{max_tool_calls})")
        judge_user_msg = evidence_judge_user(
            original_query=query,
            rewritten_queries=rewritten_strs,
            current_tool=f"{action}({args})",
            llm_visible_result=tool_result.get("llm_visible_result", ""),
        )
        try:
            judge_result = client.chat_json(EVIDENCE_JUDGE_SYSTEM, judge_user_msg)
        except Exception:
            judge_result = {
                "sufficient": False,
                "confidence": 0.0,
                "keep_chunks": tool_result.get("returned_chunk_ids", []),
                "discard_chunks": [],
                "keep_structural": [],
                "missing": "judge failed",
                "reason": "judge parse error",
            }

        memory.judge_history.append(judge_result)

        retrieval_query = args.get("query", rewritten_strs[0] if rewritten_strs else query)
        _absorb_judge_result(judge_result, tool_result, memory, retrieval_query)

        if judge_result.get("sufficient"):
            memory.stop_reason = "sufficient_evidence"
            _progress("Evidence is sufficient.")
            break
    else:
        memory.stop_reason = "max_tool_calls"

    if not memory.stop_reason:
        memory.stop_reason = "max_tool_calls"
    # Only mark empty_evidence if truly nothing was collected
    if not memory.evidence_container and memory.stop_reason != "sufficient_evidence":
        memory.stop_reason = "empty_evidence"
        _progress("No useful evidence was collected.")
    else:
        _progress(f"Finished with stop reason: {memory.stop_reason}")

    return memory

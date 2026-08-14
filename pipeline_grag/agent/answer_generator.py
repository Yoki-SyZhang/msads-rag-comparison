"""Generate the final answer from evidence and build citations."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from agent.deepseek_client import DeepSeekClient
from agent.prompts import ANSWER_GENERATION_SYSTEM, answer_generation_user
from agent.schemas import AgentMemory, ChatResponse, Citation


def _build_evidence_dicts(memory: AgentMemory) -> List[Dict[str, Any]]:
    return [ev.to_dict() for ev in memory.evidence_container]


def _extract_citation_indices(answer_text: str) -> List[int]:
    """Extract all [n] citation markers from answer text."""
    return [int(m) for m in re.findall(r"\[(\d+)\]", answer_text)]


def _build_citations(answer_text: str, evidence_list: List[Dict[str, Any]]) -> List[Citation]:
    """Build citation objects from evidence items referenced in the answer."""
    indices = sorted(set(_extract_citation_indices(answer_text)))
    # Map evidence_id ev_001 -> index 1
    id_to_ev: Dict[int, Dict[str, Any]] = {}
    for ev in evidence_list:
        eid = ev.get("evidence_id", "")
        m = re.match(r"ev_(\d+)", eid)
        if m:
            id_to_ev[int(m.group(1))] = ev

    citations = []
    for idx in indices:
        ev = id_to_ev.get(idx)
        if not ev:
            continue
        text = ev.get("text", "")
        snippet = text[80:230].strip() if len(text) > 80 else text[:150].strip()
        if not snippet:
            snippet = text[:150].strip()
        citations.append(
            Citation(
                index=idx,
                title=ev.get("page_title", ""),
                source_url=ev.get("source_url", ""),
                snippet=snippet,
            )
        )
    return citations


def generate(memory: AgentMemory, client: DeepSeekClient) -> ChatResponse:
    """Call DeepSeek for the final answer and assemble the ChatResponse."""
    evidence_list = _build_evidence_dicts(memory)

    user_msg = answer_generation_user(
        original_query=memory.original_query,
        evidence_container=evidence_list,
        stop_reason=memory.stop_reason,
    )

    try:
        answer_text = client.chat_text(ANSWER_GENERATION_SYSTEM, user_msg)
    except Exception as exc:
        answer_text = f"(answer generation failed: {exc})"

    citations = _build_citations(answer_text, evidence_list)

    debug: Dict[str, Any] = {
        "run_id": memory.run_id,
        "log_path": "",  # filled in by logger after writing
        "rewritten_queries": [r.get("query", "") for r in memory.rewritten_queries],
        "tool_call_count": len(memory.tool_calls),
        "stop_reason": memory.stop_reason,
    }

    return ChatResponse(answer=answer_text, citations=citations, debug=debug)

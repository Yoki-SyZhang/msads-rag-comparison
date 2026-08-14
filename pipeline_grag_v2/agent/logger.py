"""Per-request GRAG v2 JSON logging; the logger owns run IDs.

Writes the full anatomy described in PLAN.md §6: request, rewritten queries, the
complete reference registry, tool_history (including decision_reason), the structured
page-grouped node_evaluation_log, the turn-by-turn progress_summary_history (not just
the final snapshot — this is the audit trail the user asked to be able to review),
judgment, fallback info, stop_reason, and the final response.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple

from .schemas import AgentContext, ChatResponse


LOG_DIR = Path(__file__).resolve().parents[1] / "log"


def _node_evaluation_log_to_dict(context: AgentContext) -> Dict[str, Dict[str, Any]]:
    return {
        page_key: {node_key: evaluation.to_dict() for node_key, evaluation in nodes.items()}
        for page_key, nodes in context.node_evaluation_log.items()
    }


def write_log(context: AgentContext, response: ChatResponse) -> Tuple[Path, str]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    path = LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_{run_id}.json"
    payload = {
        "run_id": run_id,
        "request": {
            "query": context.query_state.original_query,
            "history": [dataclasses.asdict(message) for message in context.query_state.conversation_history],
        },
        "rewritten_queries": context.query_state.rewritten_queries,
        "reference_registry": {
            "pages": [dataclasses.asdict(entry) for entry in context.refs.entries("page")],
            "nodes": [dataclasses.asdict(entry) for entry in context.refs.entries("node")],
            "evidence": [dataclasses.asdict(entry) for entry in context.refs.entries("evidence")],
        },
        "tool_history": [dataclasses.asdict(record) for record in context.tool_history],
        "node_evaluation_log": _node_evaluation_log_to_dict(context),
        "progress_summary_history": context.progress_summary_history,
        "judgment": dataclasses.asdict(context.judgment),
        "fallback": context.fallback,
        "valid_fetch_count": context.valid_fetch_count,
        "invalid_fetch_count": context.invalid_fetch_count,
        "turn": context.turn,
        "stop_reason": context.stop_reason,
        "final_response": response.to_dict(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, run_id

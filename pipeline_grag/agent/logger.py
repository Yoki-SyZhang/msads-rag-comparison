"""Write per-request JSON log files for the MSADS RAG agent."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from agent.schemas import AgentMemory, ChatResponse


LOG_DIR = Path(__file__).resolve().parents[1] / "log"


def _serialize(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def write_log(memory: AgentMemory, response: ChatResponse) -> Path:
    """Write a complete run log and return the log file path."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = LOG_DIR / f"{ts}_{memory.run_id}.json"

    payload: Dict[str, Any] = {
        "run_id": memory.run_id,
        "request": {
            "query": memory.original_query,
            "history": [dataclasses.asdict(h) for h in memory.history],
        },
        "rewritten_queries": memory.rewritten_queries,
        "tool_calls": [dataclasses.asdict(tc) for tc in memory.tool_calls],
        "judge_history": memory.judge_history,
        "evidence_container": [ev.to_dict() for ev in memory.evidence_container],
        "stop_reason": memory.stop_reason,
        "final_answer_prompt": {
            "evidence_count": len(memory.evidence_container),
        },
        "raw_answer_text": response.answer,
        "parsed_citation_markers": [c.index for c in response.citations],
        "final_response": response.to_dict(),
    }

    log_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_serialize),
        encoding="utf-8",
    )
    return log_path

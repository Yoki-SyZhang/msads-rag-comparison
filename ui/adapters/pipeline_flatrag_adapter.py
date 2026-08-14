"""Adapter for the existing FlatRAG pipeline."""

from __future__ import annotations

import importlib
import sys
import json
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_ROOT = REPO_ROOT / "pipeline_flatrag"
LOG_DIR = PIPELINE_ROOT / "data" / "qa_log"


def _ensure_path() -> None:
    path = str(PIPELINE_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)


class FlatRAGAdapter:
    pipeline_id = "pipeline_flatrag"
    display_name = "FlatRAG"
    description = "Flat chunks + Chroma vector retrieval + DeepSeek agent loop"

    def __init__(self) -> None:
        _ensure_path()
        qa_pipeline = importlib.import_module("src.qa.qa_pipeline")
        qa_pipeline = importlib.reload(qa_pipeline)
        self._pipeline = qa_pipeline.build_pipeline()

    def run(
        self,
        query: str,
        history: List[Dict[str, Any]] | None = None,
        progress_cb: "callable | None" = None,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            if progress_cb:
                progress_cb("Starting FlatRAG agent loop...")
            result = self._pipeline.run(
                query,
                history=history or [],
                stream=False,
                progress_cb=progress_cb,
            )
            elapsed = time.perf_counter() - started
            fallback_log = self._latest_log_payload()
            steps = result.get("agent_steps") or fallback_log.get("agent_steps", [])
            evidence = result.get("evidence") or fallback_log.get("useful_chunks", [])
            usage = result.get("usage") or {}
            raw_debug = dict(result)
            raw_debug["usage"] = usage
            raw_debug["metrics"] = {
                "tool_calls": sum(len(s.get("tool_calls", [])) for s in steps),
                "loop_count": len(steps),
                "evidence_count": len(evidence),
                "llm_calls": usage.get("llm_calls"),
                "judge_calls": None,
                "valid_fetch_count": None,
                "invalid_fetch_count": None,
                "fallback_triggered": None,
            }
            return {
                "pipeline_id": self.pipeline_id,
                "display_name": self.display_name,
                "answer": result.get("answer", ""),
                "sources": result.get("sources", []),
                "steps": steps or [
                    {
                        "label": "agent_attempts",
                        "value": result.get("attempts", 0),
                    },
                    {
                        "label": "completeness",
                        "value": result.get("completeness", ""),
                    },
                ],
                "evidence": evidence,
                "time_sec": round(elapsed, 3),
                "low_confidence": bool(result.get("low_confidence", False)),
                "error": None,
                "raw_debug": raw_debug,
            }
        except Exception as exc:
            elapsed = time.perf_counter() - started
            return self._error_result(exc, elapsed)

    @staticmethod
    def _latest_log_payload() -> Dict[str, Any]:
        try:
            logs = sorted(LOG_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not logs:
                return {}
            return json.loads(logs[0].read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _error_result(self, exc: Exception, elapsed: float) -> Dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "display_name": self.display_name,
            "answer": "",
            "sources": [],
            "steps": [],
            "evidence": [],
            "time_sec": round(elapsed, 3),
            "low_confidence": True,
            "error": str(exc),
            "raw_debug": {"traceback": traceback.format_exc()},
        }

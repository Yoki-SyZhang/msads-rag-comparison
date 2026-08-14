"""Adapter for the existing GRAG pipeline using DeepSeek, not Ollama."""

from __future__ import annotations

import dataclasses
import importlib
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_ROOT = REPO_ROOT / "pipeline_grag"


def _ensure_path() -> None:
    path = str(PIPELINE_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)


class GRAGAdapter:
    pipeline_id = "pipeline_grag"
    display_name = "GRAG"
    description = "DOM-aware graph + hybrid retrieval + DeepSeek agent loop"

    def __init__(self) -> None:
        _ensure_path()
        deepseek_client = importlib.reload(importlib.import_module("agent.deepseek_client"))
        importlib.reload(importlib.import_module("grag.graph_tools"))
        tools_module = importlib.reload(importlib.import_module("agent.tools"))

        self._client = deepseek_client.DeepSeekClient()
        self._tools = tools_module.AgentTools(index_dir=str(PIPELINE_ROOT / "index"))
        _ = self._tools.loaded_index

    def run(
        self,
        query: str,
        history: List[Dict[str, Any]] | None = None,
        progress_cb: "callable | None" = None,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            importlib.reload(importlib.import_module("agent.prompts"))
            agent_loop = importlib.reload(importlib.import_module("agent.agent_loop"))
            answer_generator = importlib.reload(importlib.import_module("agent.answer_generator"))
            logger = importlib.reload(importlib.import_module("agent.logger"))

            if progress_cb:
                progress_cb("Initializing GRAG tools...")
            memory = agent_loop.run(
                query=query,
                history=history or [],
                tools=self._tools,
                client=self._client,
                progress_cb=progress_cb,
            )
            if progress_cb:
                progress_cb("Generating final answer...")
            response = answer_generator.generate(memory, self._client)
            log_path = logger.write_log(memory, response)
            response.debug["log_path"] = str(log_path)

            elapsed = time.perf_counter() - started
            evidence = [ev.to_dict() for ev in memory.evidence_container]
            tool_steps = self._normalize_steps(memory)
            return {
                "pipeline_id": self.pipeline_id,
                "display_name": self.display_name,
                "answer": response.answer,
                "sources": self._sources_from_response(response.to_dict(), evidence),
                "steps": tool_steps,
                "evidence": evidence,
                "time_sec": round(elapsed, 3),
                "low_confidence": memory.stop_reason != "sufficient_evidence",
                "error": None,
                "raw_debug": {
                    "response": response.to_dict(),
                    "run_id": memory.run_id,
                    "rewritten_queries": memory.rewritten_queries,
                    "judge_history": memory.judge_history,
                    "stop_reason": memory.stop_reason,
                    "log_path": str(log_path),
                },
            }
        except Exception as exc:
            elapsed = time.perf_counter() - started
            return self._error_result(exc, elapsed)

    @staticmethod
    def _sources_from_response(response: Dict[str, Any], evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: set[str] = set()
        sources: List[Dict[str, Any]] = []
        for citation in response.get("citations", []):
            url = citation.get("source_url", "")
            if url and url not in seen:
                seen.add(url)
                sources.append({"page_title": citation.get("title", ""), "source_url": url})
        for item in evidence:
            url = item.get("source_url", "")
            if url and url not in seen:
                seen.add(url)
                sources.append({"page_title": item.get("page_title", ""), "source_url": url})
        return sources

    @staticmethod
    def _normalize_steps(memory: Any) -> List[Dict[str, Any]]:
        steps: List[Dict[str, Any]] = []
        for idx, tc in enumerate(memory.tool_calls, start=1):
            record = dataclasses.asdict(tc)
            judge = memory.judge_history[idx - 1] if idx - 1 < len(memory.judge_history) else None
            steps.append(
                {
                    "step": record.get("step", idx),
                    "tool_calls": [
                        {
                            "name": record.get("tool_name", ""),
                            "args": record.get("arguments", {}),
                        }
                    ],
                    "tool_results": [
                        {
                            "name": record.get("tool_name", ""),
                            "content": record.get("llm_visible_result") or record.get("result_summary", ""),
                        }
                    ],
                    "judger": judge,
                }
            )
        return steps

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

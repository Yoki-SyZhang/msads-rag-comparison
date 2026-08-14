"""Comparison adapter for the pipeline_grag_v2 graph-navigation-first pipeline."""

from __future__ import annotations

import dataclasses
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_ROOT = REPO_ROOT / "pipeline_grag_v2"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline_grag_v2.agent import agent_loop, answer_generator, logger
from pipeline_grag_v2.agent.deepseek_client import DeepSeekClient
from pipeline_grag_v2.agent.tools import AgentTools


class GRAGv2Adapter:
    pipeline_id = "pipeline_grag_v2"
    display_name = "GRAG v2"
    description = "Graph-navigation-first agent (inspect_page/fetch_graph_content only) with a scoped hybrid-retrieve fallback"

    def __init__(self) -> None:
        self._client = DeepSeekClient()
        # Index loading stays lazy so an absent/corrupt v2 index is isolated to this
        # comparison side rather than preventing FlatRAG from starting.
        self._tools = AgentTools(index_dir=PIPELINE_ROOT / "index")

    def run(
        self,
        query: str,
        history: List[Dict[str, Any]] | None = None,
        progress_cb: Any = None,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        starting_llm_calls = self._client.call_count
        starting_usage = {
            "prompt_tokens": self._client.prompt_tokens,
            "completion_tokens": self._client.completion_tokens,
            "total_tokens": self._client.total_tokens,
        }
        try:
            context = agent_loop.run(
                query=query,
                history=history or [],
                tools=self._tools,
                client=self._client,
                progress_cb=progress_cb,
            )
            if progress_cb:
                progress_cb("Generating the evidence-grounded answer...")
            response = answer_generator.generate(context, self._client)
            log_path, run_id = logger.write_log(context, response)
            response.debug.update({"log_path": str(log_path), "run_id": run_id})
            accepted = [item.to_dict() for item in context.evidence.accepted()]
            elapsed = time.perf_counter() - started
            return {
                "pipeline_id": self.pipeline_id,
                "display_name": self.display_name,
                "answer": response.answer,
                "sources": self._sources(response.to_dict(), accepted),
                "steps": self._steps(context),
                "evidence": accepted,
                "time_sec": round(elapsed, 3),
                "low_confidence": not context.judgment.sufficient,
                "error": None,
                "progress_summary": self._progress_summary(context),
                "raw_debug": {
                    "response": response.to_dict(),
                    "run_id": run_id,
                    "rewritten_queries": context.query_state.rewritten_queries,
                    "judgment": dataclasses.asdict(context.judgment),
                    "stop_reason": context.stop_reason,
                    "fallback": context.fallback,
                    "metrics": self._metrics(context, self._client.call_count - starting_llm_calls),
                    "usage": {
                        "prompt_tokens": self._client.prompt_tokens - starting_usage["prompt_tokens"],
                        "completion_tokens": self._client.completion_tokens - starting_usage["completion_tokens"],
                        "total_tokens": self._client.total_tokens - starting_usage["total_tokens"],
                        "llm_calls": self._client.call_count - starting_llm_calls,
                    },
                    "progress_summary_history": context.progress_summary_history,
                    "node_evaluation_log": {
                        page_key: {node_key: evaluation.to_dict() for node_key, evaluation in nodes.items()}
                        for page_key, nodes in context.node_evaluation_log.items()
                    },
                    "log_path": str(log_path),
                },
            }
        except Exception as exc:
            return self._error_result(exc, time.perf_counter() - started)

    @staticmethod
    def _sources(response: Dict[str, Any], evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sources, seen = [], set()
        for item in list(response.get("citations", [])) + evidence:
            url = item.get("source_url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append({"page_title": item.get("title") or item.get("page_title", ""), "source_url": url})
        return sources

    @staticmethod
    def _steps(context: Any) -> List[Dict[str, Any]]:
        return [
            {
                "step": record.turn,
                "tool_calls": [{"name": record.tool, "args": record.normalized_arguments}],
                "tool_results": [{"name": record.tool, "content": record.llm_visible_result}],
                "judger": {"node_assessments": record.node_assessments} if record.node_assessments else None,
                "decision_reason": record.decision_reason,
            }
            for record in context.tool_history
        ]

    @staticmethod
    def _progress_summary(context: Any) -> Dict[str, str] | None:
        if not context.progress_summary_history:
            return None
        latest = context.progress_summary_history[-1]
        return {
            "summary_covering": latest.get("summary_covering", ""),
            "decision_trace_narrative": latest.get("decision_trace_narrative", ""),
        }

    @staticmethod
    def _metrics(context: Any, llm_calls: int) -> Dict[str, Any]:
        return {
            "agent_turns": context.turn,
            "tool_calls": len(context.tool_history),
            "judge_calls": sum(1 for record in context.tool_history if record.node_assessments),
            "llm_calls": llm_calls,
            "valid_fetch_count": context.valid_fetch_count,
            "invalid_fetch_count": context.invalid_fetch_count,
            "accepted_evidence_count": len(context.evidence.accepted()),
            "stop_reason": context.stop_reason,
            "fallback_triggered": context.fallback.get("triggered", False),
        }

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
            "progress_summary": None,
            "raw_debug": {"traceback": traceback.format_exc()},
        }

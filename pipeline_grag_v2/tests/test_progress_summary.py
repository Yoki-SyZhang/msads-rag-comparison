from __future__ import annotations

import unittest

from pipeline_grag_v2.agent.agent_loop import _run_summarizer
from pipeline_grag_v2.agent.references import ReferenceRegistry
from pipeline_grag_v2.agent.schemas import AgentContext, QueryState


class FailingClient:
    def chat_json(self, system, user, **kwargs):
        raise RuntimeError("summarizer unavailable")


class ScriptedClient:
    def __init__(self, response) -> None:
        self.response = response

    def chat_json(self, system, user, **kwargs):
        return self.response


class ProgressSummaryTests(unittest.TestCase):
    def test_two_part_output_populates_history_and_judgment(self) -> None:
        ctx = AgentContext(QueryState("q"), ReferenceRegistry())
        ctx.turn = 1
        client = ScriptedClient({
            "summary_covering": "Page A: fee confirmed.",
            "decision_trace_narrative": "Turn 1: fetched N9, kept E1.",
            "sufficient": True,
            "missing": "",
        })
        _run_summarizer(ctx, "fetch_graph_content", "OK", "fetch the fee node", client)
        self.assertEqual(len(ctx.progress_summary_history), 1)
        entry = ctx.progress_summary_history[0]
        self.assertEqual(entry["summary_covering"], "Page A: fee confirmed.")
        self.assertEqual(entry["decision_trace_narrative"], "Turn 1: fetched N9, kept E1.")
        self.assertTrue(ctx.judgment.sufficient)
        # summary_covering and decision_trace_narrative are genuinely separate fields,
        # not concatenated into one blob (PLAN.md §3.5).
        self.assertNotIn("Turn 1:", entry["summary_covering"])
        self.assertNotIn("fee confirmed", entry["decision_trace_narrative"])

    def test_summarizer_failure_keeps_previous_summary_and_stays_insufficient(self) -> None:
        ctx = AgentContext(QueryState("q"), ReferenceRegistry())
        ctx.turn = 1
        _run_summarizer(ctx, "fetch_graph_content", "OK", "first attempt", ScriptedClient({
            "summary_covering": "Page A checked.", "decision_trace_narrative": "Turn 1 done.",
            "sufficient": False, "missing": "more info",
        }))
        ctx.turn = 2
        _run_summarizer(ctx, "fetch_graph_content", "OK", "second attempt", FailingClient())
        self.assertEqual(len(ctx.progress_summary_history), 2)
        latest = ctx.progress_summary_history[-1]
        # Failure keeps the previous turn's narrative content rather than losing it.
        self.assertEqual(latest["summary_covering"], "Page A checked.")
        self.assertEqual(latest["decision_trace_narrative"], "Turn 1 done.")
        self.assertFalse(latest["sufficient"])
        self.assertFalse(ctx.judgment.sufficient)
        self.assertIn("unavailable", latest["missing"])

    def test_history_is_appended_not_overwritten_across_turns(self) -> None:
        ctx = AgentContext(QueryState("q"), ReferenceRegistry())
        for turn in range(1, 4):
            ctx.turn = turn
            _run_summarizer(ctx, "fetch_graph_content", "OK", f"turn {turn}", ScriptedClient({
                "summary_covering": f"state at turn {turn}", "decision_trace_narrative": "...",
                "sufficient": False, "missing": "",
            }))
        self.assertEqual(len(ctx.progress_summary_history), 3)
        self.assertEqual([entry["turn"] for entry in ctx.progress_summary_history], [1, 2, 3])
        self.assertEqual(ctx.progress_summary_history[-1]["summary_covering"], "state at turn 3")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from pipeline_grag_v2.agent.agent_loop import _run_fallback
from pipeline_grag_v2.agent.references import ReferenceRegistry
from pipeline_grag_v2.agent.schemas import AgentContext, QueryState, ToolExecution


class RecordingClient:
    def __init__(self, page_select_response) -> None:
        self.page_select_response = page_select_response
        self.calls = []

    def chat_json(self, system, user, **kwargs):
        self.calls.append((system, user))
        if len(self.calls) == 1:
            return self.page_select_response
        return {"node_assessments": [{"node_ref": "hybrid", "keep_evidence": ["E1"], "comment": "Directly relevant."}]}


class FailingSelectClient:
    def chat_json(self, system, user, **kwargs):
        raise RuntimeError("page select unavailable")


class RecordingTools:
    def __init__(self, execution: ToolExecution) -> None:
        self._execution = execution
        self.hybrid_calls = []

    def hybrid_retrieve(self, query, page_ids, context):
        self.hybrid_calls.append((query, set(page_ids)))
        return self._execution


def _make_context() -> AgentContext:
    ctx = AgentContext(QueryState("what courses are in quarter 1"), ReferenceRegistry())
    ctx.query_state.rewritten_queries = [{"id": "q1", "query": "quarter 1 courses", "target": "fact"}]
    for i in range(1, 15):
        ctx.refs.assign("page", f"page:{i}", f"Page {i}", {"description": f"desc {i}"})
    ctx.turn = 6
    return ctx


def _fake_execution(evidence_refs) -> ToolExecution:
    from pipeline_grag_v2.agent.schemas import EvidenceItem

    return ToolExecution(
        tool="hybrid_retrieve", normalized_arguments={"query": "quarter 1 courses"}, resolved_real_ids=[],
        canonical_signature="hybrid-sig", status="OK", llm_visible_result="[E1] some course text",
        candidate_evidence_refs=evidence_refs, candidate_groups={"hybrid": evidence_refs},
        fact_producing=True, is_valid_fetch=True,
    )


class FallbackTests(unittest.TestCase):
    def test_selected_page_refs_are_resolved_and_scoped(self) -> None:
        ctx = _make_context()
        client = RecordingClient({"page_refs": ["P2", "P5"], "reason": "most likely pages"})
        execution = _fake_execution([])
        tools = RecordingTools(execution)
        _run_fallback(ctx, tools, client, progress=lambda msg: None)
        self.assertEqual(len(tools.hybrid_calls), 1)
        _, scoped_page_ids = tools.hybrid_calls[0]
        p2 = ctx.refs.resolve("P2", "page")
        p5 = ctx.refs.resolve("P5", "page")
        self.assertEqual(scoped_page_ids, {p2.real_id, p5.real_id})
        self.assertEqual(ctx.fallback["pages_selected"], ["P2", "P5"])
        self.assertTrue(ctx.fallback["triggered"])
        self.assertEqual(ctx.stop_reason, "fallback_hybrid_retrieve")

    def test_page_select_failure_defaults_to_first_five_pages(self) -> None:
        ctx = _make_context()
        execution = _fake_execution([])
        tools = RecordingTools(execution)
        _run_fallback(ctx, tools, FailingSelectClient(), progress=lambda msg: None)
        expected = {entry.real_id for entry in ctx.refs.entries("page")[:5]}
        _, scoped_page_ids = tools.hybrid_calls[0]
        self.assertEqual(scoped_page_ids, expected)
        self.assertEqual(len(ctx.fallback["pages_selected"]), 5)

    def test_hallucinated_page_refs_also_default_to_first_five(self) -> None:
        ctx = _make_context()
        client = RecordingClient({"page_refs": ["P999", "P1000"], "reason": "guess"})
        execution = _fake_execution([])
        tools = RecordingTools(execution)
        _run_fallback(ctx, tools, client, progress=lambda msg: None)
        expected = {entry.real_id for entry in ctx.refs.entries("page")[:5]}
        _, scoped_page_ids = tools.hybrid_calls[0]
        self.assertEqual(scoped_page_ids, expected)

    def test_fallback_evidence_merges_into_accepted_evidence(self) -> None:
        ctx = _make_context()
        client = RecordingClient({"page_refs": ["P1"], "reason": "single page"})
        ref = ctx.refs.assign("evidence", "chunk:course1", "quarter 1 course")
        from pipeline_grag_v2.agent.schemas import EvidenceItem
        ctx.evidence.register(EvidenceItem(
            ref, "chunk:course1", "section:x", "page:1", "Page 1", "https://x",
            "Quarter 1 core courses are ...", ["Page 1", "Quarter 1"], "text",
        ))
        execution = _fake_execution([ref])
        tools = RecordingTools(execution)
        _run_fallback(ctx, tools, client, progress=lambda msg: None)
        self.assertTrue(ctx.evidence.accepted())
        self.assertEqual(ctx.evidence.accepted()[0].evidence_ref, ref)

    def test_fallback_does_not_reenter_graph_navigation(self) -> None:
        # _run_fallback must not touch tools.dispatch / inspect_page / fetch_graph_content
        # at all — it is a single bounded phase, not a return into the main loop.
        ctx = _make_context()
        client = RecordingClient({"page_refs": ["P1"], "reason": "single page"})

        class StrictTools(RecordingTools):
            def dispatch(self, action, args, context):
                raise AssertionError("fallback must not call dispatch()")

        tools = StrictTools(_fake_execution([]))
        _run_fallback(ctx, tools, client, progress=lambda msg: None)
        self.assertEqual(ctx.stop_reason, "fallback_hybrid_retrieve")

    def test_empty_hybrid_result_still_sets_stop_reason(self) -> None:
        ctx = _make_context()
        client = RecordingClient({"page_refs": ["P1"], "reason": "single page"})
        execution = ToolExecution(
            tool="hybrid_retrieve", normalized_arguments={"query": "x"}, resolved_real_ids=[],
            canonical_signature="sig", status="OK", llm_visible_result="No retrieval results.",
            candidate_evidence_refs=[], candidate_groups={}, fact_producing=True, is_valid_fetch=True,
        )
        tools = RecordingTools(execution)
        _run_fallback(ctx, tools, client, progress=lambda msg: None)
        self.assertEqual(ctx.stop_reason, "fallback_hybrid_retrieve")
        self.assertFalse(ctx.evidence.accepted())


if __name__ == "__main__":
    unittest.main()

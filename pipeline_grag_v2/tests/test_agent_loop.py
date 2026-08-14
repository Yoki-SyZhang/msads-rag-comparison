from __future__ import annotations

import unittest

from pipeline_grag_v2.agent.agent_loop import (
    FALLBACK_INVALID_FETCH_LIMIT,
    FALLBACK_STUCK_TURN_LIMIT,
    FALLBACK_VALID_FETCH_LIMIT,
    _judge,
    _update_node_evaluation_log,
    run,
)
from pipeline_grag_v2.agent.answer_generator import generate
from pipeline_grag_v2.agent.references import ReferenceRegistry
from pipeline_grag_v2.agent.schemas import AgentContext, EvidenceItem, QueryState, ToolExecution


class QueueClient:
    def __init__(self, json_responses=None, text="Answer [1]") -> None:
        self.responses = list(json_responses or [])
        self.text = text
        self.json_calls = 0

    def chat_json(self, system, user, **kwargs):
        self.json_calls += 1
        if not self.responses:
            raise RuntimeError("no queued response")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def chat_text(self, system, user, **kwargs):
        return self.text


REWRITE = {"rewritten_queries": [{"id": "q1", "query": "q", "target": "fact"}]}


def summarizer(sufficient: bool, missing: str = "") -> dict:
    return {
        "summary_covering": "Page X was checked.",
        "decision_trace_narrative": "Inspected then fetched.",
        "sufficient": sufficient,
        "missing": missing,
    }


class ScriptedTools:
    """Fake AgentTools returning scripted ToolExecutions, so agent_loop control-flow
    (dedup handling, Judge gating, fallback triggering) can be tested without any real
    graph/index data. Each queued step is a callable (action, args, context) ->
    ToolExecution so it can register real refs/evidence on the shared context."""

    def __init__(self, page_summaries, step_fns) -> None:
        self._page_summaries = page_summaries
        self._step_fns = list(step_fns)
        self.dispatch_calls = []

    def load_page_summaries(self, context):
        out = []
        for real_id, title, desc in self._page_summaries:
            ref = context.refs.assign("page", real_id, title, {"description": desc})
            out.append({"page_ref": ref, "page_title": title, "description": desc})
        return out

    def dispatch(self, action, args, context):
        self.dispatch_calls.append((action, dict(args)))
        return self._step_fns.pop(0)(action, args, context)

    def hybrid_retrieve(self, query, page_ids, context):
        return self._step_fns.pop(0)("hybrid_retrieve", {"query": query, "page_ids": page_ids}, context)


def _inspect_step(action, args, context):
    node_ref = context.refs.assign("node", "section:n1", "N1 Label", {"content_count": 3, "page_ref": "P1", "path": ["Page", "N1 Label"]})
    return ToolExecution(
        tool="inspect_page", normalized_arguments={"page_ref": args.get("page_ref", "")},
        resolved_real_ids=["page:1"], canonical_signature="inspect-sig", status="OK",
        llm_visible_result=f"[P1] Page\n  [{node_ref}] N1 Label",
    )


def _fresh_fetch_step(action, args, context):
    ref = context.evidence.register(EvidenceItem(
        "E1", "chunk:1", "section:n1", "page:1", "Page", "https://x", "supported text", ["Page", "N1 Label"], "text",
    )).evidence_ref
    return ToolExecution(
        tool="fetch_graph_content", normalized_arguments={"node_refs": ["N1"]},
        resolved_real_ids=["section:n1"], canonical_signature="fetch-sig-1", status="OK",
        llm_visible_result="[N1] N1 Label\n  [E1] supported text",
        candidate_evidence_refs=[ref], candidate_groups={"N1": [ref]},
        fact_producing=True, is_valid_fetch=True,
    )


def _already_checked_fetch_step(action, args, context):
    return ToolExecution(
        tool="fetch_graph_content", normalized_arguments={"node_refs": ["N1"]},
        resolved_real_ids=["section:n1"], canonical_signature="fetch-sig-1", status="ALREADY_CHECKED",
        llm_visible_result="ALREADY_CHECKED [N1] — kept evidence: E1", fact_producing=True, executed=False,
        is_valid_fetch=False,
    )


def _invalid_reference_step(action, args, context):
    return ToolExecution(
        tool="fetch_graph_content", normalized_arguments={"node_refs": ["N99"]},
        resolved_real_ids=[], canonical_signature="invalid-sig", status="INVALID_REFERENCE",
        llm_visible_result="INVALID_REFERENCE", fact_producing=True, executed=False, is_valid_fetch=False,
    )


PAGE_SUMMARIES = [("page:1", "Page", "A test page.")]


class AgentLoopTests(unittest.TestCase):
    def test_already_checked_skips_judge_but_summarizer_still_runs(self) -> None:
        client = QueueClient([
            REWRITE,
            {"action": "inspect_page", "args": {"page_ref": "P1"}, "reason": "look at the page"},
            summarizer(False, "need the fact"),
            {"action": "fetch_graph_content", "args": {"node_refs": ["N1"]}, "reason": "fetch it"},
            {"node_assessments": [{"node_ref": "N1", "keep_evidence": ["E1"], "comment": "Directly useful."}]},
            summarizer(False, "still need more"),
            {"action": "fetch_graph_content", "args": {"node_refs": ["N1"]}, "reason": "fetch again by mistake"},
            summarizer(True),  # no judge call queued here — must not be consumed
        ])
        tools = ScriptedTools(PAGE_SUMMARIES, [_inspect_step, _fresh_fetch_step, _already_checked_fetch_step])
        ctx = run("q", [], tools, client, max_turns=5)
        self.assertEqual(ctx.stop_reason, "sufficient_evidence")
        self.assertEqual(ctx.tool_history[2].status, "ALREADY_CHECKED")
        self.assertFalse(ctx.tool_history[2].node_assessments)
        self.assertEqual(len(client.responses), 0)  # every queued response was consumed exactly once

    def test_sufficient_ends_loop_without_a_finish_action(self) -> None:
        client = QueueClient([
            REWRITE,
            {"action": "fetch_graph_content", "args": {"node_refs": ["N1"]}, "reason": "fetch"},
            {"node_assessments": [{"node_ref": "N1", "keep_evidence": ["E1"], "comment": "Answers it."}]},
            summarizer(True),
        ])
        tools = ScriptedTools(PAGE_SUMMARIES, [_fresh_fetch_step])
        ctx = run("q", [], tools, client, max_turns=9)
        self.assertEqual(ctx.turn, 1)
        self.assertEqual(ctx.stop_reason, "sufficient_evidence")
        self.assertTrue(ctx.evidence.accepted())

    def test_max_turns_exhausted_without_reaching_a_fallback_condition(self) -> None:
        # 4 turns of a single always-invalid-reference fetch: invalid_fetch_count only
        # reaches 4 (> FALLBACK_INVALID_FETCH_LIMIT=3) at turn 4, so with max_turns=3
        # the loop should exhaust turns first, before the fallback check ever trips.
        client = QueueClient(
            [REWRITE]
            + [
                item
                for _ in range(3)
                for item in (
                    {"action": "fetch_graph_content", "args": {"node_refs": ["N99"]}, "reason": "guess"},
                    summarizer(False, "nothing yet"),
                )
            ]
        )
        tools = ScriptedTools(PAGE_SUMMARIES, [_invalid_reference_step] * 3)
        ctx = run("q", [], tools, client, max_turns=3)
        self.assertEqual(ctx.turn, 3)
        # Loop exhausts max_turns (invalid_fetch_count=3 never exceeds the >3 fallback
        # threshold), then the final empty-evidence override fires since nothing was
        # ever accepted — this mirrors pipeline_grag_fixed's existing override behavior.
        self.assertEqual(ctx.stop_reason, "empty_evidence")
        self.assertEqual(ctx.invalid_fetch_count, 3)
        self.assertFalse(ctx.fallback["triggered"])

    def test_fallback_triggers_on_valid_fetch_hard_limit(self) -> None:
        def make_fetch_step(n):
            def step(action, args, context):
                ref = context.evidence.register(EvidenceItem(
                    f"E{n}", f"chunk:{n}", "section:n1", "page:1", "Page", "https://x",
                    f"text {n}", ["Page"], "text",
                )).evidence_ref
                return ToolExecution(
                    tool="fetch_graph_content", normalized_arguments={"node_refs": [f"N{n}"]},
                    resolved_real_ids=[f"section:n{n}"], canonical_signature=f"sig-{n}", status="OK",
                    llm_visible_result=f"[N{n}] text {n}", candidate_evidence_refs=[ref],
                    candidate_groups={f"N{n}": [ref]}, fact_producing=True, is_valid_fetch=True,
                )
            return step

        responses = [REWRITE]
        steps = []
        for n in range(1, FALLBACK_VALID_FETCH_LIMIT + 2):  # one more than the hard limit
            responses.append({"action": "fetch_graph_content", "args": {"node_refs": [f"N{n}"]}, "reason": f"fetch {n}"})
            responses.append({"node_assessments": [{"node_ref": f"N{n}", "keep_evidence": [f"E{n}"], "comment": "partial"}]})
            responses.append(summarizer(False, "still incomplete"))
            steps.append(make_fetch_step(n))
        # fallback phase: page-select + hybrid retrieve returns nothing new + no judge needed
        responses.append({"page_refs": ["P1"], "reason": "best guess"})

        class TrackingTools(ScriptedTools):
            def hybrid_retrieve(self, query, page_ids, context):
                return ToolExecution(
                    tool="hybrid_retrieve", normalized_arguments={"query": query}, resolved_real_ids=[],
                    canonical_signature="hybrid-sig", status="OK", llm_visible_result="no new results",
                    candidate_evidence_refs=[], candidate_groups={}, fact_producing=True, is_valid_fetch=True,
                )

        tools = TrackingTools(PAGE_SUMMARIES, steps)
        ctx = run("q", [], tools, QueueClient(responses), max_turns=9)
        self.assertEqual(ctx.stop_reason, "fallback_hybrid_retrieve")
        self.assertTrue(ctx.fallback["triggered"])
        self.assertGreater(ctx.valid_fetch_count, FALLBACK_VALID_FETCH_LIMIT)

    def test_fallback_triggers_on_invalid_fetch_limit_when_valid_is_low(self) -> None:
        responses = [REWRITE]
        for _ in range(FALLBACK_INVALID_FETCH_LIMIT + 1):
            responses.append({"action": "fetch_graph_content", "args": {"node_refs": ["N99"]}, "reason": "bad guess"})
            responses.append(summarizer(False, "nothing yet"))
        responses.append({"page_refs": ["P1"], "reason": "best guess"})

        class TrackingTools(ScriptedTools):
            def hybrid_retrieve(self, query, page_ids, context):
                return ToolExecution(
                    tool="hybrid_retrieve", normalized_arguments={"query": query}, resolved_real_ids=[],
                    canonical_signature="hybrid-sig", status="OK", llm_visible_result="no new results",
                    candidate_evidence_refs=[], candidate_groups={}, fact_producing=True, is_valid_fetch=True,
                )

        tools = TrackingTools(PAGE_SUMMARIES, [_invalid_reference_step] * (FALLBACK_INVALID_FETCH_LIMIT + 1))
        ctx = run("q", [], tools, QueueClient(responses), max_turns=9)
        self.assertEqual(ctx.stop_reason, "fallback_hybrid_retrieve")
        self.assertLessEqual(ctx.valid_fetch_count, FALLBACK_VALID_FETCH_LIMIT)
        self.assertGreater(ctx.invalid_fetch_count, FALLBACK_INVALID_FETCH_LIMIT)

    def test_fallback_triggers_when_no_fetch_ever_attempted(self) -> None:
        # Agent only ever calls inspect_page — valid_fetch_count and invalid_fetch_count
        # both stay 0 forever, so this must trip the third (stuck-turn) condition rather
        # than silently running to max_turns. This is the gap the union-completeness
        # check in PLAN.md §3.8 specifically added.
        responses = [REWRITE]
        for _ in range(FALLBACK_STUCK_TURN_LIMIT):
            responses.append({"action": "inspect_page", "args": {"page_ref": "P1"}, "reason": "look again"})
            responses.append(summarizer(False, "still looking"))
        responses.append({"page_refs": ["P1"], "reason": "best guess"})

        class TrackingTools(ScriptedTools):
            def hybrid_retrieve(self, query, page_ids, context):
                return ToolExecution(
                    tool="hybrid_retrieve", normalized_arguments={"query": query}, resolved_real_ids=[],
                    canonical_signature="hybrid-sig", status="OK", llm_visible_result="no new results",
                    candidate_evidence_refs=[], candidate_groups={}, fact_producing=True, is_valid_fetch=True,
                )

        tools = TrackingTools(PAGE_SUMMARIES, [_inspect_step] * FALLBACK_STUCK_TURN_LIMIT)
        ctx = run("q", [], tools, QueueClient(responses), max_turns=9)
        self.assertEqual(ctx.stop_reason, "fallback_hybrid_retrieve")
        self.assertEqual(ctx.valid_fetch_count, 0)
        self.assertEqual(ctx.invalid_fetch_count, 0)

    def test_judge_caps_new_acceptance_at_ten_and_assesses_each_group(self) -> None:
        ctx = AgentContext(QueryState("compound"), ReferenceRegistry())
        groups = {"N1": [], "N2": []}
        for index in range(12):
            ref = ctx.refs.assign("evidence", f"chunk:{index}", f"evidence {index}")
            ctx.evidence.register(
                EvidenceItem(ref, f"chunk:{index}", "node", "page", "Page", "url", f"text {index}", ["Page"], "text")
            )
            groups["N1" if index < 6 else "N2"].append(ref)
        execution = ToolExecution(
            tool="fetch_graph_content",
            normalized_arguments={"node_refs": ["N1", "N2"]},
            resolved_real_ids=["node:1", "node:2"],
            canonical_signature="sig",
            status="OK",
            llm_visible_result="",
            candidate_evidence_refs=[ref for refs in groups.values() for ref in refs],
            candidate_groups=groups,
            fact_producing=True,
        )
        client = QueueClient([{
            "node_assessments": [
                {"node_ref": group, "keep_evidence": refs, "comment": f"{group} useful."}
                for group, refs in groups.items()
            ],
        }])
        assessments, kept = _judge(ctx, execution, client)
        self.assertEqual(len(assessments), 2)
        self.assertEqual(len(kept), 10)
        self.assertEqual(len(ctx.evidence.accepted()), 10)

    def test_node_evaluation_log_grouped_by_page(self) -> None:
        ctx = AgentContext(QueryState("q"), ReferenceRegistry())
        page_ref = ctx.refs.assign("page", "page:1", "How to Apply", {"description": "..."})
        node_ref = ctx.refs.assign("node", "section:fee", "Application Fee", {"page_ref": page_ref, "path": ["How to Apply", "Application Fee"]})
        evidence_ref = ctx.refs.assign("evidence", "chunk:fee", "fee text")
        ctx.evidence.register(EvidenceItem(evidence_ref, "chunk:fee", "section:fee", "page:1", "How to Apply", "url", "$90 fee", ["How to Apply", "Application Fee"], "text"))
        assessments = [{"node_ref": node_ref, "keep_evidence": [evidence_ref], "comment": "Confirms the fee."}]
        _update_node_evaluation_log(ctx, assessments)
        page_key = f"{page_ref} (How to Apply)"
        node_key = f"{node_ref} (Application Fee)"
        self.assertIn(page_key, ctx.node_evaluation_log)
        self.assertIn(node_key, ctx.node_evaluation_log[page_key])
        evaluation = ctx.node_evaluation_log[page_key][node_key]
        self.assertEqual(evaluation.node_id, "section:fee")
        self.assertEqual(evaluation.path, ["How to Apply", "Application Fee"])
        self.assertEqual(evaluation.eff_evidence[0]["evidence_ref"], evidence_ref)

    def test_max_turns_empty_vs_partial_answer(self) -> None:
        empty = AgentContext(QueryState("q"), ReferenceRegistry())
        empty.stop_reason = "empty_evidence"
        # No Python-level short-circuit anymore: empty evidence still goes through the
        # LLM so it can distinguish "off-topic" from "in-scope but uncovered" per
        # ANSWER_GENERATION_SYSTEM's NO EVIDENCE rule — verify the call actually
        # reaches the client and citations stay empty regardless of what it says.
        empty_client = QueueClient(text="I can only answer questions about the UChicago MS in Applied Data Science program.")
        empty_answer = generate(empty, empty_client)
        self.assertEqual(empty_answer.answer, empty_client.text)
        self.assertEqual(empty_answer.citations, [])

        partial = AgentContext(QueryState("q"), ReferenceRegistry())
        ref = partial.refs.assign("evidence", "chunk:1", "fact")
        item = EvidenceItem(ref, "chunk:1", "node", "page", "Page", "https://example.edu", "partial fact", ["Page"], "text", status="accepted")
        partial.evidence.register(item)
        partial.judgment.missing = "remaining fact"
        partial.stop_reason = "max_agent_turns"
        partial_answer = generate(partial, QueueClient(text="Partial answer [1]"))
        self.assertEqual(partial_answer.answer, "Partial answer [1]")
        self.assertEqual(len(partial_answer.citations), 1)


if __name__ == "__main__":
    unittest.main()

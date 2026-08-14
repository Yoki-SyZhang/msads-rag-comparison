from __future__ import annotations

import unittest

from pipeline_grag_v2.agent.prompts import agent_decision_user
from pipeline_grag_v2.agent.references import ReferenceRegistry
from pipeline_grag_v2.agent.schemas import AgentContext, QueryState, ReferenceEntry, ToolCallRecord
from pipeline_grag_v2.agent.tools import NODE_LIMIT, AgentTools
from pipeline_grag_v2.build_index import PIPELINE_ROOT, build_page_summaries
from pipeline_grag_v2.grag.graph_tools import GraphNavigator
from pipeline_grag_v2.grag.kg_builder import build_graph


def context() -> AgentContext:
    return AgentContext(QueryState("test query"), ReferenceRegistry())


class _FakeLeafNavigator:
    """A minimal navigator stand-in for a graph leaf whose OWN chunk count exceeds
    NODE_LIMIT with no structural children to redirect to — this shape does not exist
    in the real corpus (real leaves top out at 4-6 direct chunks), so it must be
    exercised synthetically. See PLAN.md §3.3's "deepest displayed node" exception."""

    nodes: dict = {}

    def descendant_content_count(self, node_id: str) -> int:
        return 20

    def has_structural_children(self, node_id: str) -> bool:
        return False

    def structural_children_edges(self, node_id: str):
        return []

    def chunks_for_node(self, node_id: str):
        return [
            {
                "id": f"chunk:leaf{i}", "text": f"leaf text {i}", "path": ["Big Leaf Page", "Big Leaf"],
                "page_title": "Big Leaf Page", "owner_node_id": node_id, "source_type": "body_text",
            }
            for i in range(20)
        ]


class ReferenceAndToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        graph, chunks = build_graph(PIPELINE_ROOT / "raw")
        summaries = build_page_summaries(graph, PIPELINE_ROOT / "docs" / "page_summaries.json")
        cls.navigator = GraphNavigator(
            PIPELINE_ROOT / "index",
            {"graph": graph, "chunks": chunks, "page_summaries": summaries},
        )
        cls.tools = AgentTools(
            PIPELINE_ROOT / "index",
            loaded_index={"graph": graph, "chunks": chunks, "page_summaries": summaries},
            navigator=cls.navigator,
        )

    def _inspect_apply(self):
        ctx = context()
        self.tools.load_page_summaries(ctx)
        page = next(entry for entry in ctx.refs.entries("page") if entry.label == "How to Apply")
        inspect = self.tools.inspect_page(page.ref, ctx)
        return ctx, page, inspect

    @staticmethod
    def _record(execution, turn, assessments=None, kept=None):
        return ToolCallRecord(
            turn=turn,
            tool=execution.tool,
            normalized_arguments=execution.normalized_arguments,
            resolved_real_ids=execution.resolved_real_ids,
            canonical_signature=execution.canonical_signature,
            status=execution.status,
            cached_result=execution.llm_visible_result,
            llm_visible_result=execution.llm_visible_result,
            candidate_evidence_refs=execution.candidate_evidence_refs,
            kept_evidence_refs=kept or [],
            node_assessments=assessments or [],
        )

    # ------------------------------------------------------------------
    # No-leak / short-ref guarantees (unchanged from pipeline_grag_fixed)
    # ------------------------------------------------------------------
    def test_visible_navigation_contains_only_short_refs(self) -> None:
        ctx, page, inspect = self._inspect_apply()
        self.assertIn(f"[{page.ref}] How to Apply", inspect.llm_visible_result)
        self.assertIn("Letters of Recommendation", inspect.llm_visible_result)
        self.assertNotIn("http", inspect.llm_visible_result)
        self.assertNotRegex(inspect.llm_visible_result, r"\b(?:page|section|chunk):[0-9a-f]{8}")
        self.assertNotIn("HAS_CONTENT", inspect.llm_visible_result)
        self.assertNotIn("source_type", inspect.llm_visible_result)
        self.assertTrue(ctx.refs.entries("node"))

    def test_page_ref_is_forbidden_in_fetch(self) -> None:
        ctx, page, _ = self._inspect_apply()
        result = self.tools.fetch_graph_content([page.ref], ctx)
        self.assertEqual(result.status, "INVALID_REFERENCE")
        self.assertFalse(result.executed)

    def test_decorated_short_refs_are_canonicalized(self) -> None:
        ctx, page, inspect = self._inspect_apply()
        decorated = self.tools.inspect_page(f"[{page.ref}] HOW TO APPLY", ctx)
        self.assertEqual(decorated.status, "OK")
        node = next(entry for entry in ctx.refs.entries("node") if entry.label == "Application Fee")
        fetched = self.tools.fetch_graph_content([f"[{node.ref}] Application Fee"], ctx)
        self.assertEqual(fetched.status, "OK")
        self.assertEqual(fetched.normalized_arguments["node_refs"], [node.ref])
        self.assertIsNone(ctx.refs.resolve("HOW TO APPLY", "page"))

    # ------------------------------------------------------------------
    # inspect_page: allowed to repeat, no dedup at all (PLAN.md §3.2)
    # ------------------------------------------------------------------
    def test_inspect_page_repeats_freely_with_stable_refs(self) -> None:
        ctx, page, first = self._inspect_apply()
        first_node_refs = {entry.real_id: entry.ref for entry in ctx.refs.entries("node")}
        second = self.tools.inspect_page(page.ref, ctx)
        self.assertEqual(second.status, "OK")
        self.assertNotEqual(second.status, "ALREADY_INSPECTED")
        second_node_refs = {entry.real_id: entry.ref for entry in ctx.refs.entries("node")}
        self.assertEqual(first_node_refs, second_node_refs)
        self.assertEqual(first.llm_visible_result, second.llm_visible_result)

    def test_collapsed_branch_below_limit_gets_no_further_node_refs(self) -> None:
        ctx = context()
        self.tools.load_page_summaries(ctx)
        page = next(entry for entry in ctx.refs.entries("page") if entry.label == "Course Progressions")
        tree = self.navigator.structural_tree(page.real_id)
        self.tools.inspect_page(page.ref, ctx)
        direct_child_ids = {child["id"] for child in tree.get("children", [])}
        # Any node expanded further (child_node_refs recorded via add_child) must
        # either be one of the page's own direct children (always expanded, §3.2) or
        # exceed NODE_LIMIT — collapse and fetch-safety must stay aligned (§3.3).
        for entry in ctx.refs.entries("node"):
            if entry.real_id in direct_child_ids:
                continue
            if entry.payload.get("child_node_refs"):
                self.assertGreater(
                    entry.payload.get("content_count", 0), NODE_LIMIT,
                    f"{entry.ref} ({entry.label}) was expanded further despite being <= NODE_LIMIT",
                )

    # ------------------------------------------------------------------
    # fetch_graph_content: new scope check aligned with NODE_LIMIT (§3.3)
    # ------------------------------------------------------------------
    def test_multi_node_fetch_deduplicates_chunks(self) -> None:
        ctx, _, inspect = self._inspect_apply()
        wanted = {"Letters of Recommendation", "Candidate Statement", "Resume/CV"}
        refs = [entry.ref for entry in ctx.refs.entries("node") if entry.label in wanted]
        result = self.tools.fetch_graph_content(refs, ctx)
        self.assertEqual(result.status, "OK")
        chunk_ids = [ctx.evidence.get(ref).chunk_id for ref in result.candidate_evidence_refs]
        self.assertEqual(len(chunk_ids), len(set(chunk_ids)))
        self.assertNotRegex(result.llm_visible_result, r"\b(?:page|section|chunk):[0-9a-f]{8}")

    def test_repeated_node_and_mixed_old_new_processing(self) -> None:
        ctx, _, inspect = self._inspect_apply()
        letter = next(entry for entry in ctx.refs.entries("node") if entry.label == "Letters of Recommendation")
        statement = next(entry for entry in ctx.refs.entries("node") if entry.label == "Candidate Statement")
        first = self.tools.fetch_graph_content([letter.ref], ctx)
        assessment = [{"node_ref": letter.ref, "keep_evidence": first.candidate_evidence_refs, "comment": "Directly answers letters only."}]
        ctx.tool_history.append(self._record(first, 3, assessment, first.candidate_evidence_refs))
        repeated = self.tools.fetch_graph_content([letter.ref], ctx)
        self.assertEqual(repeated.status, "ALREADY_CHECKED")
        self.assertFalse(repeated.executed)
        mixed = self.tools.fetch_graph_content([letter.ref, statement.ref], ctx)
        self.assertEqual(mixed.status, "OK_WITH_ALREADY_CHECKED")
        self.assertIn("ALREADY_CHECKED", mixed.llm_visible_result)
        self.assertEqual(set(mixed.candidate_groups), {statement.ref})

    def test_same_real_node_under_alias_is_still_duplicate(self) -> None:
        ctx, _, inspect = self._inspect_apply()
        letter = next(entry for entry in ctx.refs.entries("node") if entry.label == "Letters of Recommendation")
        first = self.tools.fetch_graph_content([letter.ref], ctx)
        ctx.tool_history.append(self._record(first, 3, kept=first.candidate_evidence_refs))
        ctx.refs._entries["N99"] = ReferenceEntry("N99", "node", letter.real_id, letter.label, letter.payload)
        repeated = self.tools.fetch_graph_content(["N99"], ctx)
        self.assertEqual(repeated.status, "ALREADY_CHECKED")
        self.assertFalse(repeated.executed)

    def test_too_broad_node_with_children_is_rejected_with_suggestions(self) -> None:
        ctx = context()
        self.tools.load_page_summaries(ctx)
        page = next(entry for entry in ctx.refs.entries("page") if entry.label == "Course Progressions")
        self.tools.inspect_page(page.ref, ctx)
        broad = max(ctx.refs.entries("node"), key=lambda entry: entry.payload.get("content_count", 0))
        self.assertGreater(broad.payload["content_count"], NODE_LIMIT)
        self.assertTrue(broad.payload.get("has_structural_children"))
        result = self.tools.fetch_graph_content([broad.ref], ctx)
        self.assertEqual(result.status, "NODE_SCOPE_TOO_BROAD")
        self.assertFalse(result.candidate_evidence_refs)
        self.assertIn("too broad", result.llm_visible_result)
        # child_node_refs were recorded during inspect_page's walk, so a suggestion
        # should be present whenever the node has real children.
        if broad.payload.get("child_node_refs"):
            self.assertIn("Consider its children", result.llm_visible_result)

    def test_offender_in_batch_does_not_block_admissible_sibling(self) -> None:
        ctx = context()
        self.tools.load_page_summaries(ctx)
        page = next(entry for entry in ctx.refs.entries("page") if entry.label == "Online Program")
        self.tools.inspect_page(page.ref, ctx)
        # Find a too-broad node whose suggested child is itself a wide-shallow branch
        # (fetchable despite its own large content_count) — the scenario that exposed
        # the all-or-nothing batch rejection: a rejected parent bundled with its now-
        # fetchable child must not drag the child down with it.
        broad = None
        wide_child_ref = None
        for entry in ctx.refs.entries("node"):
            if entry.payload.get("content_count", 0) <= NODE_LIMIT or entry.payload.get("is_wide_shallow_branch"):
                continue
            for child_ref in entry.payload.get("child_node_refs", []):
                child = ctx.refs.resolve(child_ref, "node")
                if child and child.payload.get("is_wide_shallow_branch"):
                    broad, wide_child_ref = entry, child_ref
                    break
            if broad:
                break
        if broad is None:
            self.skipTest("No too-broad node with a wide-shallow-branch child found in this corpus build.")

        result = self.tools.fetch_graph_content([broad.ref, wide_child_ref], ctx)
        self.assertEqual(result.status, "OK")
        self.assertIn("too broad", result.llm_visible_result)
        self.assertEqual(set(result.candidate_groups), {wide_child_ref})
        self.assertNotIn(broad.real_id, result.resolved_real_ids)
        self.assertIn(broad.ref, ctx.too_broad_node_refs)

        # A later solo request for the offender must still be cleanly rejected, not
        # falsely short-circuited as ALREADY_CHECKED using the sibling's history.
        repeated = self.tools.fetch_graph_content([broad.ref], ctx)
        self.assertEqual(repeated.status, "NODE_SCOPE_TOO_BROAD")

    def test_too_broad_node_is_annotated_in_available_refs_on_later_turns(self) -> None:
        ctx = context()
        self.tools.load_page_summaries(ctx)
        page = next(entry for entry in ctx.refs.entries("page") if entry.label == "Online Program")
        self.tools.inspect_page(page.ref, ctx)
        broad = next(
            entry for entry in ctx.refs.entries("node")
            if entry.payload.get("content_count", 0) > NODE_LIMIT and not entry.payload.get("is_wide_shallow_branch")
        )
        self.tools.fetch_graph_content([broad.ref], ctx)
        self.assertIn(broad.ref, ctx.too_broad_node_refs)
        # The annotation must persist into a fresh decision prompt on a later turn —
        # not just be visible transiently within the rejecting call itself, since each
        # decision call is stateless and only ever sees what agent_decision_user renders.
        prompt_text = agent_decision_user(ctx)
        self.assertIn(f"[{broad.ref}]", prompt_text)
        self.assertIn("ALREADY REJECTED", prompt_text)

    def test_leaf_node_exceeding_limit_is_still_allowed(self) -> None:
        ctx = context()
        fake_tools = AgentTools("unused", loaded_index={}, navigator=_FakeLeafNavigator())
        node_ref = ctx.refs.assign(
            "node", "section:big-leaf", "Big Leaf",
            {"content_count": 20, "page_ref": "P1", "path": ["Big Leaf Page", "Big Leaf"], "has_structural_children": False},
        )
        result = fake_tools.fetch_graph_content([node_ref], ctx)
        self.assertEqual(result.status, "OK")
        self.assertEqual(len(result.candidate_evidence_refs), 20)

    def test_fetch_node_refs_capped_at_five(self) -> None:
        ctx, _, inspect = self._inspect_apply()
        refs = [entry.ref for entry in ctx.refs.entries("node")][:6]
        self.assertEqual(len(refs), 6)
        result = self.tools.fetch_graph_content(refs, ctx)
        self.assertEqual(result.status, "INVALID_ARGUMENTS")

    def test_hierarchical_display_shows_sub_numbering(self) -> None:
        ctx = context()
        self.tools.load_page_summaries(ctx)
        page = next(entry for entry in ctx.refs.entries("page") if entry.label == "Course Progressions")
        self.tools.inspect_page(page.ref, ctx)
        # Find a node with real structural children and content small enough to fetch.
        candidate = next(
            (e for e in ctx.refs.entries("node")
             if e.payload.get("has_structural_children") and 2 <= e.payload.get("content_count", 0) <= NODE_LIMIT),
            None,
        )
        if candidate is None:
            self.skipTest("No small node with structural children found in this corpus build.")
        result = self.tools.fetch_graph_content([candidate.ref], ctx)
        self.assertIn("OK", result.status)
        self.assertIn(f"{candidate.ref}.1", result.llm_visible_result)
        # Full path shown once at the top heading only.
        self.assertIn("path:", result.llm_visible_result)

    # ------------------------------------------------------------------
    # Tool surface: only inspect_page / fetch_graph_content are dispatchable
    # ------------------------------------------------------------------
    def test_hybrid_retrieve_not_dispatchable_from_main_loop(self) -> None:
        result = self.tools.dispatch("hybrid_retrieve", {"query": "x"}, context())
        self.assertEqual(result.status, "INVALID_TOOL")

    def test_fetch_chunk_and_list_page_summaries_tools_do_not_exist(self) -> None:
        self.assertFalse(hasattr(self.tools, "fetch_chunk"))
        self.assertFalse(hasattr(self.tools, "list_page_summaries"))
        result = self.tools.dispatch("fetch_chunk", {"chunk_ref": "E1"}, context())
        self.assertEqual(result.status, "INVALID_TOOL")

    def test_hybrid_returns_full_texts_and_no_scores(self) -> None:
        long_texts = [f"result {index} " + ("x" * 1200) for index in range(5)]

        def fake_retriever(*args, **kwargs):
            return [
                {
                    "id": f"chunk:{index}",
                    "text": text,
                    "url": "https://internal.example/page",
                    "page_id": "page:internal",
                    "page_title": "Test Page",
                    "path": ["Test Page", f"Section {index}"],
                    "source_type": "body_text",
                    "owner_node_id": f"node:{index}",
                    "score": 0.9,
                    "score_breakdown": {"vector": 0.8},
                }
                for index, text in enumerate(long_texts)
            ]

        tools = AgentTools("unused", loaded_index={}, navigator=self.navigator, retriever=fake_retriever)
        result = tools.hybrid_retrieve("full text", {"page:internal"}, context())
        self.assertEqual(len(result.candidate_evidence_refs), 5)
        self.assertIn("x" * 1200, result.llm_visible_result)
        self.assertNotIn("0.9", result.llm_visible_result)
        self.assertNotIn("https://", result.llm_visible_result)


if __name__ == "__main__":
    unittest.main()

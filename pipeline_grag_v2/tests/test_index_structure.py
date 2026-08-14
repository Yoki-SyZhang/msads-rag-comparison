from __future__ import annotations

import json
import unittest
from pathlib import Path

from pipeline_grag_v2.build_index import PIPELINE_ROOT, build_page_summaries
from pipeline_grag_v2.grag.embeddings import EMBEDDING_MODEL
from pipeline_grag_v2.grag.graph_tools import GraphNavigator
from pipeline_grag_v2.grag.kg_builder import build_graph


class IndexStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph, cls.chunks = build_graph(PIPELINE_ROOT / "raw")
        cls.summaries = build_page_summaries(
            cls.graph, PIPELINE_ROOT / "docs" / "page_summaries.json"
        )
        cls.navigator = GraphNavigator(
            PIPELINE_ROOT / "index",
            {"graph": cls.graph, "chunks": cls.chunks, "page_summaries": cls.summaries},
        )

    def test_graph_references_and_chunk_owners_are_valid(self) -> None:
        nodes = {node["id"]: node for node in self.graph["nodes"]}
        content_edges = {
            edge["target"]: edge["source"]
            for edge in self.graph["edges"]
            if edge["relation"] == "HAS_CONTENT"
        }
        for edge in self.graph["edges"]:
            self.assertIn(edge["source"], nodes)
            self.assertIn(edge["target"], nodes)
        self.assertEqual(len(self.chunks), len(content_edges))
        for chunk in self.chunks:
            self.assertEqual(nodes[chunk["id"]]["type"], "Chunk")
            self.assertEqual(content_edges[chunk["id"]], chunk["owner_node_id"])
            self.assertIn(chunk["page_id"], nodes)
            self.assertEqual(nodes[chunk["page_id"]]["type"], "Page")

    def test_character_chunk_contract(self) -> None:
        self.assertTrue(self.chunks)
        self.assertLessEqual(max(len(chunk["text"]) for chunk in self.chunks), 900)
        for chunk in self.chunks:
            if chunk["source_type"].endswith("_index"):
                self.assertEqual(chunk["block_types"], [chunk["source_type"]])
            elif chunk["source_type"] == "body_text":
                self.assertFalse(any(kind.endswith("_index") for kind in chunk["block_types"]))

    def test_page_index_and_required_page_summary(self) -> None:
        self.assertTrue(any(chunk["source_type"] == "page_index" for chunk in self.chunks))
        main = next(item for item in self.summaries if item["label"] == "main")
        self.assertIn("limited substantive detail", main["description"])
        self.assertIn("avoid for specific", main["description"])

    def test_how_to_apply_has_sibling_structural_nodes(self) -> None:
        page = next(item for item in self.summaries if item["label"] == "how_to_apply")
        tree = self.navigator.structural_tree(page["page_id"])
        labels = {child["label"] for child in tree["children"]}
        for expected in {
            "Letters of Recommendation",
            "Candidate Statement",
            "Resume/CV",
            "Programming Supplement",
            "Transcripts from all previous colleges and universities attended",
            "Application Fee",
        }:
            self.assertIn(expected, labels)

    def test_built_artifact_counts_when_index_exists(self) -> None:
        meta_path = PIPELINE_ROOT / "index" / "index_meta.json"
        if not meta_path.exists():
            self.skipTest("Full embedding index has not been built yet.")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        chunks = json.loads((PIPELINE_ROOT / "index" / "chunks.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["embedding_model"], EMBEDDING_MODEL)
        self.assertEqual(meta["num_chunks"], len(chunks))
        self.assertEqual(meta["chroma_count"], len(chunks))


if __name__ == "__main__":
    unittest.main()


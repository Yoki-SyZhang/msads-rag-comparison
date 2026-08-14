from __future__ import annotations

import unittest

from pipeline_grag_v2.build_index import PIPELINE_ROOT
from pipeline_grag_v2.grag.kg_builder import build_graph


class KnownFailureRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _, cls.chunks = build_graph(PIPELINE_ROOT / "raw")
        cls.corpus = "\n".join(chunk["text"] for chunk in cls.chunks).lower()

    def test_known_answer_evidence_exists(self) -> None:
        probes = {
            "recommendation letters": ["letters of recommendation", "recommend"],
            "application fee": ["application fee"],
            "core courses": ["core", "course"],
            "quarter schedule": ["quarter 1"],
            "GRE": ["gre", "gmat"],
            "program formats": ["in-person", "online"],
        }
        for label, terms in probes.items():
            with self.subTest(label=label):
                self.assertTrue(all(term in self.corpus for term in terms))


if __name__ == "__main__":
    unittest.main()


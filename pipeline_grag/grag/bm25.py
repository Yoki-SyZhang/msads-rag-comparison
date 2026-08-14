"""
BM25 keyword retrieval index.

Complements vector search for exact-term queries (deadlines, tuition, OPT, etc.).
Built from all chunk texts at index time; call scores(query) at retrieval time.
"""

import math
from collections import Counter, defaultdict
from typing import Dict, Iterable, List

import numpy as np

from .text import tokenize


class BM25Index:
    def __init__(self, texts: Iterable[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.docs = [tokenize(text) for text in texts]
        self.doc_lens = np.asarray([len(doc) for doc in self.docs], dtype="float32")
        self.avgdl = float(self.doc_lens.mean()) if len(self.doc_lens) else 1.0
        self.term_freqs: List[Counter] = [Counter(doc) for doc in self.docs]
        df: Dict[str, int] = defaultdict(int)
        for doc in self.docs:
            for term in set(doc):
                df[term] += 1
        n = max(1, len(self.docs))
        self.idf = {
            term: math.log(1 + (n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def scores(self, query: str) -> np.ndarray:
        terms = tokenize(query)
        scores = np.zeros(len(self.docs), dtype="float32")
        for term in terms:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, freqs in enumerate(self.term_freqs):
                tf = freqs.get(term, 0)
                if tf == 0:
                    continue
                denom = tf + self.k1 * (1 - self.b + self.b * self.doc_lens[i] / self.avgdl)
                scores[i] += idf * (tf * (self.k1 + 1)) / denom
        return scores

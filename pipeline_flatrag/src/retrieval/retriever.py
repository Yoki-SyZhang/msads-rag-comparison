"""
Retriever: wraps a ChromaDB collection and a sentence-transformers model
for query-time semantic retrieval.

Usage:
    from src.retrieval.retriever import Retriever
    r = Retriever("msads_minilm_size_512", "sentence-transformers/all-MiniLM-L6-v2")
    hits = r.retrieve("What courses are required?", top_k=5)
"""

from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHROMA_PATH = str(PROJECT_ROOT / "data" / "chroma_db")

# BGE models expect this prefix on queries (not on indexed passages).
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Retriever:
    def __init__(
        self,
        collection_name: str,
        model_name: str,
        chroma_path: str = DEFAULT_CHROMA_PATH,
    ) -> None:
        self._model_name = model_name
        self._model = SentenceTransformer(model_name)
        client = chromadb.PersistentClient(path=chroma_path)
        self._collection = client.get_collection(name=collection_name)

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        where: dict | None = None,
    ) -> list[dict]:
        """Return the top_k most similar chunks to query.

        Args:
            query:  The user's natural-language question.
            top_k:  Number of results to return.
            where:  Optional ChromaDB metadata filter. Supported operators:
                    $ne  — {"program_type": {"$ne": "online"}}   (Stage 2 router)
                    $in  — {"source_url": {"$in": ["url1", ...]}}  (Stage 3 page selector)
                    Both are standard ChromaDB where-clause operators on string fields.

        Each result dict contains:
            text, chunk_id, source_url, page_title, section,
            section_breadcrumb, content_type, program_type, distance
        """
        encoded_query = query
        if "bge" in self._model_name.lower():
            encoded_query = _BGE_QUERY_PREFIX + query

        vec: list[float] = self._model.encode(
            encoded_query, convert_to_numpy=True
        ).tolist()

        kwargs: dict = {
            "query_embeddings": [vec],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        raw = self._collection.query(**kwargs)

        results = []
        for i in range(len(raw["ids"][0])):
            meta = raw["metadatas"][0][i]
            results.append(
                {
                    "text":               raw["documents"][0][i],
                    "chunk_id":           raw["ids"][0][i],
                    "source_url":         meta.get("source_url", ""),
                    "page_title":         meta.get("page_title", ""),
                    "section":            meta.get("section", ""),
                    "section_breadcrumb": meta.get("section_breadcrumb", ""),
                    "content_type":       meta.get("content_type", ""),
                    "program_type":       meta.get("program_type", "general"),
                    "distance":           raw["distances"][0][i],
                }
            )
        return results

    def list_sections(self, url: str) -> list[dict]:
        """Return all unique section_breadcrumbs and chunk counts for a page URL."""
        result = self._collection.get(
            where={"source_url": {"$eq": url}},
            include=["metadatas"],
        )
        counts: dict[str, int] = {}
        for meta in result["metadatas"]:
            bc = meta.get("section_breadcrumb") or meta.get("section", "")
            counts[bc] = counts.get(bc, 0) + 1
        return [{"section_breadcrumb": k, "chunk_count": v} for k, v in sorted(counts.items())]

    def get_by_section(self, url: str, section_breadcrumb: str) -> list[dict]:
        """Retrieve all chunks from a specific section of a page (exact match)."""
        result = self._collection.get(
            where={"$and": [
                {"source_url":         {"$eq": url}},
                {"section_breadcrumb": {"$eq": section_breadcrumb}},
            ]},
            include=["documents", "metadatas"],
        )
        chunks = []
        for i, doc in enumerate(result["documents"]):
            meta = result["metadatas"][i]
            chunks.append(
                {
                    "text":               doc,
                    "chunk_id":           result["ids"][i],
                    "source_url":         meta.get("source_url", ""),
                    "page_title":         meta.get("page_title", ""),
                    "section":            meta.get("section", ""),
                    "section_breadcrumb": meta.get("section_breadcrumb", ""),
                    "content_type":       meta.get("content_type", ""),
                    "program_type":       meta.get("program_type", "general"),
                    "distance":           0.0,
                }
            )
        return chunks

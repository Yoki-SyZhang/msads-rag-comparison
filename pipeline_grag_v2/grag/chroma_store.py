"""Chroma persistence for pre-computed GRAG Fixed embeddings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import numpy as np


COLLECTION_NAME = "msads_grag_fixed_chunks"


def chunk_document(chunk: Dict[str, Any]) -> str:
    path_text = " > ".join(chunk.get("path", []))
    return f"{path_text}\n{chunk.get('text', '')}".strip()


def _client(chroma_dir: Path):
    import chromadb

    chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(chroma_dir))


def create_collection(chroma_dir: Path, chunks: List[Dict[str, Any]], vectors: np.ndarray) -> int:
    client = _client(chroma_dir)
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
    if chunks:
        collection.add(
            ids=[chunk["id"] for chunk in chunks],
            documents=[chunk_document(chunk) for chunk in chunks],
            metadatas=[
                {
                    "position": position,
                    "chunk_id": chunk["id"],
                    "page_id": chunk.get("page_id", ""),
                    "page_title": chunk.get("page_title", ""),
                    "source_type": chunk.get("source_type", ""),
                }
                for position, chunk in enumerate(chunks)
            ],
            embeddings=vectors.astype("float32").tolist(),
        )
    return collection.count()


def collection_count(chroma_dir: Path) -> int:
    return _client(chroma_dir).get_collection(COLLECTION_NAME).count()


def query_vector_scores(
    chroma_dir: Path,
    query_vector: np.ndarray,
    chunks: List[Dict[str, Any]],
) -> np.ndarray:
    if not chunks:
        return np.zeros(0, dtype="float32")
    collection = _client(chroma_dir).get_collection(COLLECTION_NAME)
    result = collection.query(
        query_embeddings=[query_vector.astype("float32").tolist()],
        n_results=len(chunks),
        include=["distances"],
    )
    positions = {chunk["id"]: index for index, chunk in enumerate(chunks)}
    scores = np.zeros(len(chunks), dtype="float32")
    for chunk_id, distance in zip(result.get("ids", [[]])[0], result.get("distances", [[]])[0]):
        index = positions.get(chunk_id)
        if index is not None:
            scores[index] = 1.0 - float(distance)
    return scores


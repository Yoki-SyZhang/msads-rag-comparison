"""
ChromaDB storage helpers for the local MSADS retrieval index.

build_index.py uses this file to create a fresh Chroma collection from chunks
and embeddings. retrieve.py uses the same collection to get vector scores.
"""

from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import chromadb


COLLECTION_NAME = "msads_chunks"


def chunk_document(chunk: Dict[str, Any]) -> str:
    path_text = " > ".join(chunk.get("path", []))
    return f"{path_text}\n{chunk.get('text', '')}".strip()


def chunk_metadata(chunk: Dict[str, Any], position: int) -> Dict[str, Any]:
    return {
        "position": position,
        "chunk_id": chunk.get("id", ""),
        "url": chunk.get("url", ""),
        "page_id": chunk.get("page_id", ""),
        "page_title": chunk.get("page_title", ""),
        "path": " > ".join(chunk.get("path", [])),
        "source_type": chunk.get("source_type", ""),
    }


def get_client(chroma_dir: Path) -> chromadb.PersistentClient:
    chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(chroma_dir))


def create_collection(chroma_dir: Path, chunks: List[Dict[str, Any]], vectors: np.ndarray) -> None:
    client = get_client(chroma_dir)
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    ids = [chunk["id"] for chunk in chunks]
    documents = [chunk_document(chunk) for chunk in chunks]
    metadatas = [chunk_metadata(chunk, index) for index, chunk in enumerate(chunks)]
    collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=vectors.astype("float32").tolist(),
    )


def query_vector_scores(chroma_dir: Path, query_vector: np.ndarray, chunks: List[Dict[str, Any]]) -> np.ndarray:
    client = get_client(chroma_dir)
    collection = client.get_collection(COLLECTION_NAME)
    if not chunks:
        return np.zeros(0, dtype="float32")

    result = collection.query(
        query_embeddings=[query_vector.astype("float32").tolist()],
        n_results=len(chunks),
        include=["distances"],
    )

    id_to_index = {chunk["id"]: index for index, chunk in enumerate(chunks)}
    scores = np.zeros(len(chunks), dtype="float32")
    ids = result.get("ids", [[]])[0]
    distances = result.get("distances", [[]])[0]
    for chunk_id, distance in zip(ids, distances):
        index = id_to_index.get(chunk_id)
        if index is None:
            continue
        scores[index] = 1.0 - float(distance)
    return scores

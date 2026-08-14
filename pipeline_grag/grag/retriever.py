"""Reusable hybrid retrieval core for the MSADS RAG agent and CLI."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from grag.chroma_store import query_vector_scores
from grag.embeddings import choose_embedder
from grag.index_io import manifest, read_json, read_pickle
from grag.text import tokenize


def minmax(scores: np.ndarray) -> np.ndarray:
    if len(scores) == 0:
        return scores
    lo = float(scores.min())
    hi = float(scores.max())
    if hi <= lo:
        return np.zeros_like(scores, dtype="float32")
    return (scores - lo) / (hi - lo)


def chunk_owner_maps(graph: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    owners: Dict[str, Dict[str, Any]] = {}
    for edge in graph.get("edges", []):
        if edge.get("relation") != "HAS_CONTENT":
            continue
        chunk_id = edge.get("target")
        owner_id = edge.get("source")
        owner = nodes.get(owner_id, {})
        owners[chunk_id] = {
            "owner_node_id": owner_id,
            "owner_node_name": node_name(owner),
            "owner_node_type": owner.get("type", ""),
        }
    return owners


def node_name(node: Dict[str, Any]) -> str:
    for key in ("label", "heading", "title", "page_title", "id"):
        value = node.get(key)
        if value:
            return str(value)
    return ""


def graph_scores(query: str, chunks: List[Dict[str, Any]], graph: Dict[str, Any]) -> np.ndarray:
    q_terms = set(tokenize(query))
    if not q_terms:
        return np.zeros(len(chunks), dtype="float32")

    node_labels = {
        node["id"]: " ".join(
            [
                str(node.get("label", "")),
                str(node.get("type", "")),
                " ".join(map(str, node.get("path", []))) if isinstance(node.get("path"), list) else "",
            ]
        )
        for node in graph.get("nodes", [])
    }
    parents: Dict[str, List[Any]] = {}
    for edge in graph.get("edges", []):
        parents.setdefault(edge["target"], []).append((edge["source"], edge["relation"]))

    scores = np.zeros(len(chunks), dtype="float32")
    for i, chunk in enumerate(chunks):
        node_id = chunk["id"]
        seen = set()
        frontier = [(node_id, 1.0)]
        score = 0.0
        while frontier:
            cur, weight = frontier.pop()
            if cur in seen:
                continue
            seen.add(cur)
            label_terms = set(tokenize(node_labels.get(cur, "")))
            overlap = len(q_terms & label_terms)
            if overlap:
                score += weight * overlap / max(1, len(q_terms))
            for parent, rel in parents.get(cur, []):
                rel_bonus = 0.15 if any(t in rel.lower() for t in q_terms) else 0.0
                frontier.append((parent, weight * 0.72 + rel_bonus))
        path_terms = set(tokenize(" ".join(chunk.get("path", []))))
        scores[i] = score + 0.8 * len(q_terms & path_terms) / max(1, len(q_terms))
    return scores


def intent_boosts(query: str, chunks: List[Dict[str, Any]]) -> np.ndarray:
    terms = set(tokenize(query))
    raw = query.lower()
    boosts = np.zeros(len(chunks), dtype="float32")
    for i, chunk in enumerate(chunks):
        text_lower = chunk.get("text", "").lower()
        path_lower = " ".join(chunk.get("path", [])).lower()
        haystack = " ".join(
            [
                chunk.get("text", ""),
                " ".join(chunk.get("path", [])),
                chunk.get("page_title", ""),
                chunk.get("source_type", ""),
            ]
        ).lower()
        if {"deadline", "deadlines"} & terms:
            if "deadline" in text_lower:
                boosts[i] += 0.24
                if re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},\s+\d{4}\b", text_lower):
                    boosts[i] += 0.12
            elif "deadline" in path_lower:
                boosts[i] += 0.06
        if {"tuition", "cost", "fee", "fees"} & terms and ("$" in haystack or "tuition" in haystack):
            boosts[i] += 0.12
        if "core courses" in raw and chunk.get("source_type") == "accordion_index" and "core courses" in haystack:
            boosts[i] += 0.22
        if {"visa", "opt", "stem"} & terms and ("stem" in haystack or "opt" in haystack or "visa" in haystack):
            boosts[i] += 0.12
    return boosts


def load_index(index_dir: Path) -> Dict[str, Any]:
    paths = manifest(index_dir)
    payload = {
        "graph": read_json(paths["graph"]),
        "chunks": read_json(paths["chunks"]),
        "chroma_dir": paths["chroma"],
        "bm25": read_pickle(paths["bm25"]),
        "meta": read_json(paths["meta"]),
    }
    if paths.get("page_summaries") and paths["page_summaries"].exists():
        payload["page_summaries"] = read_json(paths["page_summaries"])
    else:
        payload["page_summaries"] = []
    return payload


def retrieve_evidence(
    query: str,
    index_dir: Path | str = "index",
    top_k: int = 8,
    vector_weight: float = 0.50,
    keyword_weight: float = 0.30,
    graph_weight: float = 0.20,
    embedding_backend: str = "auto",
    loaded_index: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    index = loaded_index or load_index(Path(index_dir))
    chunks = index["chunks"]

    embedder_name = index["meta"].get("embedding_backend", "")
    backend = embedding_backend
    if backend == "auto" and embedder_name.startswith("tfidf-svd"):
        backend = "tfidf-svd"
    embedder = choose_embedder(backend)
    if embedder.name.startswith("tfidf-svd"):
        vector_scores = np.zeros(len(chunks), dtype="float32")
    else:
        q_vec = embedder.encode([query])[0]
        vector_scores = query_vector_scores(index["chroma_dir"], q_vec, chunks)

    keyword_scores = index["bm25"].scores(query)
    g_scores = graph_scores(query, chunks, index["graph"])
    boosts = intent_boosts(query, chunks)

    final = (
        vector_weight * minmax(vector_scores)
        + keyword_weight * minmax(keyword_scores)
        + graph_weight * minmax(g_scores)
        + boosts
    )
    order = np.argsort(-final)[:top_k]
    owners = chunk_owner_maps(index["graph"])

    results = []
    for rank, idx in enumerate(order, start=1):
        chunk = chunks[int(idx)]
        owner = owners.get(chunk.get("id"), {})
        results.append(
            {
                "rank": rank,
                "chunk_id": chunk.get("id"),
                "owner_node_id": owner.get("owner_node_id", ""),
                "owner_node_name": owner.get("owner_node_name", ""),
                "owner_node_type": owner.get("owner_node_type", ""),
                "score": round(float(final[idx]), 4),
                "vector_score": round(float(vector_scores[idx]), 4),
                "keyword_score": round(float(keyword_scores[idx]), 4),
                "graph_score": round(float(g_scores[idx]), 4),
                "intent_boost": round(float(boosts[idx]), 4),
                "page_id": chunk.get("page_id"),
                "page_title": chunk.get("page_title"),
                "path": chunk.get("path", []),
                "source_url": chunk.get("url"),
                "url": chunk.get("url"),
                "source_type": chunk.get("source_type"),
                "text": chunk.get("text"),
                "retrieval_query": query,
            }
        )
    return results

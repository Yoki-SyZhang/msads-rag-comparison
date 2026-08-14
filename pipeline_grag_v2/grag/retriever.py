"""Strict hybrid vector/BM25/graph retrieval for GRAG v2.

Used both by the fallback phase (page-scoped) — see agent/agent_loop.py's fallback
handling — and (indirectly, via the same scoring machinery) by nothing else: the main
agent loop is pure graph navigation and never calls this module directly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import numpy as np

from .chroma_store import collection_count, query_vector_scores
from .embeddings import EMBEDDING_MODEL, load_embedder
from .index_io import manifest, read_json, read_pickle
from .text import tokenize


def _minmax(scores: np.ndarray) -> np.ndarray:
    if not len(scores):
        return scores
    low, high = float(scores.min()), float(scores.max())
    if high <= low:
        return np.zeros_like(scores, dtype="float32")
    return (scores - low) / (high - low)


def _node_name(node: Dict[str, Any]) -> str:
    return str(node.get("label") or node.get("title") or node.get("id") or "")


def _owner_maps(graph: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    owners: Dict[str, Dict[str, str]] = {}
    for edge in graph.get("edges", []):
        if edge.get("relation") != "HAS_CONTENT":
            continue
        owner = nodes.get(edge.get("source"), {})
        owners[edge.get("target", "")] = {
            "owner_node_id": owner.get("id", ""),
            "owner_node_name": _node_name(owner),
            "owner_node_type": owner.get("type", ""),
        }
    return owners


def graph_scores(query: str, chunks: List[Dict[str, Any]], graph: Dict[str, Any]) -> np.ndarray:
    query_terms = set(tokenize(query, remove_domain_terms=True))
    if not query_terms:
        return np.zeros(len(chunks), dtype="float32")
    labels = {
        node["id"]: " ".join(
            [
                str(node.get("label", "")),
                " ".join(map(str, node.get("path", []))),
            ]
        )
        for node in graph.get("nodes", [])
    }
    parents: Dict[str, List[str]] = {}
    for edge in graph.get("edges", []):
        parents.setdefault(edge["target"], []).append(edge["source"])

    scores = np.zeros(len(chunks), dtype="float32")
    for index, chunk in enumerate(chunks):
        current = chunk["id"]
        frontier = [(current, 1.0)]
        seen = set()
        score = 0.0
        while frontier:
            node_id, weight = frontier.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            label_terms = set(tokenize(labels.get(node_id, ""), remove_domain_terms=True))
            score += weight * len(query_terms & label_terms) / max(1, len(query_terms))
            frontier.extend((parent, weight * 0.72) for parent in parents.get(node_id, []))
        path_terms = set(
            tokenize(" ".join(chunk.get("path", [])), remove_domain_terms=True)
        )
        scores[index] = score + 0.8 * len(query_terms & path_terms) / max(1, len(query_terms))
    return scores


def intent_boosts(query: str, chunks: List[Dict[str, Any]]) -> np.ndarray:
    terms = set(tokenize(query, remove_domain_terms=True))
    raw = query.lower()
    boosts = np.zeros(len(chunks), dtype="float32")
    for index, chunk in enumerate(chunks):
        text = chunk.get("text", "").lower()
        path = " ".join(chunk.get("path", [])).lower()
        haystack = f"{text} {path} {chunk.get('page_title', '').lower()}"
        if {"deadline", "deadlines"} & terms and "deadline" in haystack:
            boosts[index] += 0.24
            if re.search(
                r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},\s+\d{4}\b",
                text,
            ):
                boosts[index] += 0.12
        if {"tuition", "cost", "fee", "fees"} & terms and ("$" in haystack or "tuition" in haystack):
            boosts[index] += 0.12
        if "core courses" in raw and chunk.get("source_type") in {"course_group_index", "accordion_index"}:
            boosts[index] += 0.22
        if {"visa", "opt", "stem"} & terms and ({"stem", "opt", "visa"} & set(haystack.split())):
            boosts[index] += 0.12
    return boosts


def load_index(index_dir: Path | str) -> Dict[str, Any]:
    paths = manifest(Path(index_dir))
    payload = {
        "graph": read_json(paths["graph"]),
        "chunks": read_json(paths["chunks"]),
        "chroma_dir": paths["chroma"],
        "bm25": read_pickle(paths["bm25"]),
        "meta": read_json(paths["meta"]),
        "page_summaries": read_json(paths["page_summaries"]),
    }
    validate_index(payload)
    return payload


def validate_index(index: Dict[str, Any]) -> None:
    meta = index["meta"]
    model = meta.get("embedding_model")
    if model != EMBEDDING_MODEL:
        raise RuntimeError(
            f"Index embedding model mismatch: expected {EMBEDDING_MODEL!r}, got {model!r}."
        )
    chunk_count = len(index["chunks"])
    if int(meta.get("num_chunks", -1)) != chunk_count:
        raise RuntimeError("Index metadata and chunks.json have different chunk counts.")
    if len(index["bm25"].docs) != chunk_count:
        raise RuntimeError("BM25 and chunks.json have different document counts.")
    if collection_count(Path(index["chroma_dir"])) != chunk_count:
        raise RuntimeError("Chroma and chunks.json have different document counts.")


def retrieve_evidence(
    query: str,
    *,
    index_dir: Path | str,
    top_k: int = 5,
    vector_weight: float = 0.50,
    keyword_weight: float = 0.30,
    graph_weight: float = 0.20,
    loaded_index: Optional[Dict[str, Any]] = None,
    page_ids: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """page_ids: only used by the fallback phase (agent_loop's fallback handling) to
    scope the final ranked output to a handful of decision-agent-selected pages. None
    (the default) means unscoped, corpus-wide retrieval — the main graph-navigation
    loop never calls this function at all.

    All three signals (vector/BM25/graph) are always scored against the FULL corpus
    first, min-max normalized against that full-corpus distribution (so scoring
    semantics stay identical to an unscoped call), and only THEN is the ranked
    candidate list restricted to page_ids. Filtering chunks *before* scoring would be
    wrong for the vector signal specifically: Chroma's query returns the globally
    nearest `n_results` neighbors, so requesting only `len(filtered_chunks)` results
    would mostly miss the filtered subset entirely and zero-fill their scores.
    """
    index = loaded_index or load_index(index_dir)
    all_chunks = index["chunks"]
    embedder = index.get("_embedder")
    if embedder is None:
        embedder = load_embedder(index["meta"]["embedding_model"])
        index["_embedder"] = embedder
    query_vector = embedder.encode_queries([query])[0]
    vector = query_vector_scores(Path(index["chroma_dir"]), query_vector, all_chunks)
    keyword = index["bm25"].scores(query)
    graph = graph_scores(query, all_chunks, index["graph"])
    boost = intent_boosts(query, all_chunks)
    final = (
        vector_weight * _minmax(vector)
        + keyword_weight * _minmax(keyword)
        + graph_weight * _minmax(graph)
        + boost
    )
    owners = _owner_maps(index["graph"])

    if page_ids is not None:
        candidates = [i for i, chunk in enumerate(all_chunks) if chunk.get("page_id") in page_ids]
    else:
        candidates = list(range(len(all_chunks)))
    candidates.sort(key=lambda i: -final[i])
    order = candidates[: min(top_k, len(candidates))]

    results = []
    for rank, position in enumerate(order, start=1):
        chunk = all_chunks[position]
        owner = owners.get(chunk["id"], {})
        results.append(
            {
                **chunk,
                **owner,
                "rank": rank,
                "score": round(float(final[position]), 6),
                "score_breakdown": {
                    "vector": round(float(vector[position]), 6),
                    "bm25": round(float(keyword[position]), 6),
                    "graph": round(float(graph[position]), 6),
                    "intent_boost": round(float(boost[position]), 6),
                },
                "retrieval_query": query,
            }
        )
    return results

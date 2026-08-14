"""Build GRAG Fixed KG, chunks, BGE vectors, BM25, and page summaries."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List

from .grag.bm25 import BM25Index
from .grag.chroma_store import create_collection
from .grag.embeddings import EMBEDDING_MODEL, load_embedder
from .grag.index_io import manifest, write_json, write_pickle
from .grag.kg_builder import build_graph


PIPELINE_ROOT = Path(__file__).resolve().parent


def _normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def build_page_summaries(graph: Dict[str, Any], config_path: Path) -> List[Dict[str, Any]]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pages = [node for node in graph["nodes"] if node.get("type") == "Page"]
    by_url = {_normalize_url(page.get("url", "")): page for page in pages}
    mapped: List[Dict[str, Any]] = []
    seen = set()
    for item in config:
        for url in item.get("urls", []):
            page = by_url.get(_normalize_url(url))
            if not page or page["id"] in seen:
                continue
            seen.add(page["id"])
            mapped.append(
                {
                    "label": item.get("label", ""),
                    "description": item.get("description", ""),
                    "page_id": page["id"],
                    "page_title": page.get("label", ""),
                    "source_url": page.get("url", ""),
                }
            )
    for page in pages:
        if page["id"] not in seen:
            mapped.append(
                {
                    "label": page.get("label", "").lower().replace(" ", "_"),
                    "description": page.get("label", ""),
                    "page_id": page["id"],
                    "page_title": page.get("label", ""),
                    "source_url": page.get("url", ""),
                }
            )
    return mapped


def build(
    raw_dir: Path = PIPELINE_ROOT / "raw",
    processed_dir: Path = PIPELINE_ROOT / "processed",
    index_dir: Path = PIPELINE_ROOT / "index",
) -> Dict[str, Any]:
    graph, chunks = build_graph(raw_dir)
    texts = [f"{' > '.join(chunk.get('path', []))}\n{chunk['text']}" for chunk in chunks]
    embedder = load_embedder(EMBEDDING_MODEL, allow_download=True)
    vectors = embedder.encode_documents(texts)
    bm25 = BM25Index(texts)
    summaries = build_page_summaries(graph, PIPELINE_ROOT / "docs" / "page_summaries.json")

    paths = manifest(index_dir)
    if paths["chroma"].exists():
        shutil.rmtree(paths["chroma"])
    processed_dir.mkdir(parents=True, exist_ok=True)
    index_dir.mkdir(parents=True, exist_ok=True)
    write_json(paths["graph"], graph)
    write_json(paths["chunks"], chunks)
    write_pickle(paths["bm25"], bm25)
    chroma_count = create_collection(paths["chroma"], chunks, vectors)
    write_json(paths["page_summaries"], summaries)
    metadata = {
        "pipeline_id": "pipeline_grag_v2",
        "embedding_backend": embedder.name,
        "embedding_model": EMBEDDING_MODEL,
        "query_instruction": "Represent this sentence for searching relevant passages: ",
        "chunk_target_characters": 900,
        "chunk_overlap_characters": 90,
        "num_nodes": len(graph["nodes"]),
        "num_edges": len(graph["edges"]),
        "num_chunks": len(chunks),
        "vector_shape": list(vectors.shape),
        "chroma_count": chroma_count,
        "hybrid_weights": {"vector": 0.50, "bm25": 0.30, "graph": 0.20},
    }
    write_json(paths["meta"], metadata)
    write_json(processed_dir / "knowledge_graph.json", graph)
    write_json(processed_dir / "chunks.json", chunks)
    write_json(processed_dir / "page_summaries.json", summaries)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=PIPELINE_ROOT / "raw")
    parser.add_argument("--processed-dir", type=Path, default=PIPELINE_ROOT / "processed")
    parser.add_argument("--index-dir", type=Path, default=PIPELINE_ROOT / "index")
    args = parser.parse_args()
    print(json.dumps(build(args.raw_dir, args.processed_dir, args.index_dir), indent=2))


if __name__ == "__main__":
    main()

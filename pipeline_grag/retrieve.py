"""CLI wrapper for the reusable MSADS hybrid retriever."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List

from grag.retriever import retrieve_evidence


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hybrid retrieve over MSADS KG + vector + BM25 indexes.")
    parser.add_argument("query", nargs="?", help="Question or search query.")
    parser.add_argument("--index-dir", default="index")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--vector-weight", type=float, default=0.50)
    parser.add_argument("--keyword-weight", type=float, default=0.30)
    parser.add_argument("--graph-weight", type=float, default=0.20)
    parser.add_argument("--embedding-backend", default="auto")
    parser.add_argument("--json", action="store_true", help="Print raw JSON results.")
    return parser.parse_args()


def print_human(results: List[Dict[str, Any]]) -> None:
    for item in results:
        path = " > ".join(item.get("path", []))
        print(f"\n#{item['rank']} score={item['score']} page={item['page_title']}")
        print(f"path: {path}")
        print(f"url: {item['source_url']}")
        print(f"chunk_id: {item['chunk_id']}")
        if item.get("owner_node_id"):
            print(f"owner: {item['owner_node_name']} ({item['owner_node_id']})")
        print(
            f"signals: vector={item['vector_score']} keyword={item['keyword_score']} graph={item['graph_score']} boost={item['intent_boost']}"
        )
        text = item["text"]
        print(text[:700] + ("..." if len(text) > 700 else ""))


def main() -> None:
    args = parse_args()
    if not args.query:
        args.query = input("Query: ").strip()
    results = retrieve_evidence(
        args.query,
        index_dir=args.index_dir,
        top_k=args.top_k,
        vector_weight=args.vector_weight,
        keyword_weight=args.keyword_weight,
        graph_weight=args.graph_weight,
        embedding_backend=args.embedding_backend,
    )
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_human(results)


if __name__ == "__main__":
    main()

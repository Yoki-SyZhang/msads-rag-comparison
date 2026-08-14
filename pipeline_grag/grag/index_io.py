"""
Index file I/O helpers.

Centralizes JSON/pickle read-write utilities and the index/ directory manifest
so build_index.py and retriever.py share consistent file paths.
"""

import json
import pickle
from pathlib import Path
from typing import Any, Dict


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_pickle(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(data, f)


def read_pickle(path: Path) -> Any:
    with path.open("rb") as f:
        return pickle.load(f)


def manifest(index_dir: Path) -> Dict[str, Path]:
    return {
        "graph": index_dir / "knowledge_graph.json",
        "chunks": index_dir / "chunks.json",
        "chroma": index_dir / "chroma",
        "bm25": index_dir / "bm25.pkl",
        "meta": index_dir / "index_meta.json",
        "page_summaries": index_dir / "page_summaries.json",
    }

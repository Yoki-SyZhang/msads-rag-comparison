"""
Validation script for Stage 2 — vector DB loading.

Checks:
  1. data/chroma_db/ exists and is non-empty
  2. All 3 expected collections are present
  3. Each collection's chunk count matches the source data/chunks/size_*/ directory
  4. Smoke-test query returns 3 non-empty results from each collection

Run: python tests/validate_stage_2_vectorDB.py
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import chromadb

CHROMA_PATH = PROJECT_ROOT / "data" / "chroma_db"
CHUNKS_DIR = PROJECT_ROOT / "data" / "chunks"

COLLECTION_SPECS = {
    "msads_minilm_size_512": {
        "config": "size_512",
        "model_name": "sentence-transformers/all-MiniLM-L6-v2",
    },
    "msads_minilm_size_900": {
        "config": "size_900",
        "model_name": "sentence-transformers/all-MiniLM-L6-v2",
    },
    "msads_bgesmall_size_900": {
        "config": "size_900",
        "model_name": "BAAI/bge-small-en-v1.5",
    },
}

SMOKE_QUERY = (
    "What are the admission requirements for the MS in Applied Data Science program?"
)


def count_chunks_in_dir(config_dir: Path) -> int:
    """Count unique chunk IDs across all files (mirrors embedding_DB deduplication)."""
    seen: set[str] = set()
    for path in config_dir.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        for chunk in record.get("chunks", []):
            seen.add(chunk["chunk_id"])
    return len(seen)


def main() -> None:
    passed = 0
    failed = 0

    def ok(msg: str) -> None:
        nonlocal passed
        passed += 1
        print(f"  PASS  {msg}")

    def fail(msg: str) -> None:
        nonlocal failed
        failed += 1
        print(f"  FAIL  {msg}")

    print("\n=== validate_stage_2_vectorDB ===\n")

    # ── Check 1: chroma_db exists ────────────────────────────────────────────
    print("[1] data/chroma_db/ exists and is non-empty")
    if CHROMA_PATH.exists() and any(CHROMA_PATH.iterdir()):
        ok(f"{CHROMA_PATH.relative_to(PROJECT_ROOT)} found")
    else:
        fail(f"{CHROMA_PATH.relative_to(PROJECT_ROOT)} missing or empty")

    # ── Check 2: all 3 collections present ───────────────────────────────────
    print("\n[2] All 3 collections exist in ChromaDB")
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    raw_collections = client.list_collections()
    existing = {
        c if isinstance(c, str) else c.name for c in raw_collections
    }
    for name in COLLECTION_SPECS:
        if name in existing:
            ok(f"'{name}' found")
        else:
            fail(f"'{name}' NOT found")

    # ── Check 3: chunk counts match source files ──────────────────────────────
    print("\n[3] Collection chunk counts match data/chunks/")
    for name, spec in COLLECTION_SPECS.items():
        config_dir = CHUNKS_DIR / spec["config"]
        expected = count_chunks_in_dir(config_dir)
        try:
            coll = client.get_collection(name)
            actual = coll.count()
            if actual == expected:
                ok(f"{name}: {actual} chunks (matches {spec['config']})")
            else:
                fail(
                    f"{name}: {actual} chunks in DB vs {expected} "
                    f"in {spec['config']}"
                )
        except Exception as exc:
            fail(f"{name}: could not query — {exc}")

    # ── Check 4: smoke-test query ─────────────────────────────────────────────
    print(f"\n[4] Smoke-test query (top_k=3) against all 3 collections")
    print(f"    Query: \"{SMOKE_QUERY}\"\n")
    from src.retrieval.retriever import Retriever

    for name, spec in COLLECTION_SPECS.items():
        try:
            retriever = Retriever(name, spec["model_name"])
            hits = retriever.retrieve(SMOKE_QUERY, top_k=3)
            if len(hits) == 3 and all(h["text"] for h in hits):
                ok(f"{name}: 3 non-empty results returned")
            else:
                fail(
                    f"{name}: got {len(hits)} results; "
                    f"empty texts: {[i for i, h in enumerate(hits) if not h['text']]}"
                )
        except Exception as exc:
            fail(f"{name}: retrieval error — {exc}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'=' * 42}")
    print(f"  {passed} passed, {failed} failed")
    print(f"{'=' * 42}\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

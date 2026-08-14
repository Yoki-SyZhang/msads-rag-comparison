"""
Embed chunks with sentence-transformers and load into ChromaDB.

Five collections:
  msads_minilm_size_512    — all-MiniLM-L6-v2      + size_512 chunks
  msads_minilm_size_900    — all-MiniLM-L6-v2      + size_900 chunks
  msads_bgesmall_size_900  — BAAI/bge-small-en-v1.5 + size_900 chunks
  msads_bgesmall_size_1536 — BAAI/bge-small-en-v1.5 + size_1536 chunks
  msads_bgesmall_size_1900 — BAAI/bge-small-en-v1.5 + size_1900 chunks

Run: python src/retrieval/embedding_DB.py
"""

import json
import time
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHUNKS_DIR = PROJECT_ROOT / "data" / "chunks"
CHROMA_PATH = PROJECT_ROOT / "data" / "chroma_db"

BATCH_SIZE = 32

COLLECTIONS = [
    {
        "name": "msads_minilm_size_512",
        "model_name": "sentence-transformers/all-MiniLM-L6-v2",
        "config": "size_512",
    },
    {
        "name": "msads_minilm_size_900",
        "model_name": "sentence-transformers/all-MiniLM-L6-v2",
        "config": "size_900",
    },
    {
        "name": "msads_bgesmall_size_900",
        "model_name": "BAAI/bge-small-en-v1.5",
        "config": "size_900",
    },
    {
        "name": "msads_bgesmall_size_1536",
        "model_name": "BAAI/bge-small-en-v1.5",
        "config": "size_1536",
    },
    {
        "name": "msads_bgesmall_size_1900",
        "model_name": "BAAI/bge-small-en-v1.5",
        "config": "size_1900",
    },
]


def load_chunks(config_dir: Path) -> tuple[list[str], list[str], list[dict]]:
    """Read all chunk JSON files in config_dir.

    Returns (ids, texts, metadatas) ready for collection.add().
    url_aliases is serialized to a JSON string because ChromaDB metadata
    values must be scalars.

    Duplicate chunk_ids (a known chunker bug where some sections are emitted
    multiple times in the same file) are silently deduplicated — first
    occurrence wins.
    """
    ids, texts, metadatas = [], [], []
    seen: set[str] = set()
    dup_count = 0

    for path in sorted(config_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for chunk in record.get("chunks", []):
            cid = chunk["chunk_id"]
            if cid in seen:
                dup_count += 1
                continue
            seen.add(cid)
            ids.append(cid)
            page_title = chunk.get("page_title", "")
            breadcrumb = chunk.get("section_breadcrumb") or chunk.get("section", "")
            body = chunk["text"]
            prefix = f"[Page: {page_title} | Section: {breadcrumb}]"
            texts.append(f"{prefix}\n\n{body}")
            metadatas.append(
                {
                    "source_url":       chunk.get("source_url", ""),
                    "page_title":       chunk.get("page_title", ""),
                    "section":          chunk.get("section", ""),
                    "section_breadcrumb": chunk.get("section_breadcrumb", chunk.get("section", "")),
                    "content_type":     chunk.get("content_type", ""),
                    "program_type":     chunk.get("program_type", "general"),
                    "word_count":       chunk.get("word_count", 0),
                    "scraped_at":       chunk.get("scraped_at", ""),
                    "url_aliases":      json.dumps(chunk.get("url_aliases", [])),
                }
            )

    if dup_count:
        print(f"  WARNING: {dup_count} duplicate chunk_ids skipped in {config_dir.name}")
    return ids, texts, metadatas


def embed_and_load(
    client: chromadb.PersistentClient,
    collection_name: str,
    model: SentenceTransformer,
    ids: list[str],
    texts: list[str],
    metadatas: list[dict],
    batch_size: int = BATCH_SIZE,
) -> int:
    """Embed texts and load into a ChromaDB collection.

    Idempotency:
      count == expected  → skip (already loaded)
      count == 0         → embed and load
      count mismatch     → delete collection and rebuild from scratch
    """
    expected = len(ids)
    collection = client.get_or_create_collection(name=collection_name)
    current_count = collection.count()

    if current_count == expected:
        print(f"  [{collection_name}] already loaded ({current_count} chunks), skipping.")
        return current_count

    if current_count > 0:
        print(
            f"  [{collection_name}] count mismatch "
            f"({current_count} in DB vs {expected} expected). Rebuilding..."
        )
        client.delete_collection(collection_name)
        collection = client.create_collection(name=collection_name)

    print(f"  [{collection_name}] Embedding {expected} chunks (batch_size={batch_size})...")
    embeddings: list[list[float]] = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
    ).tolist()

    for start in range(0, expected, batch_size):
        end = min(start + batch_size, expected)
        collection.add(
            ids=ids[start:end],
            documents=texts[start:end],
            embeddings=embeddings[start:end],
            metadatas=metadatas[start:end],
        )

    final_count = collection.count()
    print(f"  [{collection_name}] Loaded {final_count} chunks.")
    return final_count


def main() -> None:
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))

    current_model_name: str | None = None
    model: SentenceTransformer | None = None

    results: list[tuple[str, int, float]] = []
    for spec in COLLECTIONS:
        config_dir = CHUNKS_DIR / spec["config"]
        print(f"\n--- {spec['name']} ---")

        ids, texts, metadatas = load_chunks(config_dir)
        print(f"  Chunks in {config_dir.name}: {len(ids)}")

        if spec["model_name"] != current_model_name:
            print(f"  Loading model: {spec['model_name']}")
            model = SentenceTransformer(spec["model_name"])
            current_model_name = spec["model_name"]

        t0 = time.perf_counter()
        count = embed_and_load(client, spec["name"], model, ids, texts, metadatas)
        elapsed = time.perf_counter() - t0
        results.append((spec["name"], count, elapsed))

    print("\n" + "=" * 68)
    print(f"{'Collection':<37} {'Chunks':>8}  {'Time (s)':>10}")
    print("-" * 68)
    for name, count, elapsed in results:
        print(f"{name:<37} {count:>8}  {elapsed:>10.1f}")
    print("=" * 68)


if __name__ == "__main__":
    main()

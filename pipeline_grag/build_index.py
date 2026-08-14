"""
Build all offline indexes needed for retrieval.

Reads raw page JSON from raw/, builds a DOM-aware knowledge graph and chunks,

Generates vector embeddings, builds a BM25 keyword index, and writes all artifacts to processed/ and index/. 

Re-run this whenever you want to rebuild the graph or indexes from scratch.
"""

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from grag.bm25 import BM25Index
from grag.chroma_store import create_collection
from grag.embeddings import choose_embedder
from grag.index_io import manifest, write_json, write_pickle
from grag.kg_builder import build_graph


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = value.strip("_")
    return value or "page"


def reset_generated_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def chunk_preview(text: str, limit: int = 220) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def graph_tree_for_page(graph: Dict[str, Any], page_id: str) -> Dict[str, Any]:
    nodes = {node["id"]: node for node in graph["nodes"]}
    children: Dict[str, List[Dict[str, str]]] = {}
    for edge in graph["edges"]:
        children.setdefault(edge["source"], []).append(edge)

    def build_node(node_id: str, depth: int = 0) -> Dict[str, Any]:
        node = nodes[node_id]
        item: Dict[str, Any] = {
            "id": node["id"],
            "type": node["type"],
            "label": node.get("label", ""),
        }
        for key in ["url", "source_type", "heading_tag", "path"]:
            if key in node:
                item[key] = node[key]
        if node.get("type") == "Chunk":
            item["text_preview"] = chunk_preview(node.get("text", ""))
            item["text_length"] = len(node.get("text", ""))
            return item
        item["children"] = [
            {
                "relation": edge["relation"],
                "node": build_node(edge["target"], depth + 1),
            }
            for edge in children.get(node_id, [])
        ]
        return item

    return build_node(page_id)


def write_page_debug_artifacts(out_dir: Path, graph: Dict[str, Any], chunks: List[Dict[str, Any]]) -> None:
    chunks_dir = out_dir / "chunks"
    pages_dir = out_dir / "pages"
    reset_generated_path(chunks_dir)
    reset_generated_path(pages_dir)
    chunks_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)

    page_nodes = [node for node in graph["nodes"] if node.get("type") == "Page"]
    chunks_by_page: Dict[str, List[Dict[str, Any]]] = {}
    for chunk in chunks:
        chunks_by_page.setdefault(chunk["page_id"], []).append(chunk)

    overview_pages = []
    for page in sorted(page_nodes, key=lambda item: item.get("label", "")):
        slug = slugify(page.get("label", page["id"]))
        page_chunks = chunks_by_page.get(page["id"], [])
        tree = graph_tree_for_page(graph, page["id"])
        chunks_payload = {
            "page": {
                "id": page["id"],
                "title": page.get("label"),
                "url": page.get("url"),
                "raw_file": page.get("raw_file"),
            },
            "num_chunks": len(page_chunks),
            "chunks": page_chunks,
        }
        page_payload = {
            "page": chunks_payload["page"],
            "how_to_debug": [
                "先看 graph_tree，确认网页结构是否像页面真实结构。",
                "再看 chunks，确认每个可检索文本块是否属于正确的 path。",
                "如果检索结果奇怪，优先检查对应 page 的 path 和 source_type。",
            ],
            "graph_tree": tree,
            "chunks": page_chunks,
        }
        write_json(chunks_dir / f"{slug}.json", chunks_payload)
        write_json(pages_dir / f"{slug}.json", page_payload)
        write_page_markdown(pages_dir / f"{slug}.md", page, tree, page_chunks)
        write_page_html(pages_dir / f"{slug}.html", page, tree, page_chunks)
        overview_pages.append(
            {
                "title": page.get("label"),
                "url": page.get("url"),
                "page_id": page["id"],
                "num_chunks": len(page_chunks),
                "debug_page_json": f"pages/{slug}.json",
                "debug_page_markdown": f"pages/{slug}.md",
                "debug_page_html": f"pages/{slug}.html",
                "chunks_json": f"chunks/{slug}.json",
            }
        )

    write_processed_index_html(out_dir / "index.html", overview_pages, len(graph["nodes"]), len(graph["edges"]), len(chunks))


def write_page_markdown(path: Path, page: Dict[str, Any], tree: Dict[str, Any], chunks: List[Dict[str, Any]]) -> None:
    lines = [
        f"# {page.get('label', 'Untitled Page')}",
        "",
        f"URL: {page.get('url', '')}",
        "",
        "## Graph Tree",
        "",
    ]

    def walk(item: Dict[str, Any], depth: int = 0, relation: str = "") -> None:
        indent = "  " * depth
        prefix = f"{relation} -> " if relation else ""
        label = item.get("label") or item.get("id")
        if item.get("type") == "Chunk":
            label = item.get("id")
        lines.append(f"{indent}- {prefix}{item.get('type')}: {label}")
        if item.get("type") == "Chunk":
            preview = item.get("text_preview", "")
            if preview:
                lines.append(f"{indent}  text: {preview}")
            return
        for child in item.get("children", []):
            walk(child["node"], depth + 1, child["relation"])

    walk(tree)
    lines.extend(["", "## Chunks", ""])
    for i, chunk in enumerate(chunks, start=1):
        path_text = " > ".join(chunk.get("path", []))
        lines.extend(
            [
                f"### Chunk {i}: {chunk.get('id')}",
                "",
                f"- source_type: `{chunk.get('source_type')}`",
                f"- path: {path_text}",
                "",
                chunk.get("text", ""),
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def html_escape(value: Any) -> str:
    text = str(value or "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def build_page_summaries(graph: Dict[str, Any], summaries_path: Path) -> List[Dict[str, Any]]:
    page_nodes = [node for node in graph["nodes"] if node.get("type") == "Page"]
    pages_by_url = {normalize_url(page.get("url", "")): page for page in page_nodes if page.get("url")}
    fallback_pages = {page.get("label", "").lower(): page for page in page_nodes}

    if summaries_path.exists():
        raw_summaries = json.loads(summaries_path.read_text(encoding="utf-8"))
    else:
        raw_summaries = [
            {
                "label": slugify(page.get("label", page["id"])),
                "description": page.get("label", ""),
                "urls": [page.get("url", "")],
            }
            for page in page_nodes
        ]

    mapped = []
    for item in raw_summaries:
        urls = item.get("urls", [])
        matched_pages = []
        for url in urls:
            page = pages_by_url.get(normalize_url(url))
            if page and page["id"] not in {existing["id"] for existing in matched_pages}:
                matched_pages.append(page)
        if not matched_pages:
            label = str(item.get("label", "")).replace("_", " ").lower()
            page = fallback_pages.get(label)
            if page:
                matched_pages.append(page)
        for page in matched_pages:
            mapped.append(
                {
                    "label": item.get("label", slugify(page.get("label", page["id"]))),
                    "description": item.get("description", page.get("label", "")),
                    "page_id": page["id"],
                    "page_title": page.get("label", ""),
                    "source_url": page.get("url", ""),
                }
            )
    seen = set()
    deduped = []
    for item in mapped:
        key = (item["label"], item["page_id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def render_tree_html(item: Dict[str, Any], relation: str = "") -> str:
    item_type = html_escape(item.get("type"))
    label = html_escape(item.get("label") or item.get("id"))
    rel = f'<span class="rel">{html_escape(relation)}</span>' if relation else ""
    if item.get("type") == "Chunk":
        preview = html_escape(item.get("text_preview", ""))
        source_type = html_escape(item.get("source_type", ""))
        return (
            f'<li class="node chunk-node">{rel}<span class="type">{item_type}</span>'
            f'<span class="chunk-id">{html_escape(item.get("id"))}</span>'
            f'<span class="badge">{source_type}</span>'
            f'<p>{preview}</p></li>'
        )

    children = item.get("children", [])
    child_html = "".join(render_tree_html(child["node"], child["relation"]) for child in children)
    open_attr = " open" if item.get("type") in {"Page", "Section", "Accordion", "AccordionItem"} else ""
    return (
        f'<li class="node"><details{open_attr}><summary>{rel}<span class="type">{item_type}</span>'
        f'<span class="label">{label}</span><span class="count">{len(children)}</span></summary>'
        f'<ul>{child_html}</ul></details></li>'
    )


def write_page_html(path: Path, page: Dict[str, Any], tree: Dict[str, Any], chunks: List[Dict[str, Any]]) -> None:
    chunk_cards = []
    for idx, chunk in enumerate(chunks, start=1):
        path_text = " > ".join(chunk.get("path", []))
        chunk_cards.append(
            f"""
            <article class="chunk-card">
              <div class="chunk-meta">
                <span>#{idx}</span>
                <span>{html_escape(chunk.get("source_type"))}</span>
              </div>
              <h3>{html_escape(path_text)}</h3>
              <p>{html_escape(chunk.get("text"))}</p>
            </article>
            """
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html_escape(page.get("label"))} debug</title>
  <style>
    :root {{ color-scheme: light; --ink:#171717; --muted:#666; --line:#ddd; --soft:#f6f6f4; --accent:#800000; }}
    body {{ margin:0; font-family: Arial, sans-serif; color:var(--ink); background:#fff; }}
    header {{ padding:24px 32px; border-bottom:1px solid var(--line); background:#fafafa; }}
    header h1 {{ margin:0 0 8px; font-size:28px; }}
    header a {{ color:var(--accent); word-break:break-word; }}
    main {{ display:grid; grid-template-columns:minmax(360px, 44%) 1fr; gap:24px; padding:24px 32px; }}
    section {{ min-width:0; }}
    h2 {{ margin:0 0 14px; font-size:18px; }}
    ul {{ list-style:none; padding-left:18px; margin:6px 0; border-left:1px solid var(--line); }}
    summary {{ cursor:pointer; padding:7px 8px; border-radius:6px; }}
    summary:hover {{ background:var(--soft); }}
    .type {{ display:inline-block; min-width:92px; color:var(--accent); font-weight:700; }}
    .label {{ font-weight:600; }}
    .rel {{ color:var(--muted); margin-right:8px; font-size:12px; }}
    .count,.badge {{ margin-left:8px; color:var(--muted); font-size:12px; border:1px solid var(--line); border-radius:999px; padding:1px 6px; }}
    .chunk-node {{ margin:8px 0 8px 6px; padding:10px; background:var(--soft); border-radius:6px; }}
    .chunk-node p {{ margin:6px 0 0; color:#333; line-height:1.45; white-space:pre-wrap; }}
    .chunk-id {{ font-family: Consolas, monospace; color:#444; }}
    .chunk-list {{ display:grid; gap:12px; }}
    .chunk-card {{ border:1px solid var(--line); border-radius:8px; padding:14px; }}
    .chunk-card h3 {{ margin:8px 0; font-size:14px; line-height:1.35; color:var(--accent); }}
    .chunk-card p {{ margin:0; line-height:1.5; white-space:pre-wrap; }}
    .chunk-meta {{ display:flex; gap:8px; color:var(--muted); font-size:12px; }}
    @media (max-width: 900px) {{ main {{ grid-template-columns:1fr; padding:18px; }} header {{ padding:18px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>{html_escape(page.get("label"))}</h1>
    <a href="{html_escape(page.get("url"))}">{html_escape(page.get("url"))}</a>
  </header>
  <main>
    <section>
      <h2>Graph Tree</h2>
      <ul>{render_tree_html(tree)}</ul>
    </section>
    <section>
      <h2>Chunks ({len(chunks)})</h2>
      <div class="chunk-list">{''.join(chunk_cards)}</div>
    </section>
  </main>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def write_processed_index_html(path: Path, pages: List[Dict[str, Any]], num_nodes: int, num_edges: int, num_chunks: int) -> None:
    rows = []
    for page in pages:
        rows.append(
            f"""
            <tr>
              <td><a href="{html_escape(page['debug_page_html'])}">{html_escape(page['title'])}</a></td>
              <td>{page['num_chunks']}</td>
              <td><a href="{html_escape(page['chunks_json'])}">chunks json</a></td>
              <td><a href="{html_escape(page['debug_page_json'])}">tree json</a></td>
              <td><a href="{html_escape(page['url'])}">source</a></td>
            </tr>
            """
        )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MSADS processed debug</title>
  <style>
    body {{ margin:0; font-family:Arial, sans-serif; color:#171717; }}
    header {{ padding:28px 36px; background:#f7f7f5; border-bottom:1px solid #ddd; }}
    h1 {{ margin:0 0 8px; }}
    main {{ padding:24px 36px; }}
    .stats {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:12px; }}
    .stat {{ border:1px solid #ddd; border-radius:8px; padding:10px 12px; background:#fff; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ border-bottom:1px solid #ddd; text-align:left; padding:10px; vertical-align:top; }}
    th {{ background:#fafafa; }}
    a {{ color:#800000; }}
  </style>
</head>
<body>
  <header>
    <h1>MSADS Processed Debug</h1>
    <p>从这里按 page 打开 HTML，可视化检查 graph tree 和 chunks。</p>
    <div class="stats">
      <div class="stat">nodes: {num_nodes}</div>
      <div class="stat">edges: {num_edges}</div>
      <div class="stat">chunks: {num_chunks}</div>
      <div class="stat">pages: {len(pages)}</div>
    </div>
  </header>
  <main>
    <table>
      <thead><tr><th>Page</th><th>Chunks</th><th>Chunks JSON</th><th>Tree JSON</th><th>Source</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </main>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build MSADS DOM-aware KG and retrieval indexes.")
    parser.add_argument("--raw-dir", default="raw", help="Directory containing raw page JSON files.")
    parser.add_argument("--out-dir", default="processed", help="Directory for cleaned graph/chunk artifacts.")
    parser.add_argument("--index-dir", default="index", help="Directory for retrieval indexes.")
    parser.add_argument(
        "--embedding-backend",
        default="auto",
        choices=["auto", "sentence-transformers", "sbert", "tfidf-svd"],
        help="Embedding backend. auto tries local sentence-transformers bge-small, then TF-IDF/SVD.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    index_dir = Path(args.index_dir)
    paths = manifest(index_dir)

    graph, chunks = build_graph(raw_dir)
    texts = [
        " | ".join(chunk.get("path", [])) + "\n" + chunk["text"]
        for chunk in chunks
    ]

    embedder = choose_embedder(args.embedding_backend)
    vectors = embedder.encode(texts)
    bm25 = BM25Index(texts)

    reset_generated_path(paths["chroma"])
    for stale_path in [
        index_dir / "vectors.npy",
        out_dir / "chunks.json",
        out_dir / "knowledge_graph.json",
        out_dir / "knowledge_graph_flat.json",
        out_dir / "all_chunks.json",
    ]:
        reset_generated_path(stale_path)
    write_page_debug_artifacts(out_dir, graph, chunks)
    write_json(paths["graph"], graph)
    write_json(paths["chunks"], chunks)
    create_collection(paths["chroma"], chunks, vectors)
    write_pickle(paths["bm25"], bm25)
    page_summaries = build_page_summaries(graph, Path("docs/page_summaries.json"))
    write_json(paths["page_summaries"], page_summaries)
    write_json(out_dir / "page_summaries.json", page_summaries)
    write_json(
        paths["meta"],
        {
            "raw_dir": str(raw_dir),
            "embedding_backend": embedder.name,
            "num_nodes": len(graph["nodes"]),
            "num_edges": len(graph["edges"]),
            "num_chunks": len(chunks),
            "vector_shape": list(vectors.shape),
            "vector_store": "chromadb",
            "chroma_dir": str(paths["chroma"]),
        },
    )

    print(
        json.dumps(
            {
                "embedding_backend": embedder.name,
                "nodes": len(graph["nodes"]),
                "edges": len(graph["edges"]),
                "chunks": len(chunks),
                "index_dir": str(index_dir),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

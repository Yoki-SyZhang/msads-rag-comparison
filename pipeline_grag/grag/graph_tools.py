"""Knowledge-graph navigation helpers for the MSADS RAG agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from grag.index_io import manifest, read_json
from grag.text import tokenize


CONTENT_RELATION = "HAS_CONTENT"


def node_name(node: Dict[str, Any]) -> str:
    for key in ("label", "heading", "title", "page_title", "id"):
        value = node.get(key)
        if value:
            return str(value)
    return ""


def normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def load_graph_index(index_dir: Path | str = "index") -> Dict[str, Any]:
    paths = manifest(Path(index_dir))
    graph = read_json(paths["graph"])
    chunks = read_json(paths["chunks"])
    summaries = read_json(paths["page_summaries"]) if paths["page_summaries"].exists() else []
    return {"graph": graph, "chunks": chunks, "page_summaries": summaries}


class GraphNavigator:
    def __init__(self, index_dir: Path | str = "index") -> None:
        payload = load_graph_index(index_dir)
        self.graph = payload["graph"]
        self.chunks = payload["chunks"]
        self.page_summaries = payload["page_summaries"]
        self.nodes = {node["id"]: node for node in self.graph.get("nodes", [])}
        self.children: Dict[str, List[Dict[str, Any]]] = {}
        self.parents: Dict[str, List[Dict[str, Any]]] = {}
        for edge in self.graph.get("edges", []):
            self.children.setdefault(edge["source"], []).append(edge)
            self.parents.setdefault(edge["target"], []).append(edge)
        self.chunks_by_id = {chunk["id"]: chunk for chunk in self.chunks}
        self.page_by_url = {
            normalize_url(node.get("url", "")): node
            for node in self.graph.get("nodes", [])
            if node.get("type") == "Page" and node.get("url")
        }
        self.page_by_label = {
            node_name(node).lower(): node
            for node in self.graph.get("nodes", [])
            if node.get("type") == "Page"
        }
        if not self.page_summaries and Path("docs/page_summaries.json").exists():
            self.page_summaries = self._map_raw_page_summaries(Path("docs/page_summaries.json"))

    def list_page_summaries(self, query: str = "") -> List[Dict[str, Any]]:
        summaries = self.page_summaries or self._fallback_page_summaries()
        if not query:
            return summaries
        q_terms = set(tokenize(query))

        def score(item: Dict[str, Any]) -> float:
            haystack = " ".join(
                [
                    item.get("label", ""),
                    item.get("description", ""),
                    item.get("page_title", ""),
                    item.get("source_url", ""),
                ]
            )
            terms = set(tokenize(haystack))
            return len(q_terms & terms) / max(1, len(q_terms))

        return sorted(summaries, key=score, reverse=True)

    def inspect_page(self, page_id_or_label_or_title: str) -> Dict[str, Any]:
        page = self.resolve_page(page_id_or_label_or_title)
        if not page:
            return {
                "ok": False,
                "error": f"Page not found: {page_id_or_label_or_title}",
                "llm_visible_result": "",
            }
        markdown = self._render_page_tree(page["id"])
        return {
            "ok": True,
            "page_id": page["id"],
            "page_title": node_name(page),
            "source_url": page.get("url", ""),
            "llm_visible_result": markdown,
        }

    def fetch_node_chunks(self, node_id: str, fetch_depth: int = 1) -> Dict[str, Any]:
        original_node_id = node_id
        node_id = self.resolve_node_id(node_id) or node_id
        if node_id not in self.nodes:
            return {
                "ok": False,
                "error": f"Node not found: {original_node_id}",
                "chunks": [],
                "llm_visible_result": f"Node not found: {original_node_id}. Use an exact node_id from inspect_page output.",
            }
        node_ids = self._structure_descendants(node_id, max(0, int(fetch_depth)))
        chunk_ids: List[str] = []
        for current in node_ids:
            for edge in self.children.get(current, []):
                if edge.get("relation") == CONTENT_RELATION:
                    chunk_ids.append(edge["target"])
        seen: Set[str] = set()
        chunks = []
        for chunk_id in chunk_ids:
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            chunk = self.chunks_by_id.get(chunk_id)
            if not chunk:
                node = self.nodes.get(chunk_id)
                if node and node.get("type") == "Chunk":
                    chunk = self._chunk_from_node(node)
            if chunk:
                chunks.append(self._evidence_from_chunk(chunk, self._owner_for_chunk(chunk["id"])))
        return {
            "ok": True,
            "node_id": node_id,
            "requested_node_id": original_node_id,
            "resolved_node_id": node_id,
            "node_name": node_name(self.nodes[node_id]),
            "fetch_depth": fetch_depth,
            "chunks": chunks,
            "llm_visible_result": self._format_fetch_result(original_node_id, node_id, chunks),
        }

    def fetch_chunk(self, chunk_id: str) -> Dict[str, Any]:
        chunk = self.chunks_by_id.get(chunk_id)
        if not chunk:
            node = self.nodes.get(chunk_id)
            if node and node.get("type") == "Chunk":
                chunk = self._chunk_from_node(node)
        if not chunk:
            return {
                "ok": False,
                "error": f"Chunk not found: {chunk_id}",
                "llm_visible_result": "",
            }
        owner = self._owner_for_chunk(chunk_id)
        evidence = self._evidence_from_chunk(chunk, owner)
        return {
            "ok": True,
            "chunk": evidence,
            "chunks": [evidence],
            "llm_visible_result": format_chunks_for_llm([evidence]),
        }

    def resolve_page(self, value: str) -> Optional[Dict[str, Any]]:
        key = (value or "").strip()
        if not key:
            return None
        if key in self.nodes and self.nodes[key].get("type") == "Page":
            return self.nodes[key]
        by_label = self.page_by_label.get(key.lower())
        if by_label:
            return by_label
        by_url = self.page_by_url.get(normalize_url(key))
        if by_url:
            return by_url
        for summary in self.page_summaries:
            if key.lower() == str(summary.get("label", "")).lower():
                page_id = summary.get("page_id")
                if page_id in self.nodes:
                    return self.nodes[page_id]
            if key.lower() == str(summary.get("page_title", "")).lower():
                page_id = summary.get("page_id")
                if page_id in self.nodes:
                    return self.nodes[page_id]
        for page in self.page_by_label.values():
            if key.lower() in node_name(page).lower():
                return page
        return None

    def resolve_node_id(self, value: str) -> Optional[str]:
        """Resolve exact or slightly malformed LLM-provided node identifiers.

        The agent sometimes synthesizes IDs like
        ``node:<page_hash>_how-to-apply`` instead of copying the exact
        ``page:...`` or ``section:...`` value from inspect_page. This helper
        keeps the KG tool robust while still preferring exact IDs.
        """
        key = (value or "").strip()
        if not key:
            return None
        if key in self.nodes:
            return key

        without_prefix = key.removeprefix("node:")
        if without_prefix in self.nodes:
            return without_prefix

        for prefix in ("page:", "section:", "accordion:", "chunk:"):
            candidate = prefix + without_prefix
            if candidate in self.nodes:
                return candidate

        # Common hallucination: node:<hash>_<slug>. Prefer resolving the slug.
        if "_" in without_prefix:
            suffix = without_prefix.split("_", 1)[1]
            labelish = suffix.replace("-", " ").replace("_", " ").strip()
            page = self.resolve_page(labelish)
            if page:
                return page["id"]
            label_terms = labelish.lower()
            for node in self.nodes.values():
                if label_terms and label_terms == node_name(node).lower():
                    return node["id"]
            for node in self.nodes.values():
                if label_terms and label_terms in node_name(node).lower():
                    return node["id"]

        # Last chance: treat the whole value as a page label/title/url.
        page = self.resolve_page(key)
        if page:
            return page["id"]
        return None

    def _fallback_page_summaries(self) -> List[Dict[str, Any]]:
        return [
            {
                "label": node_name(page).lower().replace(" ", "_"),
                "description": node_name(page),
                "page_id": page["id"],
                "page_title": node_name(page),
                "source_url": page.get("url", ""),
            }
            for page in self.nodes.values()
            if page.get("type") == "Page"
        ]

    def _map_raw_page_summaries(self, path: Path) -> List[Dict[str, Any]]:
        raw = read_json(path)
        mapped = []
        for item in raw:
            matches = []
            for url in item.get("urls", []):
                page = self.page_by_url.get(normalize_url(url))
                if page and page["id"] not in {existing["id"] for existing in matches}:
                    matches.append(page)
            for page in matches:
                mapped.append(
                    {
                        "label": item.get("label", ""),
                        "description": item.get("description", ""),
                        "page_id": page["id"],
                        "page_title": node_name(page),
                        "source_url": page.get("url", ""),
                    }
                )
        return mapped

    def _render_page_tree(self, page_id: str) -> str:
        page = self.nodes[page_id]
        lines = [
            f"# Page Structure: {node_name(page)}",
            f"URL: {page.get('url', '')}",
            "",
        ]

        def walk(current_id: str, depth: int = 0, relation: str = "") -> None:
            node = self.nodes[current_id]
            indent = "  " * depth
            rel = f"{relation} -> " if relation else ""
            lines.append(
                f"{indent}- {rel}{node_name(node)} [node_id: {node['id']}] [type: {node.get('type', '')}]"
            )
            for edge in self.children.get(current_id, []):
                if edge.get("relation") == CONTENT_RELATION:
                    continue
                target = edge.get("target")
                if target in self.nodes:
                    walk(target, depth + 1, edge.get("relation", ""))

        walk(page_id)
        return "\n".join(lines)

    def _structure_descendants(self, node_id: str, depth: int) -> List[str]:
        result = [node_id]
        if depth <= 0:
            return result
        frontier = [(node_id, 0)]
        seen = {node_id}
        while frontier:
            current, cur_depth = frontier.pop(0)
            if cur_depth >= depth:
                continue
            for edge in self.children.get(current, []):
                if edge.get("relation") == CONTENT_RELATION:
                    continue
                target = edge.get("target")
                if target in seen or target not in self.nodes:
                    continue
                seen.add(target)
                result.append(target)
                frontier.append((target, cur_depth + 1))
        return result

    def _owner_for_chunk(self, chunk_id: str) -> Dict[str, Any]:
        for edge in self.parents.get(chunk_id, []):
            if edge.get("relation") == CONTENT_RELATION:
                owner = self.nodes.get(edge.get("source"), {})
                return {
                    "owner_node_id": owner.get("id", ""),
                    "owner_node_name": node_name(owner),
                    "owner_node_type": owner.get("type", ""),
                }
        return {"owner_node_id": "", "owner_node_name": "", "owner_node_type": ""}

    def _chunk_from_node(self, node: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": node["id"],
            "text": node.get("text", ""),
            "url": node.get("url", ""),
            "page_id": "",
            "page_title": node.get("page_title", ""),
            "path": node.get("path", []),
            "source_type": node.get("source_type", ""),
        }

    def _evidence_from_chunk(self, chunk: Dict[str, Any], owner: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "chunk_id": chunk.get("id"),
            "owner_node_id": owner.get("owner_node_id", ""),
            "owner_node_name": owner.get("owner_node_name", ""),
            "owner_node_type": owner.get("owner_node_type", ""),
            "page_id": chunk.get("page_id", ""),
            "page_title": chunk.get("page_title", ""),
            "path": chunk.get("path", []),
            "source_url": chunk.get("url", ""),
            "source_type": chunk.get("source_type", ""),
            "text": chunk.get("text", ""),
            "score": chunk.get("score"),
        }

    def _format_fetch_result(self, requested_id: str, resolved_id: str, chunks: List[Dict[str, Any]]) -> str:
        prefix = ""
        if requested_id != resolved_id:
            prefix = f"Resolved requested node_id {requested_id!r} to {resolved_id!r}.\n\n"
        if not chunks:
            return prefix + f"No chunks found under node_id {resolved_id!r}. Try a child section node or larger fetch_depth."
        return prefix + format_chunks_for_llm(chunks)


def format_chunks_for_llm(chunks: Iterable[Dict[str, Any]]) -> str:
    blocks = []
    for chunk in chunks:
        path = " > ".join(chunk.get("path", []))
        blocks.append(
            "\n".join(
                [
                    f"[{chunk.get('chunk_id')}]",
                    f"Page: {chunk.get('page_title', '')}",
                    f"Path: {path}",
                    "Text:",
                    str(chunk.get("text", "")),
                ]
            )
        )
    return "\n\n---\n\n".join(blocks)

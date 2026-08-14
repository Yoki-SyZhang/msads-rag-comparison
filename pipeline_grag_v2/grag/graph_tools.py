"""ID-based graph navigation; short references are assigned by the agent layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .index_io import manifest, read_json


CONTENT_RELATION = "HAS_CONTENT"
INDEX_CHUNK_TYPES = {
    "page_index",
    "accordion_index",
    "progression_tab_index",
    "quarter_index",
    "course_group_index",
}
WIDE_BRANCH_MIN_CHILDREN = 4  # more than 3 structural children
WIDE_BRANCH_MAX_CHILD_CONTENT = 5  # each child's own descendant content_count must stay small


class GraphNavigator:
    def __init__(self, index_dir: Path | str, payload: Optional[Dict[str, Any]] = None) -> None:
        if payload is None:
            paths = manifest(Path(index_dir))
            payload = {
                "graph": read_json(paths["graph"]),
                "chunks": read_json(paths["chunks"]),
                "page_summaries": read_json(paths["page_summaries"]),
            }
        self.graph = payload["graph"]
        self.chunks = payload["chunks"]
        self.page_summaries = payload.get("page_summaries", [])
        self.nodes = {node["id"]: node for node in self.graph.get("nodes", [])}
        self.chunks_by_id = {chunk["id"]: chunk for chunk in self.chunks}
        self.children: Dict[str, List[Dict[str, str]]] = {}
        self.parents: Dict[str, List[Dict[str, str]]] = {}
        for edge in self.graph.get("edges", []):
            self.children.setdefault(edge["source"], []).append(edge)
            self.parents.setdefault(edge["target"], []).append(edge)
        self._content_count_cache: Dict[str, int] = {}

    def pages(self) -> List[Dict[str, Any]]:
        return list(self.page_summaries)

    def structural_children_edges(self, node_id: str) -> List[Dict[str, str]]:
        """Edges out of node_id that lead to another structural (non-Chunk) node."""
        result = []
        for edge in self.children.get(node_id, []):
            if edge.get("relation") == CONTENT_RELATION:
                continue
            target = edge.get("target", "")
            if target in self.nodes and self.nodes[target].get("type") != "Chunk":
                result.append(edge)
        return result

    def has_structural_children(self, node_id: str) -> bool:
        """True if this node has at least one non-Chunk structural child in the graph.

        Used by fetch_graph_content's scope check: a node exceeding NODE_LIMIT is only
        rejected (redirected to smaller children) if finer-grained children actually
        exist to redirect to; a node with no structural children is, by definition, the
        deepest addressable granularity and must be allowed regardless of size.
        """
        return len(self.structural_children_edges(node_id)) > 0

    def structural_tree(self, page_id: str) -> Dict[str, Any]:
        page = self.nodes.get(page_id)
        if not page or page.get("type") != "Page":
            raise KeyError(f"Unknown page: {page_id}")

        def build(node_id: str) -> Dict[str, Any]:
            node = self.nodes[node_id]
            children = [build(edge["target"]) for edge in self.structural_children_edges(node_id)]
            return {
                "id": node_id,
                "label": node.get("label", ""),
                "type": node.get("type", ""),
                "path": list(node.get("path", [])),
                "content_count": self.descendant_content_count(node_id),
                "has_structural_children": bool(children),
                "is_wide_shallow_branch": self.is_wide_shallow_branch(node_id),
                "children": children,
            }

        return build(page_id)

    def structural_descendants(self, node_id: str) -> List[str]:
        if node_id not in self.nodes or self.nodes[node_id].get("type") == "Chunk":
            raise KeyError(f"Unknown structural node: {node_id}")
        result: List[str] = []
        queue = [node_id]
        seen: Set[str] = set()
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            result.append(current)
            for edge in self.structural_children_edges(current):
                queue.append(edge.get("target", ""))
        return result

    def descendant_content_count(self, node_id: str) -> int:
        if node_id in self._content_count_cache:
            return self._content_count_cache[node_id]
        chunk_ids: Set[str] = set()
        for current in self.structural_descendants(node_id):
            for edge in self.children.get(current, []):
                if edge.get("relation") == CONTENT_RELATION:
                    chunk_ids.add(edge.get("target", ""))
        count = len(chunk_ids)
        self._content_count_cache[node_id] = count
        return count

    def chunks_for_node(self, node_id: str) -> List[Dict[str, Any]]:
        chunk_ids: List[str] = []
        seen: Set[str] = set()
        for current in self.structural_descendants(node_id):
            for edge in self.children.get(current, []):
                if edge.get("relation") != CONTENT_RELATION:
                    continue
                chunk_id = edge.get("target", "")
                if chunk_id and chunk_id not in seen:
                    seen.add(chunk_id)
                    chunk_ids.append(chunk_id)
        return [self.chunks_by_id[cid] for cid in chunk_ids if cid in self.chunks_by_id]

    def index_chunk_text(self, node_id: str, _seen: Optional[Set[str]] = None) -> Optional[str]:
        """The synthetic index/summary chunk (accordion_index, quarter_index, etc.) that
        best summarizes node_id's collapsed content — used by inspect_page's collapsed-
        branch preview line. Tries node_id's own directly-owned index chunk first; if it
        has none but wraps exactly one structural child (e.g. a ProgressionTab wrapping
        a single Accordion), falls through to that child's index chunk instead, since the
        wrapper itself is never where the index chunk is actually mounted. Returns None
        if no index chunk is reachable this way."""
        for edge in self.children.get(node_id, []):
            if edge.get("relation") != CONTENT_RELATION:
                continue
            chunk = self.chunks_by_id.get(edge.get("target", ""))
            if chunk and chunk.get("source_type") in INDEX_CHUNK_TYPES:
                return chunk.get("text", "")
        children = self.structural_children_edges(node_id)
        if len(children) == 1:
            seen = _seen if _seen is not None else set()
            if node_id in seen:
                return None
            seen.add(node_id)
            return self.index_chunk_text(children[0].get("target", ""), seen)
        return None

    def is_wide_shallow_branch(self, node_id: str) -> bool:
        """True if node_id has more than WIDE_BRANCH_MIN_CHILDREN structural children
        that are each individually small (<=WIDE_BRANCH_MAX_CHILD_CONTENT content
        blocks) — e.g. an Accordion with a dozen short AccordionItems. Used to collapse
        such branches in inspect_page and exempt them from fetch_graph_content's scope
        check even though their own total content_count exceeds NODE_LIMIT: the useful
        summary is the node's own index chunk, and any individual child stays small
        enough to fetch on its own if the whole branch's chunks aren't already fetched
        together via this exemption."""
        children = self.structural_children_edges(node_id)
        if len(children) < WIDE_BRANCH_MIN_CHILDREN:
            return False
        return all(
            self.descendant_content_count(edge.get("target", "")) <= WIDE_BRANCH_MAX_CHILD_CONTENT
            for edge in children
        )

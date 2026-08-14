"""Agent tool implementations: wrappers around grag retriever and graph navigator."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from grag.graph_tools import GraphNavigator, format_chunks_for_llm
from grag.retriever import load_index, retrieve_evidence


class AgentTools:
    def __init__(self, index_dir: str = "index") -> None:
        self.index_dir = Path(index_dir)
        self._loaded_index: Optional[Dict[str, Any]] = None
        self._navigator: Optional[GraphNavigator] = None

    @property
    def loaded_index(self) -> Dict[str, Any]:
        if self._loaded_index is None:
            self._loaded_index = load_index(self.index_dir)
        return self._loaded_index

    @property
    def navigator(self) -> GraphNavigator:
        if self._navigator is None:
            self._navigator = GraphNavigator(self.index_dir)
        return self._navigator

    # ------------------------------------------------------------------
    # Tool: hybrid_retrieve
    # ------------------------------------------------------------------

    def hybrid_retrieve(self, query: str, top_k: int = 8) -> Dict[str, Any]:
        """Hybrid vector + BM25 + graph retrieval."""
        results = retrieve_evidence(
            query,
            loaded_index=self.loaded_index,
            top_k=top_k,
        )

        chunks = [
            {
                "chunk_id": r["chunk_id"],
                "owner_node_id": r.get("owner_node_id", ""),
                "owner_node_name": r.get("owner_node_name", ""),
                "page_id": r.get("page_id", ""),
                "page_title": r.get("page_title", ""),
                "path": r.get("path", []),
                "source_url": r.get("source_url", ""),
                "source_type": r.get("source_type", ""),
                "score": r.get("score"),
                "text": r.get("text", ""),
                "retrieval_query": query,
            }
            for r in results
        ]

        llm_visible = format_chunks_for_llm(
            [
                {
                    "chunk_id": c["chunk_id"],
                    "page_title": c["page_title"],
                    "path": c["path"],
                    "text": c["text"][:600] + ("..." if len(c.get("text", "")) > 600 else ""),
                }
                for c in chunks
            ]
        )

        return {
            "ok": True,
            "tool": "hybrid_retrieve",
            "query": query,
            "chunks": chunks,
            "returned_chunk_ids": [c["chunk_id"] for c in chunks],
            "returned_page_ids": list({c["page_id"] for c in chunks if c["page_id"]}),
            "llm_visible_result": llm_visible,
        }

    # ------------------------------------------------------------------
    # Tool: list_page_summaries
    # ------------------------------------------------------------------

    def list_page_summaries(self, query: str = "") -> Dict[str, Any]:
        """Return page summaries for agent page selection (no judge needed)."""
        summaries = self.navigator.list_page_summaries(query=query)
        llm_visible = "\n".join(
            f"- [{s.get('label', '')}] {s.get('page_title', '')} | page_id: {s.get('page_id', '')}\n"
            f"  {s.get('description', '')}\n  URL: {s.get('source_url', '')}"
            for s in summaries
        )
        return {
            "ok": True,
            "tool": "list_page_summaries",
            "summaries": summaries,
            "returned_page_ids": [s.get("page_id", "") for s in summaries],
            "llm_visible_result": llm_visible,
        }

    # ------------------------------------------------------------------
    # Tool: inspect_page
    # ------------------------------------------------------------------

    def inspect_page(self, page_id_or_label_or_title: str) -> Dict[str, Any]:
        """Show full KG structure tree for a page (no chunk text)."""
        result = self.navigator.inspect_page(page_id_or_label_or_title)
        return {
            "ok": result.get("ok", False),
            "tool": "inspect_page",
            "page_id": result.get("page_id", ""),
            "page_title": result.get("page_title", ""),
            "source_url": result.get("source_url", ""),
            "returned_page_ids": [result["page_id"]] if result.get("page_id") else [],
            "llm_visible_result": result.get("llm_visible_result", result.get("error", "")),
            # Store full markdown for judge evidence use
            "_full_markdown": result.get("llm_visible_result", ""),
        }

    # ------------------------------------------------------------------
    # Tool: fetch_node_chunks
    # ------------------------------------------------------------------

    def fetch_node_chunks(self, node_id: str, fetch_depth: int = 1) -> Dict[str, Any]:
        """Fetch chunks from a KG node and its structural descendants."""
        result = self.navigator.fetch_node_chunks(node_id, fetch_depth)
        returned_chunk_ids = [c.get("chunk_id", "") for c in result.get("chunks", [])]
        returned_node_ids = [result.get("resolved_node_id") or node_id]
        return {
            "ok": result.get("ok", False),
            "tool": "fetch_node_chunks",
            "node_id": node_id,
            "resolved_node_id": result.get("resolved_node_id", ""),
            "requested_node_id": result.get("requested_node_id", node_id),
            "fetch_depth": fetch_depth,
            "chunks": result.get("chunks", []),
            "returned_chunk_ids": returned_chunk_ids,
            "returned_node_ids": returned_node_ids,
            "llm_visible_result": result.get("llm_visible_result", result.get("error", "")),
        }

    # ------------------------------------------------------------------
    # Tool: fetch_chunk
    # ------------------------------------------------------------------

    def fetch_chunk(self, chunk_id: str) -> Dict[str, Any]:
        """Fetch a single chunk's full text."""
        result = self.navigator.fetch_chunk(chunk_id)
        chunk = result.get("chunk", {})
        return {
            "ok": result.get("ok", False),
            "tool": "fetch_chunk",
            "chunk_id": chunk_id,
            "chunks": result.get("chunks", []),
            "returned_chunk_ids": [chunk_id] if result.get("ok") else [],
            "llm_visible_result": result.get("llm_visible_result", result.get("error", "")),
        }

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------

    def dispatch(self, action: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch agent decision to the correct tool."""
        if action == "hybrid_retrieve":
            return self.hybrid_retrieve(
                query=args.get("query", ""),
                top_k=int(args.get("top_k", 8)),
            )
        if action == "list_page_summaries":
            return self.list_page_summaries(
                query=args.get("query", ""),
            )
        if action == "inspect_page":
            return self.inspect_page(
                page_id_or_label_or_title=args.get("page_id_or_label_or_title", "")
                or args.get("page_id", "")
                or args.get("label", "")
                or args.get("title", ""),
            )
        if action == "fetch_node_chunks":
            return self.fetch_node_chunks(
                node_id=args.get("node_id", ""),
                fetch_depth=int(args.get("fetch_depth", 1)),
            )
        if action == "fetch_chunk":
            return self.fetch_chunk(chunk_id=args.get("chunk_id", ""))
        return {"ok": False, "tool": action, "error": f"Unknown tool: {action}", "llm_visible_result": ""}

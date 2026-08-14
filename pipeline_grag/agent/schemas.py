"""Data structures for the MSADS RAG agent (no prompts here)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------

@dataclass
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class ChatRequest:
    query: str
    history: List[ChatMessage] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

@dataclass
class EvidenceItem:
    evidence_id: str                          # ev_001, ev_002, …
    source_type: Literal["chunk", "node_structure"]
    page_id: str
    page_title: str
    source_url: str
    text: str
    why_kept: str
    # chunk-only extras
    chunk_id: Optional[str] = None
    owner_node_id: Optional[str] = None
    owner_node_name: Optional[str] = None
    path: Optional[List[str]] = None
    score: Optional[float] = None
    retrieval_query: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "page_id": self.page_id,
            "page_title": self.page_title,
            "source_url": self.source_url,
            "text": self.text,
            "why_kept": self.why_kept,
        }
        if self.source_type == "chunk":
            d.update({
                "chunk_id": self.chunk_id,
                "owner_node_id": self.owner_node_id,
                "owner_node_name": self.owner_node_name,
                "path": self.path or [],
                "score": self.score,
                "retrieval_query": self.retrieval_query,
            })
        return d


# ---------------------------------------------------------------------------
# Tool call record
# ---------------------------------------------------------------------------

@dataclass
class ToolCallRecord:
    step: int
    tool_name: str
    arguments: Dict[str, Any]
    result_summary: str
    returned_chunk_ids: List[str] = field(default_factory=list)
    returned_page_ids: List[str] = field(default_factory=list)
    returned_node_ids: List[str] = field(default_factory=list)
    llm_visible_result: str = ""


# ---------------------------------------------------------------------------
# Agent run memory (request-level, not persisted)
# ---------------------------------------------------------------------------

@dataclass
class AgentMemory:
    run_id: str
    original_query: str
    history: List[ChatMessage]
    rewritten_queries: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    candidate_pages: List[Dict[str, Any]] = field(default_factory=list)
    inspected_pages: List[str] = field(default_factory=list)
    evidence_container: List[EvidenceItem] = field(default_factory=list)
    judge_history: List[Dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    # cache last inspect_page result for judge to reference
    _last_inspect_results: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def next_evidence_id(self) -> str:
        return f"ev_{len(self.evidence_container) + 1:03d}"

    def add_evidence(self, item: EvidenceItem) -> None:
        self.evidence_container.append(item)

    def already_have_chunk(self, chunk_id: str) -> bool:
        return any(e.chunk_id == chunk_id for e in self.evidence_container if e.source_type == "chunk")


# ---------------------------------------------------------------------------
# Final response
# ---------------------------------------------------------------------------

@dataclass
class Citation:
    index: int
    title: str
    source_url: str
    snippet: str


@dataclass
class ChatResponse:
    answer: str
    citations: List[Citation]
    debug: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "citations": [
                {
                    "index": c.index,
                    "title": c.title,
                    "source_url": c.source_url,
                    "snippet": c.snippet,
                }
                for c in self.citations
            ],
            "debug": self.debug,
        }

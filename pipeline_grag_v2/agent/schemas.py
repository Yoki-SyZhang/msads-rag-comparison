"""Request-scoped state and response schemas for the GRAG v2 agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set


@dataclass
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class QueryState:
    original_query: str
    conversation_history: List[ChatMessage] = field(default_factory=list)
    rewritten_queries: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ReferenceEntry:
    ref: str
    kind: Literal["page", "node", "evidence"]
    real_id: str
    label: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceItem:
    evidence_ref: str
    chunk_id: str
    owner_node_id: str
    page_id: str
    page_title: str
    source_url: str
    text: str
    path: List[str]
    visible_type: str
    status: Literal["candidate", "accepted", "discarded"] = "candidate"
    provenance: List[Dict[str, Any]] = field(default_factory=list)
    internal_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_ref,
            "status": self.status,
            "page_title": self.page_title,
            "source_url": self.source_url,
            "text": self.text,
            "path": self.path,
            "section_breadcrumb": " > ".join(self.path),
            "visible_type": self.visible_type,
            "provenance": self.provenance,
            "chunk_id": self.chunk_id,
            "owner_node_id": self.owner_node_id,
            "page_id": self.page_id,
            "internal_metadata": self.internal_metadata,
        }


@dataclass
class EvidenceStore:
    by_ref: Dict[str, EvidenceItem] = field(default_factory=dict)
    ref_by_chunk_id: Dict[str, str] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)

    def register(self, item: EvidenceItem) -> EvidenceItem:
        existing_ref = self.ref_by_chunk_id.get(item.chunk_id)
        if existing_ref:
            existing = self.by_ref[existing_ref]
            for source in item.provenance:
                if source not in existing.provenance:
                    existing.provenance.append(source)
            return existing
        self.by_ref[item.evidence_ref] = item
        self.ref_by_chunk_id[item.chunk_id] = item.evidence_ref
        self.order.append(item.evidence_ref)
        return item

    def get(self, evidence_ref: str) -> Optional[EvidenceItem]:
        return self.by_ref.get(evidence_ref)

    def accepted(self) -> List[EvidenceItem]:
        return [self.by_ref[ref] for ref in self.order if self.by_ref[ref].status == "accepted"]

    def all(self) -> List[EvidenceItem]:
        return [self.by_ref[ref] for ref in self.order]


@dataclass
class Judgment:
    """Populated by the Summarizer workflow step (agent/prompts.py::PROGRESS_SUMMARY_SYSTEM),
    not by the per-node Judge — the per-node Judge only assesses individual fetch batches
    (see EVIDENCE_JUDGE_SYSTEM) and never decides overall sufficiency."""

    sufficient: bool = False
    missing: str = "No evidence has been judged yet."
    comment: str = ""


@dataclass
class ToolCallRecord:
    turn: int
    tool: str
    normalized_arguments: Dict[str, Any]
    resolved_real_ids: List[str]
    canonical_signature: str
    status: str
    cached_result: str
    llm_visible_result: str
    decision_reason: str = ""
    candidate_evidence_refs: List[str] = field(default_factory=list)
    kept_evidence_refs: List[str] = field(default_factory=list)
    node_assessments: List[Dict[str, Any]] = field(default_factory=list)
    debug: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolExecution:
    tool: str
    normalized_arguments: Dict[str, Any]
    resolved_real_ids: List[str]
    canonical_signature: str
    status: str
    llm_visible_result: str
    candidate_evidence_refs: List[str] = field(default_factory=list)
    candidate_groups: Dict[str, List[str]] = field(default_factory=dict)
    fact_producing: bool = False
    executed: bool = True
    is_valid_fetch: bool = False
    debug: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeEvaluation:
    """One entry of the structured node_evaluation_log (see prompts.py::PROGRESS_SUMMARY_SYSTEM
    and agent_loop.py). Keyed two levels deep on AgentContext: page_key -> node_key -> this."""

    node_id: str
    path: List[str]
    eff_evidence: List[Dict[str, str]] = field(default_factory=list)
    comment: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "path": self.path,
            "eff_evidence": self.eff_evidence,
            "comment": self.comment,
        }


@dataclass
class AgentContext:
    query_state: QueryState
    refs: Any
    tool_history: List[ToolCallRecord] = field(default_factory=list)
    evidence: EvidenceStore = field(default_factory=EvidenceStore)
    judgment: Judgment = field(default_factory=Judgment)
    turn: int = 0
    max_turns: int = 9
    stop_reason: str = ""
    # page_key -> node_key -> NodeEvaluation; see NodeEvaluation and §5 of the plan.
    node_evaluation_log: Dict[str, Dict[str, NodeEvaluation]] = field(default_factory=dict)
    # every turn's Summarizer output, oldest first; the last entry mirrors `judgment`.
    progress_summary_history: List[Dict[str, Any]] = field(default_factory=list)
    # cumulative (not consecutive) counts driving the fallback trigger — see agent_loop.py.
    valid_fetch_count: int = 0
    invalid_fetch_count: int = 0
    # N refs ever rejected by fetch_graph_content's scope check (NODE_SCOPE_TOO_BROAD).
    # Recorded so _available_node_refs (prompts.py) can annotate them persistently on
    # every future turn — the rejection message itself is only visible for the one turn
    # it's returned in (decision calls are stateless), so without this a node already
    # known to be a dead end has no way to stay flagged across turns.
    too_broad_node_refs: Set[str] = field(default_factory=set)
    fallback: Dict[str, Any] = field(default_factory=lambda: {"triggered": False, "pages_selected": [], "reason": ""})


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
                    "index": citation.index,
                    "title": citation.title,
                    "source_url": citation.source_url,
                    "snippet": citation.snippet,
                }
                for citation in self.citations
            ],
            "debug": self.debug,
        }

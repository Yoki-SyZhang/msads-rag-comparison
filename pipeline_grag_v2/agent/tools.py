"""The two graph-navigation tools for the v2 main loop, plus the fallback-only
hybrid_retrieve and the setup-only page-summary loader. See PLAN.md §3 for the full
design: inspect_page is cheap and repeatable (no dedup); fetch_graph_content is the
only tool that produces evidence, is deduped by real node_id, and gates its own scope
via NODE_LIMIT rather than the old fixed-pipeline 40-chunk NODE_SCOPE_TOO_BROAD cap.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..grag.graph_tools import GraphNavigator
from ..grag.retriever import load_index, retrieve_evidence
from .schemas import AgentContext, EvidenceItem, ToolExecution


NODE_LIMIT = 12  # shared by inspect_page's outline collapse and fetch_graph_content's
                 # scope check — see PLAN.md §3.2/§3.3 for why these must stay aligned.
PREVIEW_MIN_COUNT = 3  # below this, a collapsed node shows no preview/contains hint
MAX_FETCH_NODE_REFS = 5
INDEX_TYPES = {
    "page_index",
    "accordion_index",
    "progression_tab_index",
    "quarter_index",
    "course_group_index",
}


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip()).lower()


def _signature(tool: str, resolved_ids: Any, other_args: Dict[str, Any]) -> str:
    payload = {
        "tool": tool,
        "resolved_ids": sorted(set(resolved_ids)),
        "args": other_args,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class AgentTools:
    def __init__(
        self,
        index_dir: Path | str,
        *,
        loaded_index: Optional[Dict[str, Any]] = None,
        navigator: Optional[GraphNavigator] = None,
        retriever: Callable[..., List[Dict[str, Any]]] = retrieve_evidence,
    ) -> None:
        self.index_dir = Path(index_dir)
        self._loaded_index = loaded_index
        self._navigator = navigator
        self._retriever = retriever

    @property
    def loaded_index(self) -> Dict[str, Any]:
        if self._loaded_index is None:
            self._loaded_index = load_index(self.index_dir)
        return self._loaded_index

    @property
    def navigator(self) -> GraphNavigator:
        if self._navigator is None:
            self._navigator = GraphNavigator(self.index_dir, payload=self.loaded_index)
        return self._navigator

    # ------------------------------------------------------------------
    # Setup-phase helper (not an LLM-choosable tool): assigns P refs and
    # returns page summaries for the decision agent's long-term-memory
    # system-prompt block. Called once at the start of agent_loop.run().
    # ------------------------------------------------------------------
    def load_page_summaries(self, context: AgentContext) -> List[Dict[str, str]]:
        out = []
        for page in self.navigator.pages():
            page_ref = context.refs.assign(
                "page", page["page_id"], page.get("page_title", ""),
                {"description": page.get("description", "")},
            )
            out.append({
                "page_ref": page_ref,
                "page_title": page.get("page_title", ""),
                "description": page.get("description", ""),
            })
        return out

    # ------------------------------------------------------------------
    # Main-loop tool dispatch: only inspect_page and fetch_graph_content.
    # hybrid_retrieve is intentionally NOT reachable through dispatch() —
    # it is only ever invoked directly by agent_loop.py's fallback phase.
    # ------------------------------------------------------------------
    def dispatch(self, action: str, args: Dict[str, Any], context: AgentContext) -> ToolExecution:
        if action == "inspect_page":
            return self.inspect_page(str(args.get("page_ref", "")), context)
        if action == "fetch_graph_content":
            refs = args.get("node_refs", [])
            if isinstance(refs, str):
                refs = [refs]
            return self.fetch_graph_content(list(refs), context)
        return ToolExecution(
            tool=action,
            normalized_arguments=args,
            resolved_real_ids=[],
            canonical_signature=_signature(action, [], {}),
            status="INVALID_TOOL",
            llm_visible_result=f"INVALID_TOOL: {action}. Choose inspect_page or fetch_graph_content.",
            executed=False,
        )

    # ------------------------------------------------------------------
    # inspect_page — always re-executes (no dedup); see PLAN.md §3.2.
    # ------------------------------------------------------------------
    def inspect_page(self, page_ref: str, context: AgentContext) -> ToolExecution:
        raw_ref = page_ref.strip().upper()
        entry = context.refs.resolve(raw_ref, "page")
        if entry is None:
            sig = _signature("inspect_page", [], {"page_ref": raw_ref})
            return ToolExecution(
                tool="inspect_page",
                normalized_arguments={"page_ref": raw_ref},
                resolved_real_ids=[],
                canonical_signature=sig,
                status="INVALID_REFERENCE",
                llm_visible_result="INVALID_REFERENCE: inspect_page requires an available P ref from the page list.",
                executed=False,
            )
        tree = self.navigator.structural_tree(entry.real_id)
        visible = self._render_page(entry.ref, tree, context)
        sig = _signature("inspect_page", [entry.real_id], {"turn_local": True})
        return ToolExecution(
            tool="inspect_page",
            normalized_arguments={"page_ref": entry.ref},
            resolved_real_ids=[entry.real_id],
            canonical_signature=sig,
            status="OK",
            llm_visible_result=visible,
        )

    def _render_page(self, page_ref: str, tree: Dict[str, Any], context: AgentContext) -> str:
        lines = [f"[{page_ref}] {tree.get('label', '')}"]
        for child in tree.get("children", []):
            lines.extend(self._render_node(child, context, page_ref, depth=0, parent_ref=None, prefix=""))
        return "\n".join(lines)

    def _render_node(
        self, node: Dict[str, Any], context: AgentContext, page_ref: str,
        depth: int, parent_ref: Optional[str], prefix: str,
    ) -> List[str]:
        node_ref = context.refs.assign(
            "node", node["id"], node.get("label", ""),
            {
                "content_count": node.get("content_count", 0),
                "page_ref": page_ref,
                "node_type": node.get("type", ""),
                "path": node.get("path", []),
                "has_structural_children": bool(node.get("children")),
                "is_wide_shallow_branch": bool(node.get("is_wide_shallow_branch")),
            },
        )
        if parent_ref:
            context.refs.add_child(parent_ref, node_ref)
        count = node.get("content_count", 0)
        unit = "content block" if count == 1 else "content blocks"
        connector = "├── " if prefix else ""
        lines = [f"{prefix}{connector}[{node_ref}] {node.get('label', '')} — {node.get('type', '')} ({count} {unit})"]

        # Collapse rule: page's own direct children (depth 0) always expand fully.
        # From depth >= 1 onward, a node whose own descendant content_count is already
        # within NODE_LIMIT is shown collapsed (no further N refs assigned below it) —
        # the same threshold fetch_graph_content uses, so "shown collapsed" and
        # "safe to fetch in one call" are always the same fact. See PLAN.md §3.2/§3.3.
        # A "wide-shallow branch" (many small children, e.g. an Accordion with a dozen
        # short AccordionItems) collapses the same way even though its own total
        # content_count exceeds NODE_LIMIT — enumerating every child is noise when the
        # node's own index chunk already names them, and fetch_graph_content grants the
        # matching exemption so the whole branch stays fetchable in one call regardless.
        if depth >= 1 and (count <= NODE_LIMIT or node.get("is_wide_shallow_branch")):
            if count >= PREVIEW_MIN_COUNT:
                preview = self.navigator.index_chunk_text(node["id"])
                child_labels = [c.get("label", "") for c in node.get("children", [])]
                if preview:
                    lines.append(f"{prefix}    preview: {preview}")
                elif child_labels:
                    lines.append(f"{prefix}    contains: {', '.join(child_labels)}")
            return lines

        child_prefix = prefix + "    "
        for child in node.get("children", []):
            lines.extend(self._render_node(child, context, page_ref, depth + 1, node_ref, child_prefix))
        return lines

    # ------------------------------------------------------------------
    # fetch_graph_content
    # ------------------------------------------------------------------
    def fetch_graph_content(self, node_refs: List[str], context: AgentContext) -> ToolExecution:
        raw_refs = list(dict.fromkeys(str(ref).strip().upper() for ref in node_refs if str(ref).strip()))
        if not 1 <= len(raw_refs) <= MAX_FETCH_NODE_REFS:
            sig = _signature("fetch_graph_content", [], {"raw_refs": raw_refs})
            return ToolExecution(
                tool="fetch_graph_content",
                normalized_arguments={"node_refs": raw_refs},
                resolved_real_ids=[],
                canonical_signature=sig,
                status="INVALID_ARGUMENTS",
                llm_visible_result=f"INVALID_ARGUMENTS: provide 1-{MAX_FETCH_NODE_REFS} available N refs; P refs are not allowed.",
                fact_producing=True,
                executed=False,
            )

        entries = [context.refs.resolve(ref, "node") for ref in raw_refs]
        if any(entry is None for entry in entries):
            sig = _signature("fetch_graph_content", [], {"raw_refs": raw_refs})
            return ToolExecution(
                tool="fetch_graph_content",
                normalized_arguments={"node_refs": raw_refs},
                resolved_real_ids=[],
                canonical_signature=sig,
                status="INVALID_REFERENCE",
                llm_visible_result="INVALID_REFERENCE: fetch_graph_content accepts only available N refs from inspect_page, never P refs.",
                fact_producing=True,
                executed=False,
            )

        # From here on, refer to requested nodes by their canonical ref (e.g. "N12"),
        # never the raw/decorated text the LLM typed — this is what gets shown back to
        # the LLM in headings and history lines, and what normalized_arguments records.
        normalized_refs = [entry.ref for entry in entries]

        # Scope check: every requested node must be either small enough to fetch
        # safely (<=NODE_LIMIT), have no structural children to redirect to at all (a
        # genuine graph leaf), or be a wide-shallow branch. See PLAN.md §3.3. An
        # offender no longer blocks the rest of the batch: it's reported inline, but
        # any other admissible node in the same call still gets fetched. This matters
        # because the rejection message suggests a child ref (often a wide-shallow-
        # branch node) that the agent then naturally re-requests alongside the still-
        # too-broad parent — without this, that admissible child would be silently
        # dropped and the call would look identical to a full rejection.
        offenders: List[Tuple[str, Any, int]] = []
        admissible: List[Tuple[str, Any]] = []
        for ref, entry in zip(normalized_refs, entries):
            ok, total = self._validate_node_scope(entry.real_id)
            if ok:
                admissible.append((ref, entry))
            else:
                offenders.append((ref, entry, total))
                context.too_broad_node_refs.add(ref)

        offender_lines = []
        for ref, entry, total in offenders:
            children = entry.payload.get("child_node_refs", [])
            suggestions = []
            for child_ref in children:
                child_entry = context.refs.resolve(child_ref, "node")
                if child_entry:
                    suggestions.append(f"{child_ref} ({child_entry.payload.get('content_count', 0)})")
            suggestion_text = f" Consider its children instead: {', '.join(suggestions)}." if suggestions else ""
            offender_lines.append(f"{ref} has {total} content blocks — too broad.{suggestion_text}")

        if not admissible:
            sig = _signature("fetch_graph_content", [], {"normalized_refs": normalized_refs, "scope_rejected": True})
            return ToolExecution(
                tool="fetch_graph_content",
                normalized_arguments={"node_refs": normalized_refs},
                resolved_real_ids=[entry.real_id for _, entry, _ in offenders],
                canonical_signature=sig,
                status="NODE_SCOPE_TOO_BROAD",
                llm_visible_result="\n".join(offender_lines),
                fact_producing=True,
                executed=False,
            )

        # Dedup by real node_id: nodes already successfully fetched before return
        # their historical kept-evidence summary instead of being re-fetched/re-judged.
        # Only admissible nodes participate — an offender was never fetched, so its real
        # id must never appear in resolved_real_ids (that would let a later solo request
        # for the same offender falsely short-circuit as ALREADY_CHECKED, borrowing
        # kept-evidence/comment text that actually belongs to a different node in this
        # same batch).
        old: List[Tuple[str, Any]] = []
        new: List[Tuple[str, Any]] = []
        for ref, entry in admissible:
            previous = self._previous_node_fetch(entry.real_id, context)
            (old if previous else new).append((ref, entry if not previous else (entry, previous)))

        history_lines = [
            f"ALREADY_CHECKED [{ref}] — kept evidence: {', '.join(prev.kept_evidence_refs) or 'none'}; "
            f"previous assessment: {self._previous_comment(prev, ref) or 'none'}"
            for ref, (entry, prev) in old
        ]

        real_ids = [entry.real_id for _, entry in admissible]
        sig = _signature("fetch_graph_content", real_ids, {})
        if not new:
            return ToolExecution(
                tool="fetch_graph_content",
                normalized_arguments={"node_refs": normalized_refs},
                resolved_real_ids=real_ids,
                canonical_signature=sig,
                status="ALREADY_CHECKED",
                llm_visible_result="\n".join(offender_lines + history_lines),
                fact_producing=True,
                executed=False,
            )

        visible_blocks = offender_lines + history_lines
        candidate_groups: Dict[str, List[str]] = {}
        candidate_refs: List[str] = []
        for ref, entry in new:
            chunks = self.navigator.chunks_for_node(entry.real_id)
            evidence_refs = [self._register_chunk(chunk, ref, "fetch_graph_content", context) for chunk in chunks]
            candidate_groups[ref] = evidence_refs
            candidate_refs.extend(evidence_refs)
            visible_blocks.append(self._format_fetch_block(ref, entry, chunks, context))

        return ToolExecution(
            tool="fetch_graph_content",
            normalized_arguments={"node_refs": normalized_refs},
            resolved_real_ids=real_ids,
            canonical_signature=sig,
            status="OK_WITH_ALREADY_CHECKED" if old else "OK",
            llm_visible_result="\n\n".join(visible_blocks),
            candidate_evidence_refs=list(dict.fromkeys(candidate_refs)),
            candidate_groups=candidate_groups,
            fact_producing=True,
            is_valid_fetch=True,
        )

    def _validate_node_scope(self, real_node_id: str) -> Tuple[bool, int]:
        total = self.navigator.descendant_content_count(real_node_id)
        if total <= NODE_LIMIT:
            return True, total
        if not self.navigator.has_structural_children(real_node_id):
            return True, total
        if self.navigator.is_wide_shallow_branch(real_node_id):
            return True, total
        return False, total

    @staticmethod
    def _previous_node_fetch(real_node_id: str, context: AgentContext):
        for record in reversed(context.tool_history):
            if record.tool == "fetch_graph_content" and record.status in {"OK", "OK_WITH_ALREADY_CHECKED"} \
               and real_node_id in record.resolved_real_ids:
                return record
        return None

    @staticmethod
    def _previous_comment(record, node_ref: str) -> str:
        for assessment in record.node_assessments:
            if assessment.get("node_ref") == node_ref:
                return assessment.get("comment", "")
        return ""

    def _register_chunk(self, chunk: Dict[str, Any], group_ref: str, tool: str, context: AgentContext) -> str:
        chunk_id = chunk.get("id") or chunk.get("chunk_id", "")
        evidence_ref = context.refs.assign(
            "evidence", chunk_id, " > ".join(chunk.get("path", [])) or chunk.get("page_title", ""),
        )
        item = EvidenceItem(
            evidence_ref=evidence_ref,
            chunk_id=chunk_id,
            owner_node_id=chunk.get("owner_node_id", ""),
            page_id=chunk.get("page_id", ""),
            page_title=chunk.get("page_title", ""),
            source_url=chunk.get("url") or chunk.get("source_url", ""),
            text=chunk.get("text", ""),
            path=list(chunk.get("path", [])),
            visible_type="structure summary" if chunk.get("source_type") in INDEX_TYPES else "text",
            provenance=[{"tool": tool, "group_ref": group_ref}],
            internal_metadata={
                "source_type": chunk.get("source_type", ""),
                "score": chunk.get("score"),
                "score_breakdown": chunk.get("score_breakdown", {}),
            },
        )
        return context.evidence.register(item).evidence_ref

    def _format_fetch_block(self, top_ref: str, entry, chunks: List[Dict[str, Any]], context: AgentContext) -> str:
        """Hierarchical N#/N#.#/E# rendering — full path shown once at the top
        heading only; nested sub-headings show just their own label, per PLAN.md §3.3."""
        top_real_id = entry.real_id
        direct_chunks = []
        chain_chunks: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
        ordered_prefixes: List[Tuple[str, ...]] = []
        seen_prefixes: Set[Tuple[str, ...]] = set()

        def register_prefixes(chain: Tuple[str, ...]) -> None:
            for i in range(1, len(chain) + 1):
                prefix = chain[:i]
                if prefix not in seen_prefixes:
                    seen_prefixes.add(prefix)
                    ordered_prefixes.append(prefix)

        for chunk in chunks:
            owner = chunk.get("owner_node_id", "") or top_real_id
            if owner == top_real_id:
                direct_chunks.append(chunk)
                continue
            chain = tuple(self._owner_chain(top_real_id, owner))
            chain_chunks.setdefault(chain, []).append(chunk)
            register_prefixes(chain)

        numbers: Dict[Tuple[str, ...], str] = {}
        counters: Dict[Tuple[str, ...], int] = {}
        for prefix in ordered_prefixes:
            parent = prefix[:-1]
            counters[parent] = counters.get(parent, 0) + 1
            parent_number = numbers.get(parent, "")
            numbers[prefix] = f"{parent_number}.{counters[parent]}" if parent_number else str(counters[parent])

        path_text = " > ".join(entry.payload.get("path", []) or [entry.label])
        lines = [f"[{top_ref}] {entry.label} — path: {path_text}"]
        for chunk in direct_chunks:
            lines.append(self._format_evidence_line(chunk, context, indent="  "))
        for prefix in ordered_prefixes:
            owner_id = prefix[-1]
            owner_node = self.navigator.nodes.get(owner_id, {})
            indent = "  " * len(prefix)
            lines.append(f"{indent}[{top_ref}.{numbers[prefix]}] {owner_node.get('label', '')}")
            for chunk in chain_chunks.get(prefix, []):
                lines.append(self._format_evidence_line(chunk, context, indent=indent + "  "))
        return "\n".join(lines)

    def _owner_chain(self, top_real_id: str, owner_id: str) -> List[str]:
        if owner_id == top_real_id:
            return []
        parent_of: Dict[str, Optional[str]] = {top_real_id: None}
        queue = [top_real_id]
        while queue:
            current = queue.pop(0)
            for edge in self.navigator.structural_children_edges(current):
                target = edge.get("target", "")
                if target and target not in parent_of:
                    parent_of[target] = current
                    queue.append(target)
        if owner_id not in parent_of:
            return [owner_id]
        chain = []
        node = owner_id
        while node != top_real_id:
            chain.append(node)
            node = parent_of[node]
        return list(reversed(chain))

    def _format_evidence_line(self, chunk: Dict[str, Any], context: AgentContext, indent: str) -> str:
        chunk_id = chunk.get("id") or chunk.get("chunk_id", "")
        entry = context.refs.by_real_id("evidence", chunk_id)
        ref = entry.ref if entry else "?"
        label = " (structure summary)" if chunk.get("source_type") in INDEX_TYPES else ""
        return f"{indent}[{ref}]{label} {chunk.get('text', '')}"

    # ------------------------------------------------------------------
    # Fallback-only tool: never reachable via dispatch(); called directly by
    # agent_loop.py's fallback handler once the main loop trips a boundary
    # condition. Scoped to the decision agent's chosen page_ids.
    # ------------------------------------------------------------------
    def hybrid_retrieve(self, query: str, page_ids: Set[str], context: AgentContext) -> ToolExecution:
        normalized_query = _normalize_query(query)
        if not normalized_query:
            return ToolExecution(
                tool="hybrid_retrieve",
                normalized_arguments={"query": query},
                resolved_real_ids=[],
                canonical_signature=_signature("hybrid_retrieve", [], {"query": ""}),
                status="INVALID_ARGUMENTS",
                llm_visible_result="INVALID_ARGUMENTS: query must be non-empty.",
                fact_producing=True,
                executed=False,
            )
        results = self._retriever(
            query, index_dir=self.index_dir, loaded_index=self.loaded_index,
            top_k=8, page_ids=page_ids,
        )
        evidence_refs = [self._register_chunk(result, "hybrid", "hybrid_retrieve", context) for result in results]
        blocks = [self._format_evidence_line(result, context, indent="") for result in results]
        return ToolExecution(
            tool="hybrid_retrieve",
            normalized_arguments={"query": query, "page_ids": sorted(page_ids)},
            resolved_real_ids=[],
            canonical_signature=_signature("hybrid_retrieve", [], {"query": normalized_query}),
            status="OK",
            llm_visible_result="\n".join(blocks) or "No retrieval results.",
            candidate_evidence_refs=evidence_refs,
            candidate_groups={"hybrid": evidence_refs},
            fact_producing=True,
            is_valid_fetch=True,
        )

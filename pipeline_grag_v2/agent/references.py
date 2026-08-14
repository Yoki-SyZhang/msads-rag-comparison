"""Typed, request-scoped P/N/E short-reference registry.

See PLAN.md §0 ("引用系统") for the full design rationale: this is the sole ref<->real-ID
mapping in the system, created fresh per request, assigned online as tools discover real
IDs (never precomputed offline), and idempotent (the same real_id always maps back to the
same ref within one request).
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

from .schemas import ReferenceEntry


PREFIX = {"page": "P", "node": "N", "evidence": "E"}
SHORT_REF_RE = re.compile(r"(?<![A-Z0-9])([PNE]\d+)(?![A-Z0-9])", re.IGNORECASE)


class ReferenceRegistry:
    def __init__(self) -> None:
        self._entries: Dict[str, ReferenceEntry] = {}
        self._reverse: Dict[Tuple[str, str], str] = {}
        self._counters = {"page": 0, "node": 0, "evidence": 0}

    def assign(
        self,
        kind: str,
        real_id: str,
        label: str,
        payload: Optional[Dict] = None,
    ) -> str:
        key = (kind, real_id)
        if key in self._reverse:
            ref = self._reverse[key]
            if payload:
                self._entries[ref].payload.update(payload)
            return ref
        if kind not in PREFIX:
            raise ValueError(f"Unknown reference kind: {kind}")
        self._counters[kind] += 1
        ref = f"{PREFIX[kind]}{self._counters[kind]}"
        entry = ReferenceEntry(ref=ref, kind=kind, real_id=real_id, label=label, payload=payload or {})
        self._entries[ref] = entry
        self._reverse[key] = ref
        return ref

    def resolve(self, ref: str, expected_kind: Optional[str] = None) -> Optional[ReferenceEntry]:
        canonical = self.canonical_ref(ref, expected_kind)
        entry = self._entries.get(canonical) if canonical else None
        if entry is None or (expected_kind and entry.kind != expected_kind):
            return None
        return entry

    def canonical_ref(self, value: str, expected_kind: Optional[str] = None) -> Optional[str]:
        raw = (value or "").strip().upper()
        if raw in self._entries:
            entry = self._entries[raw]
            return raw if not expected_kind or entry.kind == expected_kind else None
        matches = list(dict.fromkeys(match.upper() for match in SHORT_REF_RE.findall(raw)))
        if expected_kind:
            prefix = PREFIX[expected_kind]
            matches = [match for match in matches if match.startswith(prefix)]
        matches = [match for match in matches if match in self._entries]
        return matches[0] if len(matches) == 1 else None

    def by_real_id(self, kind: str, real_id: str) -> Optional[ReferenceEntry]:
        ref = self._reverse.get((kind, real_id))
        return self._entries.get(ref) if ref else None

    def entries(self, kind: Optional[str] = None) -> List[ReferenceEntry]:
        values: Iterable[ReferenceEntry] = self._entries.values()
        if kind:
            values = (entry for entry in values if entry.kind == kind)
        return list(values)

    def add_child(self, parent_ref: str, child_ref: str) -> None:
        """Record parent->child relationships between N refs as inspect_page walks the
        structural tree. Used by fetch_graph_content's NODE_SCOPE rejection message to
        suggest already-assigned smaller child refs instead of forcing another inspect
        round-trip. Safe against assign()'s per-key payload merge: child_node_refs
        accumulates across repeated inspect_page calls on the same page rather than
        being overwritten, since inspect_page is idempotent by real_id (see §0)."""
        entry = self._entries.get(parent_ref)
        if entry is None:
            return
        children = entry.payload.setdefault("child_node_refs", [])
        if child_ref not in children:
            children.append(child_ref)

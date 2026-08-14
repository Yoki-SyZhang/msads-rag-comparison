"""Conversation-aware query rewrite with a safe original-query fallback."""

from __future__ import annotations

from typing import Any, Dict, List

from .prompts import QUERY_REWRITE_SYSTEM, query_rewrite_user


def rewrite_query(query: str, history: List[Dict[str, Any]], client: Any) -> List[Dict[str, Any]]:
    try:
        result = client.chat_json(QUERY_REWRITE_SYSTEM, query_rewrite_user(query, history))
        rewrites = result.get("rewritten_queries", [])
        valid = [item for item in rewrites if isinstance(item, dict) and str(item.get("query", "")).strip()]
        if valid:
            return valid
    except Exception:
        pass
    return [{"id": "q1", "query": query, "target": "general"}]


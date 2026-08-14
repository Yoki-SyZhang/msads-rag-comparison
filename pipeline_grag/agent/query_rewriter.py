"""Query rewriter: turns the current user question into declarative retrieval queries."""

from __future__ import annotations

from typing import Any, Dict, List

from agent.deepseek_client import DeepSeekClient
from agent.prompts import QUERY_REWRITE_SYSTEM, query_rewrite_user


def rewrite_query(
    current_query: str,
    history: List[Dict[str, Any]],
    client: DeepSeekClient,
) -> List[Dict[str, Any]]:
    """Return a list of rewritten query dicts: [{id, query, target}, ...]."""
    user_msg = query_rewrite_user(current_query, history)
    try:
        result = client.chat_json(QUERY_REWRITE_SYSTEM, user_msg)
        queries = result.get("rewritten_queries", [])
        if not queries:
            raise ValueError("empty rewritten_queries")
        return queries
    except Exception:
        # Fallback: treat the original query as a single retrieval query
        return [{"id": "q1", "query": current_query, "target": "general"}]

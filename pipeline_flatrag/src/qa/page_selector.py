"""
LLM-based page-group selector for the QA pipeline.

Replaces query_router.py (program_type $ne filter) with URL-level $in filtering:
reads page_summaries.json (~14 page groups), asks DeepSeek to select 2-3 most
relevant groups for the query, and returns the matching source_url list for use
as a ChromaDB where filter.

Usage:
    from openai import OpenAI
    from src.qa.page_selector import PageSelector

    client = OpenAI(api_key=..., base_url="https://api.deepseek.com")
    selector = PageSelector()
    labels, urls = selector.select("What are the core courses?", client)
    where = {"source_url": {"$in": urls}}
"""

import json
from pathlib import Path

from openai import OpenAI

_SUMMARIES_PATH = Path(__file__).parent / "page_summaries.json"

_SYSTEM_PROMPT = (
    "You are a routing assistant for a UChicago MSADS program Q&A system. "
    "Given a user query and a list of page groups (each with a label and description), "
    "select the 2-3 page groups most likely to contain the answer. "
    "Return ONLY a JSON array of label strings, e.g. [\"faqs\", \"how_to_apply\"]. "
    "No explanation, no markdown, just the JSON array."
)


class PageSelector:
    def __init__(self, summaries_path: Path = _SUMMARIES_PATH) -> None:
        with open(summaries_path, encoding="utf-8") as f:
            self._summaries: list[dict] = json.load(f)
        self._label_to_urls: dict[str, list[str]] = {
            g["label"]: g["urls"] for g in self._summaries
        }

    def select(
        self,
        query: str,
        client: OpenAI,
        exclude_labels: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Select 2-3 relevant page groups for the query.

        Args:
            query:          The rewritten (declarative) query.
            client:         OpenAI-compatible client pointed at DeepSeek.
            exclude_labels: Labels already tried in previous retrieval attempts.

        Returns:
            (selected_labels, selected_urls) — labels chosen and their flat URL list.
        """
        exclude_labels = exclude_labels or []
        candidates = [g for g in self._summaries if g["label"] not in exclude_labels]

        groups_text = "\n".join(
            f'- "{g["label"]}": {g["description"]}' for g in candidates
        )
        user_message = f"Query: {query}\n\nAvailable page groups:\n{groups_text}"

        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_tokens=60,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()

        try:
            labels: list[str] = json.loads(raw)
            labels = [l for l in labels if l in self._label_to_urls and l not in exclude_labels]
        except (json.JSONDecodeError, TypeError):
            labels = []

        if not labels:
            labels = [candidates[0]["label"]] if candidates else []

        urls = self.urls_for_labels(labels)
        return labels, urls

    def urls_for_labels(self, labels: list[str]) -> list[str]:
        """Expand a list of labels into a flat URL list."""
        urls: list[str] = []
        for label in labels:
            urls.extend(self._label_to_urls.get(label, []))
        return urls

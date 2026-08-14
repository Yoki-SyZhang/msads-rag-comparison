"""Final answer generation from accepted evidence only."""

from __future__ import annotations

import re
from typing import Any, List

from .prompts import ANSWER_GENERATION_SYSTEM, answer_generation_user
from .schemas import AgentContext, ChatResponse, Citation


def generate(context: AgentContext, client: Any) -> ChatResponse:
    evidence = context.evidence.accepted()
    # No Python-level short-circuit on empty evidence: distinguishing "off-topic
    # question" from "in-scope but uncovered by the corpus" needs the LLM's judgment
    # (see ANSWER_GENERATION_SYSTEM's NO EVIDENCE rule) — there's no cheaper signal
    # already computed anywhere upstream that reliably tells the two apart.
    try:
        answer = client.chat_text(
            ANSWER_GENERATION_SYSTEM,
            answer_generation_user(context),
        )
    except Exception as exc:
        answer = f"Answer generation failed after evidence collection: {exc}"

    cited = sorted({int(value) for value in re.findall(r"\[(\d+)\]", answer)})
    citations: List[Citation] = []
    for index in cited:
        if not 1 <= index <= len(evidence):
            continue
        item = evidence[index - 1]
        citations.append(
            Citation(
                index=index,
                title=item.page_title,
                source_url=item.source_url,
                snippet=" ".join(item.text.split())[:180],
            )
        )
    return ChatResponse(
        answer=answer,
        citations=citations,
        debug={
            "tool_turns": context.turn,
            "stop_reason": context.stop_reason,
            "evidence_count": len(evidence),
            "sufficient": context.judgment.sufficient,
            "missing": context.judgment.missing,
        },
    )


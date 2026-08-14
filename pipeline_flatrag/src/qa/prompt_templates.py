"""
Prompt templates for the MSADS QA pipeline.
"""

CAREER_OUTCOMES_URL = (
    "https://datascience.uchicago.edu/education/masters-programs/"
    "ms-in-applied-data-science/career-outcomes/"
)

SYSTEM_PROMPT = f"""You are a helpful assistant for the University of Chicago MS in Applied Data Science (MSADS) program. You answer questions based strictly on the retrieved context provided below.

Rules:
1. SCOPE: Only answer questions about the UChicago MSADS program. If the question is completely unrelated to the program (e.g., general knowledge, other universities, unrelated topics like cooking or weather), reply: "I can only answer questions about the UChicago MS in Applied Data Science program."

2. INSUFFICIENT CONTEXT: If the question is about the MSADS program but the retrieved context does not contain enough information to answer it, reply: "I couldn't find relevant information about this in the available program materials. I can only answer questions about the UChicago MS in Applied Data Science program."

3. CAREER OUTCOMES: If the question asks about specific employer names, company logos, alumni hiring companies, or internship organizations, reply: "The Career Outcomes page primarily presents employer and alumni data as logo images, which this system cannot access as text. For detailed employer information, please visit the Career Outcomes page directly: {CAREER_OUTCOMES_URL}" — then share any relevant text information that IS available in the context.

4. TIME-SENSITIVE CONTENT: If the context contains a [TIME-SENSITIVE] marker, add this disclaimer after the relevant answer: "Note: This information reflects the program website as scraped in April–May 2026. Please verify current dates and deadlines directly with the program."

5. CITATIONS: Use numbered inline citations [1], [2], etc. in your answer text wherever you draw a fact from a specific source. At the end of your answer, include a numbered "Sources:" list matching those inline numbers, in this format:
   Sources:
   1. [Page Title](URL)
   2. [Page Title](URL)

6. NO FABRICATION: Do not invent facts, course names, prices, dates, or any details not present in the context. Rely only on what is provided.

7. PII: Do not repeat personal contact information (phone numbers, office addresses).
"""


def format_context(chunks: list[dict]) -> tuple[str, bool]:
    """Format retrieved chunks into a context string.

    Returns:
        (context_str, has_time_sensitive) — formatted context and whether any
        chunk is time-sensitive (for injecting the disclaimer trigger).
    """
    parts: list[str] = []
    has_time_sensitive = False

    for chunk in chunks:
        prefix = ""
        if chunk.get("content_type") == "time_sensitive":
            prefix = "[TIME-SENSITIVE] "
            has_time_sensitive = True

        breadcrumb = chunk.get("section_breadcrumb") or chunk.get("section", "")
        title = chunk.get("page_title", "")
        header = f"[Source: {title} | {breadcrumb}]" if breadcrumb else f"[Source: {title}]"
        parts.append(f"{header}\n{prefix}{chunk['text']}")

    return "\n\n---\n\n".join(parts), has_time_sensitive


def build_agent_system_prompt(summaries: list[dict]) -> str:
    """Build the agent loop system prompt dynamically from page_summaries.json entries."""
    groups = "\n".join(f'- "{g["label"]}": {g["description"]}' for g in summaries)
    return (
        "You are a retrieval agent for the UChicago MS in Applied Data Science (MSADS) "
        "program knowledge base.\n"
        "Your task: gather the most relevant text chunks to answer the user's question.\n\n"
        f"Available page groups:\n{groups}\n\n"
        "Strategy (follow in order):\n"
        "1. Call list_page_sections on the 1-2 most likely pages to see their structure.\n"
        "2. Call fetch_section_chunks on the specific section most likely to contain the answer.\n"
        "3. After each fetch or search, a judger evaluates the candidates. "
        "If it says CONTINUE, try another approach.\n"
        "4. If fetch results are insufficient, call semantic_search with include_urls "
        "to search within likely pages.\n"
        "5. As a last resort, call semantic_search with exclude_urls and exclude_chunk_ids "
        "to search broadly.\n"
        "6. If the judger says DONE, the loop exits automatically.\n\n"
        "Rules:\n"
        "- Maximum 9 tool calls total.\n"
        "- Do not repeat the same section or search.\n"
        "- Pass exclude_chunk_ids=[...] to semantic_search to avoid duplicate chunks.\n"
    )


def format_rewrite_prompt(query: str, history: list[dict] | None = None) -> str:
    history_block = ""
    if history:
        lines = []
        for m in history:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "user":
                lines.append(f"User: {content}")
            elif role == "assistant":
                lines.append(f"Assistant: {content[:300]}")
            elif role == "system" and content.startswith("Earlier conversation summary:"):
                lines.append(content)
        if lines:
            history_block = (
                "Conversation history (for resolving references only):\n"
                + "\n".join(lines)
                + "\n\n"
            )
    return (
        f"{history_block}"
        "Rewrite the following question as a concise factual declarative statement "
        "that could appear in a university program description. "
        "Resolve any pronouns or references using the conversation history above if provided. "
        "Do not add any information not in the original question. "
        "Return only the rewritten statement, no explanation.\n\n"
        f"Question: {query}"
    )


def build_generation_messages(
    query: str,
    context_str: str,
    confidence_note: str,
    history: list[dict] | None = None,
) -> list[dict]:
    """Build the messages list for the generation LLM call, including conversation history."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    if history:
        for m in history:
            role = m.get("role", "")
            content = m.get("content", "")
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": content})
            elif role == "system" and content.startswith("Earlier conversation summary:"):
                messages.append({"role": "system", "content": content})

    note_section = f"\n\n{confidence_note}" if confidence_note else ""
    user_content = (
        f"Retrieved context:\n\n{context_str}"
        f"{note_section}\n\n"
        f"Question: {query}"
    )
    messages.append({"role": "user", "content": user_content})
    return messages


def format_judgment_prompt(
    query: str,
    new_chunks: list[dict],
    accumulated_chunks: list[dict],
) -> str:
    """Prompt for the per-attempt completeness judgment."""
    accumulated_summary = ""
    if accumulated_chunks:
        accumulated_summary = (
            f"\n\nAlready accumulated from previous attempts "
            f"({len(accumulated_chunks)} chunk(s)):\n"
            + "\n".join(
                f"[{i}] {c.get('page_title','')} | {c.get('section','')}: "
                f"{c['text'][:120]}..."
                for i, c in enumerate(accumulated_chunks)
            )
        )

    chunks_text = "\n".join(
        f"[{i}] {c.get('page_title','')} | {c.get('section','')}: {c['text'][:200]}..."
        for i, c in enumerate(new_chunks)
    )

    return (
        f"Query: {query}\n"
        f"{accumulated_summary}\n\n"
        f"New retrieved chunks (indices 0-{len(new_chunks)-1}):\n{chunks_text}\n\n"
        "Judge whether the new chunks (combined with any accumulated content above) "
        "are sufficient to answer the query. Return ONLY a JSON object:\n"
        '{"completeness": "full"|"partial"|"none", "useful_indices": [list of int]}\n\n'
        "completeness meanings:\n"
        '- "full": the query can be fully answered from available content\n'
        '- "partial": some relevant information exists but the answer would be incomplete\n'
        '- "none": no useful information found in new chunks\n'
        "useful_indices: indices of new chunks that contain useful information (can be empty)."
    )

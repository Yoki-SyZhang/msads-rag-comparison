"""
LLM-based query router for program_type scoping.

Classifies a user query into one of three program-type labels and returns
the corresponding ChromaDB where filter (or None for general queries).

Usage (called from src/qa/ at Stage 3):
    from openai import OpenAI
    from src.retrieval.query_router import route_query

    client = OpenAI(api_key=..., base_url="https://api.deepseek.com")
    where  = route_query("What core courses are in the in-person program?", client)
    # → {"program_type": {"$ne": "online"}}
    chunks = retriever.retrieve(query, top_k=5, where=where)

Filter logic ($ne rather than $eq):
  in_person → exclude online chunks, keep in_person + general
  online    → exclude in_person chunks, keep online + general
  general   → no filter (all chunks visible, including general cross-program info)
"""

from openai import OpenAI

_SYSTEM_PROMPT = (
    "You classify user queries about a university MS in Applied Data Science program. "
    "Reply with ONLY one word — no punctuation, no explanation:\n"
    "- 'in_person' — query specifically asks about the in-person, campus, Chicago, "
    "full-time, or part-time program\n"
    "- 'online' — query specifically asks about the online or remote program\n"
    "- 'general' — query covers both programs, neither, or no specific program type "
    "is mentioned (including questions about faculty, tuition, admissions, etc.)"
)


def route_query(query: str, client: OpenAI) -> dict | None:
    """Classify query and return a ChromaDB where filter or None.

    Args:
        query:  The user's natural-language question.
        client: An OpenAI-compatible client pointed at DeepSeek (or any LLM).

    Returns:
        {"program_type": {"$ne": "online"}}   if label == "in_person"
        {"program_type": {"$ne": "in_person"}} if label == "online"
        None                                   if label == "general" (no filter)
    """
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": query},
        ],
        max_tokens=5,
        temperature=0,
    )
    label = resp.choices[0].message.content.strip().lower()

    if label == "in_person":
        return {"program_type": {"$ne": "online"}}
    if label == "online":
        return {"program_type": {"$ne": "in_person"}}
    return None

"""
Streamlit chatbot UI for the UChicago MSADS RAG assistant.

Run:
    streamlit run src/ui/app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import base64
import os
import re
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from src.qa.qa_pipeline import build_pipeline

_MSADS_HOME = (
    "https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/"
)
_HISTORY_KEEP_TURNS = 3   # recent full exchanges to keep uncompressed
_COMPRESS_AFTER = 3       # compress once we exceed this many full exchanges


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compress_history(history: list[dict], client: OpenAI) -> list[dict]:
    """Summarize the oldest 3 turns in history and replace them with a
    single system-role summary message."""
    # Separate existing summary (if any) from conversation turns
    summary_msgs = [m for m in history if m.get("role") == "system"]
    turn_msgs = [m for m in history if m.get("role") != "system"]

    # Oldest 3 exchanges = 6 messages
    to_compress = turn_msgs[:6]
    remaining = turn_msgs[6:]

    prior_summary = summary_msgs[0]["content"] if summary_msgs else ""
    dialog_text = "\n".join(
        f"{m['role'].capitalize()}: {m['content'][:300]}" for m in to_compress
    )
    prompt = (
        f"{prior_summary}\n\n" if prior_summary else ""
    ) + (
        f"Summarize the following conversation in 2-3 sentences, "
        f"keeping only the key topics and conclusions:\n\n{dialog_text}"
    )
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0,
        )
        summary_text = resp.choices[0].message.content.strip()
    except Exception:
        summary_text = prior_summary  # fallback: keep old summary unchanged

    new_history: list[dict] = [
        {"role": "system", "content": f"Earlier conversation summary: {summary_text}"}
    ] + remaining
    return new_history


def _maybe_compress(pipeline_history: list[dict], client: OpenAI) -> list[dict]:
    """Compress history when it exceeds COMPRESS_AFTER full exchanges."""
    turn_msgs = [m for m in pipeline_history if m.get("role") != "system"]
    if len(turn_msgs) > _COMPRESS_AFTER * 2:
        return _compress_history(pipeline_history, client)
    return pipeline_history


def _linkify_sources(answer: str, sources: list[dict]) -> str:
    """Replace [N] inline citation markers with markdown hyperlinks.

    The LLM outputs numbered inline citations like [1], [2] and a numbered
    Sources list at the bottom. We replace [N] in the body text with
    [[N]](url) so Streamlit renders them as clickable superscript links.
    """
    if not sources:
        return answer

    # Build index: citation number → URL (1-based)
    url_map: dict[int, str] = {}
    for i, s in enumerate(sources, start=1):
        url = s.get("source_url", "")
        if url:
            url_map[i] = url

    def _replace(match: re.Match) -> str:
        n = int(match.group(1))
        if n in url_map:
            return f"[[{n}]]({url_map[n]})"
        return match.group(0)

    return re.sub(r"\[(\d+)\]", _replace, answer)


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="UChicago MSADS Assistant",
    page_icon="🎓",
    layout="centered",
)

# ---------------------------------------------------------------------------
# Session state (must initialize before sidebar accesses it)
# ---------------------------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []        # display history (full)

if "pipeline_history" not in st.session_state:
    st.session_state.pipeline_history = []  # compressed history sent to pipeline

if "conversations" not in st.session_state:
    st.session_state.conversations = []   # saved past conversations

# ---------------------------------------------------------------------------
# Sidebar: logo + new conversation
# ---------------------------------------------------------------------------

_LOGO_PATH = Path(__file__).resolve().parents[2] / "docs" / "dsi_logo.svg"
with st.sidebar:
    _logo_b64 = base64.b64encode(_LOGO_PATH.read_bytes()).decode()
    st.markdown(
        f'<img src="data:image/svg+xml;base64,{_logo_b64}" style="width:100%;padding:8px 0">',
        unsafe_allow_html=True,
    )
    st.divider()
    if st.button("➕ New Conversation", use_container_width=True):
        if st.session_state.messages:
            first_user = next(
                (m["content"] for m in st.session_state.messages if m["role"] == "user"),
                "Conversation",
            )
            title = first_user[:40] + ("…" if len(first_user) > 40 else "")
            st.session_state.conversations.append({
                "title": title,
                "messages": list(st.session_state.messages),
                "pipeline_history": list(st.session_state.pipeline_history),
            })
        st.session_state.messages = []
        st.session_state.pipeline_history = []
        st.rerun()

    if st.session_state.conversations:
        st.divider()
        st.markdown("**Past Conversations**")
        for i, conv in enumerate(reversed(st.session_state.conversations)):
            idx = len(st.session_state.conversations) - 1 - i
            if st.button(conv["title"], key=f"conv_{idx}", use_container_width=True):
                if st.session_state.messages:
                    first_user = next(
                        (m["content"] for m in st.session_state.messages if m["role"] == "user"),
                        "Conversation",
                    )
                    cur_title = first_user[:40] + ("…" if len(first_user) > 40 else "")
                    st.session_state.conversations.append({
                        "title": cur_title,
                        "messages": list(st.session_state.messages),
                        "pipeline_history": list(st.session_state.pipeline_history),
                    })
                selected = st.session_state.conversations.pop(idx)
                st.session_state.messages = selected["messages"]
                st.session_state.pipeline_history = selected["pipeline_history"]
                st.rerun()

st.title("🎓 UChicago MSADS Assistant")
st.caption(
    "Ask me anything about the University of Chicago "
    "MS in Applied Data Science program."
)

# ---------------------------------------------------------------------------
# Pipeline (cached across reruns)
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="Loading pipeline...")
def _load_pipeline():
    return build_pipeline()


pipeline = _load_pipeline()

# Grab the underlying OpenAI client for history compression
_llm_client: OpenAI = pipeline._client  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Render existing conversation
# ---------------------------------------------------------------------------

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            st.markdown(msg["content"])
            sources = msg.get("sources", [])
            if sources:
                with st.expander("Sources"):
                    for i, s in enumerate(sources, start=1):
                        title = s.get("page_title") or s.get("source_url", "")
                        url = s.get("source_url", "")
                        if url:
                            st.markdown(f"{i}. [{title}]({url})")
            if msg.get("low_confidence"):
                fallback_url = (
                    sources[0].get("source_url", _MSADS_HOME) if sources else _MSADS_HOME
                )
                st.info(
                    f"This topic may not be fully covered in the available materials. "
                    f"For more details, visit the "
                    f"[MSADS program page]({fallback_url})."
                )
        else:
            st.markdown(msg["content"])

# ---------------------------------------------------------------------------
# Handle new input
# ---------------------------------------------------------------------------

if prompt := st.chat_input("Ask about the UChicago MSADS program..."):
    # Show user message immediately
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Compress history if needed before passing to pipeline
    st.session_state.pipeline_history = _maybe_compress(
        st.session_state.pipeline_history, _llm_client
    )

    # Run pipeline with live status + streaming generation
    with st.chat_message("assistant"):
        status_placeholder = st.empty()
        answer_placeholder = st.empty()

        full_answer_text = ""
        result_meta: dict = {}

        with st.status("Searching for information...", expanded=False) as status_box:

            def _on_progress(msg: str) -> None:
                status_box.update(label=msg, state="running")

            # Run pipeline (stream=True so generation is deferred)
            result = pipeline.run(
                prompt,
                history=st.session_state.pipeline_history,
                stream=True,
                progress_cb=_on_progress,
            )

            status_box.update(label="Streaming answer...", state="running")

        # Stream the generation output
        stream_iter = result["answer"]  # this is the raw streaming iterator
        collected_chunks: list[str] = []

        for chunk in stream_iter:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                collected_chunks.append(delta)
                full_answer_text = "".join(collected_chunks)
                answer_placeholder.markdown(full_answer_text + "▌")

        answer_placeholder.empty()

        sources = result["sources"]
        low_confidence = result.get("low_confidence", False)

        # Linkify inline citations in the final answer
        display_answer = _linkify_sources(full_answer_text, sources)
        st.markdown(display_answer)

        if sources:
            with st.expander("Sources"):
                for i, s in enumerate(sources, start=1):
                    title = s.get("page_title") or s.get("source_url", "")
                    url = s.get("source_url", "")
                    if url:
                        st.markdown(f"{i}. [{title}]({url})")

        if low_confidence:
            fallback_url = (
                sources[0].get("source_url", _MSADS_HOME) if sources else _MSADS_HOME
            )
            st.info(
                f"This topic may not be fully covered in the available materials. "
                f"For more details, visit the "
                f"[MSADS program page]({fallback_url})."
            )

    # Update session state
    st.session_state.messages.append({
        "role": "assistant",
        "content": display_answer,
        "sources": sources,
        "low_confidence": low_confidence,
    })
    st.session_state.pipeline_history.append({"role": "user", "content": prompt})
    st.session_state.pipeline_history.append(
        {"role": "assistant", "content": full_answer_text[:400]}
    )

"""Streamlit UI for side-by-side MSADS RAG pipeline comparison."""

from __future__ import annotations

import json
import queue
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

from adapters.pipeline_flatrag_adapter import FlatRAGAdapter
from adapters.pipeline_grag_v2_adapter import GRAGv2Adapter

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.comparison_lib import _load_questions, _question_meta, _write_run_log

PIPELINE_INFO = {
    "pipeline_flatrag": {
        "title": "FlatRAG",
        "summary": "`pipeline_flatrag`: flat chunks + ChromaDB vector retrieval + DeepSeek agent loop.",
        "tools": [
            "`list_page_sections`: inspect page sections",
            "`fetch_section_chunks`: fetch exact section chunks",
            "`semantic_search`: vector search over chunks",
        ],
    },
    "pipeline_grag_v2": {
        "title": "GRAG v2",
        "summary": "`pipeline_grag_v2`: graph-navigation-first agent (page summaries as long-term memory, "
                   "inspect_page + fetch_graph_content only) with a Judge + Summarizer workflow and a "
                   "bounded scoped hybrid-retrieve fallback.",
        "tools": [
            "`inspect_page`: inspect KG structure (repeatable, content-aware collapsing)",
            "`fetch_graph_content`: fetch 1-5 node subtrees, hierarchically displayed",
            "`hybrid_retrieve`: fallback-only, page-scoped, triggered by bounded conditions",
        ],
    },
}

QUESTION_PREFIXES = {
    "A": "admissions",
    "C": "curriculum",
    "F": "faculty",
    "K": "capstone",
    "T": "tuition",
    "R": "career outcomes",
    "S": "student body",
    "X": "cross-page synthesis",
    "E": "edge cases",
}


def _flat_adapter() -> FlatRAGAdapter:
    return FlatRAGAdapter()


def _grag_v2_adapter() -> GRAGv2Adapter:
    return GRAGv2Adapter()


def _render_pipeline_intro(pipeline_id: str) -> None:
    info = PIPELINE_INFO[pipeline_id]
    st.subheader(info["title"])
    st.markdown(info["summary"])
    st.markdown("**DeepSeek agent tools**")
    for tool in info["tools"]:
        st.markdown(f"- {tool}")


def _render_question_prefix_help() -> None:
    text = " | ".join(f"`{k}` {v}" for k, v in QUESTION_PREFIXES.items())
    st.caption(f"Question IDs: {text}")


def _render_sources(sources: List[Dict[str, Any]]) -> None:
    if not sources:
        st.caption("No sources returned.")
        return
    for idx, source in enumerate(sources, start=1):
        title = source.get("page_title") or source.get("source_url", "")
        url = source.get("source_url", "")
        if url:
            st.markdown(f"{idx}. [{title}]({url})")
        else:
            st.markdown(f"{idx}. {title}")


def _render_steps(steps: List[Dict[str, Any]]) -> None:
    if not steps:
        st.caption("No step trace returned.")
        return
    for idx, step in enumerate(steps[:14], start=1):
        step_no = step.get("step", idx)
        if "tool_name" in step:
            title = f"Agent step {step_no}: {step.get('tool_name', 'tool')}"
        elif "tool_calls" in step:
            names = ", ".join(tc.get("name", "tool") for tc in step.get("tool_calls", []))
            title = f"Agent step {step_no}: {names or 'no tool'}"
        else:
            title = f"Agent step {step_no}: {step.get('label', 'step')}"

        with st.expander(title, expanded=False):
            if "tool_name" in step:
                _render_tool_with_result(
                    1,
                    step.get("tool_name", "tool"),
                    step.get("arguments", {}),
                    step.get("llm_visible_result") or step.get("result_summary") or "",
                )
            elif "tool_calls" in step:
                tool_results = step.get("tool_results", [])
                for call_idx, tc in enumerate(step.get("tool_calls", []), start=1):
                    result = tool_results[call_idx - 1] if call_idx - 1 < len(tool_results) else {}
                    _render_tool_with_result(
                        call_idx,
                        tc.get("name", "tool"),
                        tc.get("args", {}),
                        result.get("content", ""),
                    )
                judger = step.get("judger")
                if judger:
                    if judger.get("node_assessments") is not None:
                        st.caption("judger: " + json.dumps(judger, ensure_ascii=False))
                    else:
                        st.caption(
                            "judger: "
                            + json.dumps(
                                {
                                    "completeness": judger.get("completeness"),
                                    "decision": judger.get("decision"),
                                    "useful_indices": judger.get("useful_indices"),
                                },
                                ensure_ascii=False,
                            )
                        )
            else:
                st.caption(str(step.get("value", "")))
    if len(steps) > 14:
        st.caption(f"{len(steps) - 14} additional steps hidden.")


def _render_tool_with_result(index: int, name: str, args: Dict[str, Any], result_preview: Any) -> None:
    st.markdown(f"Tool {index}: `{name}`")
    st.caption(f"args: {json.dumps(args, ensure_ascii=False)}")
    if result_preview:
        st.caption("result preview")
        preview_text = str(result_preview)
        if name != "list_page_summaries":
            preview_text = preview_text[:1800]
        st.code(preview_text, language="text")


def _render_evidence(evidence: List[Dict[str, Any]]) -> None:
    if not evidence:
        st.caption("No normalized evidence returned.")
        return
    for item in evidence[:10]:
        title = item.get("page_title") or item.get("source_url") or item.get("chunk_id", "Evidence")
        st.markdown(f"- **{title}**")
        path_bits = item.get("path") or []
        breadcrumb = item.get("section_breadcrumb") or " > ".join(path_bits) or item.get("why_kept", "")
        if breadcrumb:
            st.caption(str(breadcrumb))
        st.write((item.get("text", "") or "")[:650])
    if len(evidence) > 10:
        st.caption(f"{len(evidence) - 10} additional evidence items hidden.")


def _render_progress_summary(progress_summary: Dict[str, str]) -> None:
    covering = progress_summary.get("summary_covering", "")
    narrative = progress_summary.get("decision_trace_narrative", "")
    st.markdown("**Coverage (by page/path)**")
    st.write(covering or "_No coverage summary available._")
    st.markdown("**Decision history**")
    st.write(narrative or "_No decision narrative available._")


def _render_result(result: Dict[str, Any]) -> None:
    if result.get("error"):
        st.error(result["error"])
    st.caption(f"{result.get('display_name', '')} | {result.get('time_sec', 0)} sec")
    if result.get("low_confidence"):
        st.warning("Low confidence or incomplete evidence.")
    answer = result.get("answer", "")
    st.markdown(answer if answer else "_No answer returned._")
    with st.expander("Sources", expanded=True):
        _render_sources(result.get("sources", []))
    if result.get("progress_summary"):
        with st.expander("Investigation Summary", expanded=True):
            _render_progress_summary(result["progress_summary"])
    st.markdown("**Retrieval / agent steps**")
    _render_steps(result.get("steps", []))
    with st.expander("Evidence", expanded=True):
        _render_evidence(result.get("evidence", []))
    with st.expander("Raw debug"):
        st.json(result.get("raw_debug", {}))


def _drain_progress(
    progress_queue: "queue.Queue[tuple[str, str]]",
    statuses: Dict[str, List[str]],
) -> None:
    while True:
        try:
            pipeline_id, message = progress_queue.get_nowait()
        except queue.Empty:
            break
        statuses[pipeline_id].append(message)


def _run_compare_with_live_progress(
    question: str,
    status_slots: Dict[str, Any],
    timer_slots: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    adapter_factories = {
        "pipeline_flatrag": _flat_adapter,
        "pipeline_grag_v2": _grag_v2_adapter,
    }
    progress_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
    statuses = {pipeline_id: ["Queued."] for pipeline_id in adapter_factories}
    starts = {pipeline_id: time.perf_counter() for pipeline_id in adapter_factories}

    def _progress(pipeline_id: str):
        def _inner(message: str) -> None:
            progress_queue.put((pipeline_id, message))
        return _inner

    def _run_adapter(factory: Any, pipeline_id: str) -> Dict[str, Any]:
        progress = _progress(pipeline_id)
        try:
            adapter = factory()
            return adapter.run(question, history=[], progress_cb=progress)
        except TypeError as exc:
            # Streamlit can keep an older cached adapter instance after code edits.
            # Fall back to the old signature so the page recovers without requiring
            # a manual cache clear.
            if "progress_cb" not in str(exc) and "positional arguments" not in str(exc):
                raise
            progress("Running with legacy adapter signature...")
            return adapter.run(question, [])
        except Exception as exc:
            return {
                "pipeline_id": pipeline_id,
                "display_name": PIPELINE_INFO[pipeline_id]["title"],
                "answer": "",
                "sources": [],
                "steps": [],
                "evidence": [],
                "time_sec": round(time.perf_counter() - starts[pipeline_id], 3),
                "low_confidence": True,
                "error": str(exc),
                "raw_debug": {},
            }

    results: Dict[str, Dict[str, Any]] = {}
    finished: set[str] = set()
    final_elapsed: Dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(_run_adapter, factory, pipeline_id): pipeline_id
            for pipeline_id, factory in adapter_factories.items()
        }
        pending = set(futures)
        while pending:
            _drain_progress(progress_queue, statuses)
            for pipeline_id in adapter_factories:
                if pipeline_id in finished:
                    elapsed = final_elapsed[pipeline_id]
                else:
                    elapsed = time.perf_counter() - starts[pipeline_id]
                latest = statuses[pipeline_id][-1]
                timer_slots[pipeline_id].metric("Elapsed", f"{elapsed:.1f}s")
                status_slots[pipeline_id].info(latest)
            done_now = {future for future in pending if future.done()}
            for future in done_now:
                pipeline_id = futures[future]
                results[pipeline_id] = future.result()
                final_elapsed[pipeline_id] = float(results[pipeline_id].get("time_sec", time.perf_counter() - starts[pipeline_id]))
                statuses[pipeline_id].append("Done.")
                finished.add(pipeline_id)
                pending.remove(future)
            time.sleep(0.25)

        _drain_progress(progress_queue, statuses)
        for pipeline_id, result in results.items():
            timer_slots[pipeline_id].metric("Elapsed", f"{result.get('time_sec', 0):.1f}s")
            if result.get("error"):
                status_slots[pipeline_id].error("Failed.")
            else:
                status_slots[pipeline_id].success("Done.")

    return results


st.set_page_config(page_title="MSADS RAG Comparison", layout="wide")
st.title("MSADS RAG Pipeline Comparison")
st.caption("Both comparison paths use DeepSeek API. The two pipelines keep their original retrieval designs.")

questions = _load_questions()
question_options = [""] + [f"{q.get('id', 'q')}: {q['question']}" for q in questions]
selected = st.selectbox("Evaluation question", question_options, index=0)
_render_question_prefix_help()

selected_record = None
if selected:
    selected_id = selected.split(":", 1)[0]
    selected_record = next((q for q in questions if str(q.get("id")) == selected_id), None)
default_question = selected_record.get("question", "") if selected_record else ""

question = st.text_area("Question", value=default_question, height=90)

with st.expander("Pipeline logic and tools", expanded=False):
    left, right = st.columns(2)
    with left:
        _render_pipeline_intro("pipeline_flatrag")
    with right:
        _render_pipeline_intro("pipeline_grag_v2")

left, right = st.columns(2)
with left:
    st.subheader("FlatRAG")
    flat_timer = st.empty()
    flat_status = st.empty()
with right:
    st.subheader("GRAG v2")
    grag_timer = st.empty()
    grag_status = st.empty()

status_slots = {
    "pipeline_flatrag": flat_status,
    "pipeline_grag_v2": grag_status,
}
timer_slots = {
    "pipeline_flatrag": flat_timer,
    "pipeline_grag_v2": grag_timer,
}

if st.button("Compare", type="primary", disabled=not question.strip()):
    question_text = question.strip()
    # Only attach question_id/question_meta if the text still matches the
    # dropdown selection verbatim; a manually edited/typed question is
    # logged as question_id=None so it isn't mislabeled with stale metadata.
    if selected_record and question_text == selected_record.get("question", "").strip():
        question_id = selected_record.get("id")
        question_meta = _question_meta(selected_record)
    else:
        question_id = None
        question_meta = None

    results = _run_compare_with_live_progress(question_text, status_slots, timer_slots)
    log_path = _write_run_log(
        question_text,
        results,
        question_id=question_id,
        question_meta=question_meta,
    )
    st.success(f"Comparison log written to {log_path}")

    result_left, result_right = st.columns(2)
    with result_left:
        st.subheader("FlatRAG Result")
        _render_result(results.get("pipeline_flatrag", {}))
    with result_right:
        st.subheader("GRAG v2 Result")
        _render_result(results.get("pipeline_grag_v2", {}))

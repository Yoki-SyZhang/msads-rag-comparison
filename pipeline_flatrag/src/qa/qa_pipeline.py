"""
QA pipeline for the MSADS RAG chatbot (Stage 3).

Pipeline steps per query:
  1. Query rewriting  — rephrase question as declarative statement
  2. Agent loop       — up to MAX_STEPS rounds of tool-calling:
       a. Agent picks a tool: list_page_sections / fetch_section_chunks / semantic_search
       b. Tool executes → candidate_chunks (not yet accepted)
       c. Judger evaluates: completeness + useful_indices → decision (done/continue)
       d. useful_indices → promoted to useful_chunks (accumulated across steps)
       e. Exit when judger says done, or max steps reached
  3. Context assembly — format chunks, inject [TIME-SENSITIVE] markers
  4. LLM generation  — answer with citations, grounding rules enforced by system prompt
  5. Write qa_log     — one JSON file per run() call in data/qa_log/
  6. Return structured result dict

Usage:
    import os
    from openai import OpenAI
    from src.qa.qa_pipeline import QAPipeline

    client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"],
                    base_url="https://api.deepseek.com")
    pipeline = QAPipeline(client)
    result = pipeline.run("What are the core courses for in-person students?")
    print(result["answer"])
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from src.retrieval.retriever import Retriever
from src.qa.page_selector import PageSelector
from src.qa.agent_tools import TOOL_SCHEMAS, execute_tool
from src.qa.token_tracking import TrackedClient
from src.qa.prompt_templates import (
    SYSTEM_PROMPT,
    format_context,
    format_rewrite_prompt,
    format_judgment_prompt,
    build_agent_system_prompt,
    build_generation_messages,
)

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_COLLECTION = "msads_bgesmall_size_900"
_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_LLM_MODEL = "deepseek-chat"
_TOP_K = 7
_MAX_STEPS = 9
_MIN_PARTIAL_CHUNKS = 2


class QAPipeline:
    def __init__(self, llm_client: OpenAI) -> None:
        self._client = llm_client if isinstance(llm_client, TrackedClient) else TrackedClient(llm_client)
        self._retriever = Retriever(_COLLECTION, _MODEL_NAME)
        self._selector = PageSelector()
        self._agent_system_prompt = build_agent_system_prompt(self._selector._summaries)
        self._log_dir = _PROJECT_ROOT / "data" / "qa_log"
        self._log_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        query: str,
        history: list[dict] | None = None,
        stream: bool = False,
        progress_cb: "callable | None" = None,
    ) -> dict:
        """Run the full RAG pipeline for a user query.

        Args:
            query:       The user's question.
            history:     Compressed conversation history from the UI
                         (summary + recent turns). Used for coreference
                         resolution in rewrite and for conversational context
                         in generation.
            stream:      If True, _generate() returns a streaming iterator
                         instead of a string. The caller must consume it.
            progress_cb: Optional callable(msg: str). Called at key pipeline
                         steps so the UI can show live status.

        Returns a dict with keys:
            answer       (str | iterator) — generated answer (or stream)
            sources      (list)  — [{"page_title": ..., "source_url": ...}, ...]
            completeness (str)   — "full" | "partial" | "none"
            low_confidence (bool)
            attempts     (int)   — number of agent tool-call steps taken
        """
        def _cb(msg: str) -> None:
            if progress_cb:
                progress_cb(msg)

        self._client.usage.reset()
        all_useful_chunks: list[dict] = []
        all_log_steps: list[dict] = []
        completeness = "none"
        result: dict | None = None
        last_rewritten = ""

        try:
            _cb("Detecting question structure...")
            subquestions = self._detect_subquestions(query)

            for sq in subquestions:
                _cb(f"Rewriting query: \"{sq[:60]}\"...")
                rewritten = self._rewrite_query(sq, history)
                last_rewritten = rewritten

                log_steps: list[dict] = []
                sq_completeness, useful_chunks = self._run_agent_loop(
                    rewritten, sq, log_steps, progress_cb=_cb
                )
                all_log_steps.extend(log_steps)

                if not useful_chunks:
                    useful_chunks = self._retriever.retrieve(rewritten, top_k=_TOP_K)
                    sq_completeness = "none"

                # Merge chunks (deduplicated)
                seen_ids = {c["chunk_id"] for c in all_useful_chunks}
                for c in useful_chunks:
                    if c["chunk_id"] not in seen_ids:
                        all_useful_chunks.append(c)
                        seen_ids.add(c["chunk_id"])

                # Overall completeness: take the best across sub-questions
                if sq_completeness == "full":
                    completeness = "full"
                elif sq_completeness == "partial" and completeness == "none":
                    completeness = "partial"

            low_confidence = completeness != "full"
            if completeness == "full":
                confidence_note = ""
            elif completeness == "partial":
                confidence_note = (
                    "Note: Retrieved information may only partially address the question."
                )
            else:
                confidence_note = (
                    "Note: After multiple retrieval attempts, no strongly relevant content "
                    "was found. This answer may be incomplete or inaccurate."
                )

            context_str, _ = format_context(all_useful_chunks)
            _cb("Generating answer...")
            answer = self._generate(query, context_str, confidence_note, history=history, stream=stream)
            sources = self._dedupe_sources(all_useful_chunks)

            result = {
                "answer": answer,
                "sources": sources,
                "completeness": completeness,
                "low_confidence": low_confidence,
                "attempts": len(all_log_steps),
                "agent_steps": all_log_steps,
                "evidence": all_useful_chunks,
                "usage": self._client.usage.as_dict(),
            }
        finally:
            self._write_log(query, last_rewritten, all_log_steps, all_useful_chunks, result)

        return result

    # ------------------------------------------------------------------
    # Agent loop
    # ------------------------------------------------------------------

    def _run_agent_loop(
        self,
        rewritten: str,
        original_query: str,
        log_steps: list[dict],
        progress_cb: "callable | None" = None,
    ) -> tuple[str, list[dict]]:
        useful_chunks: list[dict] = []
        useful_ids: set[str] = set()
        completeness = "none"

        messages: list[dict] = [
            {"role": "system", "content": self._agent_system_prompt},
            {"role": "user", "content": f"Find relevant information to answer: {rewritten}"},
        ]

        for _step in range(_MAX_STEPS):
            resp = self._client.chat.completions.create(
                model=_LLM_MODEL,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=0,
                max_tokens=400,
            )
            msg = resp.choices[0].message

            # Build a serialisable assistant message dict
            msg_dict: dict = {"role": "assistant"}
            if msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            if msg.content:
                msg_dict["content"] = msg.content
            messages.append(msg_dict)

            if not msg.tool_calls:
                break  # agent chose to stop without calling a tool

            step_log: dict = {
                "step": _step + 1,
                "tool_calls": [],
                "tool_results": [],
                "judger": None,
            }
            candidate_chunks: list[dict] = []
            retrieval_happened = False

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                step_log["tool_calls"].append({"name": name, "args": args})

                if progress_cb:
                    if name == "list_page_sections":
                        progress_cb(f"Browsing page structure... (step {_step + 1}/{_MAX_STEPS})")
                    elif name == "fetch_section_chunks":
                        progress_cb(f"Fetching section content... (step {_step + 1}/{_MAX_STEPS})")
                    elif name == "semantic_search":
                        progress_cb(f"Running semantic search... (step {_step + 1}/{_MAX_STEPS})")

                tool_result, new_candidates = execute_tool(
                    name, args, self._retriever, useful_ids, self._selector._summaries
                )
                candidate_chunks.extend(new_candidates)

                if name in ("fetch_section_chunks", "semantic_search"):
                    retrieval_happened = True

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })
                step_log["tool_results"].append({"name": name, "content": tool_result[:2000]})

            if retrieval_happened and candidate_chunks:
                completeness, useful_indices = self._judge(
                    original_query, candidate_chunks, useful_chunks
                )

                for i in useful_indices:
                    if 0 <= i < len(candidate_chunks):
                        c = candidate_chunks[i]
                        if c["chunk_id"] not in useful_ids:
                            useful_chunks.append(c)
                            useful_ids.add(c["chunk_id"])

                if completeness == "full" or (
                    completeness == "partial" and len(useful_chunks) >= _MIN_PARTIAL_CHUNKS
                ):
                    decision = "done"
                else:
                    decision = "continue"

                step_log["judger"] = {
                    "completeness": completeness,
                    "useful_indices": useful_indices,
                    "decision": decision,
                    "candidate_chunk_count": len(candidate_chunks),
                    "accepted_chunk_count": len(useful_chunks),
                }

                if progress_cb:
                    progress_cb(
                        f"Evaluating results... (step {_step + 1}/{_MAX_STEPS}, "
                        f"completeness: {completeness})"
                    )

                judge_msg = (
                    f"[Judger] completeness={completeness}, useful_indices={useful_indices}. "
                    f"Decision: "
                    + ("DONE — exit retrieval loop." if decision == "done" else "CONTINUE — try another approach.")
                )
                messages.append({"role": "system", "content": judge_msg})

                log_steps.append(step_log)

                if decision == "done":
                    break
            else:
                log_steps.append(step_log)

        return completeness, useful_chunks

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _detect_subquestions(self, query: str) -> list[str]:
        """Detect if the query contains multiple independent questions.

        Returns a list of sub-questions. If the query is a single question,
        returns [query] unchanged.
        """
        prompt = (
            "Does the following user message contain multiple independent questions "
            "that require separate information lookups? "
            "If yes, split them into individual questions. "
            "If no, return the original message as a single item.\n\n"
            "Return ONLY a JSON array of strings, e.g.:\n"
            '["What are the core courses?", "What is the application deadline?"]\n\n'
            f"User message: {query}"
        )
        try:
            resp = self._client.chat.completions.create(
                model=_LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0,
            )
            raw = resp.choices[0].message.content.strip()
            parsed = json.loads(raw)
            if isinstance(parsed, list) and all(isinstance(q, str) for q in parsed) and parsed:
                return parsed
        except (json.JSONDecodeError, Exception):
            pass
        return [query]

    def _rewrite_query(self, query: str, history: list[dict] | None = None) -> str:
        resp = self._client.chat.completions.create(
            model=_LLM_MODEL,
            messages=[{"role": "user", "content": format_rewrite_prompt(query, history)}],
            max_tokens=80,
            temperature=0,
        )
        return resp.choices[0].message.content.strip()

    def _judge(
        self,
        query: str,
        new_chunks: list[dict],
        accumulated: list[dict],
    ) -> tuple[str, list[int]]:
        """Ask LLM whether current candidates (combined with accumulated) answer the query."""
        prompt = format_judgment_prompt(query, new_chunks, accumulated)
        resp = self._client.chat.completions.create(
            model=_LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        try:
            parsed = json.loads(raw)
            completeness = parsed.get("completeness", "none")
            if completeness not in ("full", "partial", "none"):
                completeness = "none"
            useful_indices = [int(i) for i in parsed.get("useful_indices", [])]
        except (json.JSONDecodeError, TypeError, ValueError):
            completeness = "none"
            useful_indices = []
        return completeness, useful_indices

    def _generate(
        self,
        query: str,
        context_str: str,
        confidence_note: str,
        history: list[dict] | None = None,
        stream: bool = False,
    ) -> "str | object":
        """Generate the final answer.

        When stream=False (default), returns the answer string.
        When stream=True, returns the raw streaming iterator from the OpenAI client
        so the caller (UI) can consume tokens incrementally.
        """
        messages = build_generation_messages(query, context_str, confidence_note, history)
        if stream:
            return self._client.chat.completions.create(
                model=_LLM_MODEL,
                messages=messages,
                max_tokens=1024,
                temperature=0.3,
                stream=True,
            )
        resp = self._client.chat.completions.create(
            model=_LLM_MODEL,
            messages=messages,
            max_tokens=1024,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()

    def _write_log(
        self,
        query: str,
        rewritten_query: str,
        log_steps: list[dict],
        useful_chunks: list[dict],
        result: dict | None,
    ) -> None:
        try:
            ts = datetime.now()
            slug = re.sub(r"[^a-z0-9]+", "_", query.lower())[:30].strip("_")
            fname = ts.strftime("%Y%m%d_%H%M%S") + f"_{slug}.json"
            payload = {
                "timestamp": ts.isoformat(),
                "query": query,
                "rewritten_query": rewritten_query,
                "agent_steps": log_steps,
                "useful_chunks": [
                    {
                        "chunk_id": c.get("chunk_id", ""),
                        "source_url": c.get("source_url", ""),
                        "page_title": c.get("page_title", ""),
                        "section": c.get("section", ""),
                        "section_breadcrumb": c.get("section_breadcrumb", ""),
                        "content_type": c.get("content_type", ""),
                        "program_type": c.get("program_type", ""),
                        "distance": c.get("distance"),
                        "text": c.get("text", ""),
                    }
                    for c in useful_chunks
                ],
                "result": {
                    "completeness": result.get("completeness") if result else None,
                    "low_confidence": result.get("low_confidence") if result else None,
                    "attempts": result.get("attempts") if result else None,
                    "answer_snippet": (
                        result["answer"][:200] if result and result.get("answer") else None
                    ),
                },
            }
            (self._log_dir / fname).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass  # log failure must not break the main pipeline

    @staticmethod
    def _dedupe_sources(chunks: list[dict]) -> list[dict]:
        seen: set[str] = set()
        sources: list[dict] = []
        for c in chunks:
            url = c.get("source_url", "")
            if url and url not in seen:
                seen.add(url)
                sources.append({"page_title": c.get("page_title", ""), "source_url": url})
        return sources


def build_pipeline() -> QAPipeline:
    """Convenience factory that reads DEEPSEEK_API_KEY from environment."""
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    return QAPipeline(client)

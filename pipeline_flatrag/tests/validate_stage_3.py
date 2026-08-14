"""
Stage 3 validation: 7 checks for the QA pipeline.

Run:
    conda activate adsp-nlp-backup
    python tests/validate_stage_3.py
"""

import sys
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import os

PASS = "PASS"
FAIL = "FAIL"

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    print(f"  [{status}] {name}" + (f": {detail}" if detail else ""))
    results.append((name, condition, detail))


# ---------------------------------------------------------------------------
# Check 1: import
# ---------------------------------------------------------------------------
print("Check 1: Import QAPipeline")
try:
    from src.qa.qa_pipeline import QAPipeline, build_pipeline
    from src.qa.page_selector import PageSelector
    from src.qa.prompt_templates import SYSTEM_PROMPT, format_context
    check("Import QAPipeline and helpers", True)
except Exception as e:
    check("Import QAPipeline and helpers", False, str(e))
    print("Cannot continue — fix import errors first.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Check 2: PageSelector loads without error
# ---------------------------------------------------------------------------
print("\nCheck 2: PageSelector initialization")
try:
    sel = PageSelector()
    all_urls = []
    for label in sel._label_to_urls:
        all_urls.extend(sel._label_to_urls[label])
    check("PageSelector loads page_summaries.json", True, f"{len(sel._label_to_urls)} groups, {len(all_urls)} URLs")
except Exception as e:
    check("PageSelector loads page_summaries.json", False, str(e))

# ---------------------------------------------------------------------------
# Build pipeline (requires API key)
# ---------------------------------------------------------------------------
print("\nBuilding QAPipeline (requires DEEPSEEK_API_KEY) ...")
api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
if not api_key:
    print("  WARNING: DEEPSEEK_API_KEY not set — skipping LLM checks 3-7")
    print("\n" + "-" * 50)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"Results: {passed}/{len(results)} passed (LLM checks skipped)")
    sys.exit(0)

try:
    pipeline = build_pipeline()
    check("QAPipeline instantiation", True)
except Exception as e:
    check("QAPipeline instantiation", False, str(e))
    sys.exit(1)

# ---------------------------------------------------------------------------
# Check 3: off-topic refusal
# ---------------------------------------------------------------------------
print("\nCheck 3: Off-topic question → refusal")
result = pipeline.run("What is the capital of France?")
answer_lower = result["answer"].lower()
refused = any(phrase in answer_lower for phrase in [
    "only answer questions", "i can only", "unrelated", "not related", "outside"
])
check("Off-topic question refused", refused, repr(result["answer"][:120]))

# ---------------------------------------------------------------------------
# Check 4: Q10 — no-answer edge case (dress code)
# ---------------------------------------------------------------------------
print("\nCheck 4: Q10 — no ground truth (dress code) → refuses or admits insufficient info")
result = pipeline.run("Is there a dress code for the MSADS program?")
answer_lower = result["answer"].lower()
refused_or_insufficient = any(phrase in answer_lower for phrase in [
    "don't have enough", "no information", "not mentioned", "not specified",
    "i cannot", "unable to find", "not available", "insufficient",
    "couldn't find relevant", "could not find relevant",
])
check("Q10 dress code — refuses or admits no info", refused_or_insufficient,
      repr(result["answer"][:120]))

# ---------------------------------------------------------------------------
# Check 5: Q02 — deadline question has time-sensitive disclaimer
# ---------------------------------------------------------------------------
print("\nCheck 5: Q02 — deadline question → time-sensitive disclaimer present")
result = pipeline.run("What are the application deadlines for the MSADS program?")
answer_lower = result["answer"].lower()
has_disclaimer = any(phrase in answer_lower for phrase in [
    "verify", "scraped", "2026", "current", "directly", "check", "note:"
])
check("Q02 deadline answer has time-sensitive disclaimer", has_disclaimer,
      repr(result["answer"][:120]))

# ---------------------------------------------------------------------------
# Check 6: Q04 — capstone question → sources include capstone URL
# ---------------------------------------------------------------------------
print("\nCheck 6: Q04 — capstone question → sources include capstone URL")
result = pipeline.run("What is the capstone project requirement for MSADS?")
source_urls = [s["source_url"] for s in result["sources"]]
has_capstone = any("capstone" in u for u in source_urls)
check("Q04 capstone sources include capstone URL", has_capstone,
      f"sources: {source_urls}")

# ---------------------------------------------------------------------------
# Check 7: Career outcomes → image limitation note + URL
# ---------------------------------------------------------------------------
print("\nCheck 7: Career outcomes question → image disclaimer + career outcomes URL")
result = pipeline.run("Which companies hire MSADS graduates?")
answer_lower = result["answer"].lower()
has_image_note = any(phrase in answer_lower for phrase in [
    "image", "logo", "cannot access", "not accessible", "career-outcomes", "career outcomes"
])
check("Career question contains image-limitation note or URL", has_image_note,
      repr(result["answer"][:150]))

# ---------------------------------------------------------------------------
# Check 8: return value structure
# ---------------------------------------------------------------------------
print("\nCheck 8: Pipeline return value structure")
result = pipeline.run("How much does the MSADS program cost?")
has_answer = isinstance(result.get("answer"), str) and len(result["answer"]) > 0
has_sources = isinstance(result.get("sources"), list)
has_completeness = result.get("completeness") in ("full", "partial", "none")
has_low_confidence = isinstance(result.get("low_confidence"), bool)
has_attempts = isinstance(result.get("attempts"), int)
struct_ok = all([has_answer, has_sources, has_completeness, has_low_confidence, has_attempts])
check("Return dict has correct structure", struct_ok,
      f"completeness={result.get('completeness')}, attempts={result.get('attempts')}, sources={len(result.get('sources',[]))}")

# ---------------------------------------------------------------------------
# Check 9: Multi-turn coreference resolution
#   Turn 1: ask about in-person program core courses
#   Turn 2: "What about the online version?" WITH history
#   Pass condition: answer mentions "online" and is not a scope refusal
# ---------------------------------------------------------------------------
print("\nCheck 9: Multi-turn coreference resolution")
first_query = "What are the core courses for the in-person MSADS program?"
result_t1 = pipeline.run(first_query)
history_t1 = [
    {"role": "user", "content": first_query},
    {"role": "assistant", "content": result_t1["answer"][:400]},
]
result_t2 = pipeline.run("What about the online version?", history=history_t1)
answer_t2_lower = result_t2["answer"].lower()
mentions_online = "online" in answer_t2_lower
not_just_refusal = "i can only answer questions" not in answer_t2_lower
check(
    "Multi-turn followup 'online version?' resolves correctly with history",
    mentions_online and not_just_refusal,
    f"mentions_online={mentions_online}, not_scope_refusal={not_just_refusal}, "
    f"snippet={repr(result_t2['answer'][:120])}",
)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 50)
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"Results: {passed}/{total} passed")
if passed < total:
    print("Failed checks:")
    for name, ok, detail in results:
        if not ok:
            print(f"  - {name}: {detail}")
sys.exit(0 if passed == total else 1)

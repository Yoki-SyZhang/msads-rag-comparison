"""
Stage 4 validation: checks for the Streamlit UI and upgraded pipeline.

Does NOT launch the Streamlit server — validates imports, session-state
helpers, and pipeline interface compatibility.

Run:
    conda activate adsp-nlp-backup
    python tests/validate_stage_4.py
"""

import sys
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import os
import inspect

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    print(f"  [{status}] {name}" + (f": {detail}" if detail else ""))
    results.append((name, condition, detail))


# ---------------------------------------------------------------------------
# Check 1: UI module imports without error
# ---------------------------------------------------------------------------
print("Check 1: UI module can be imported")
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "app",
        Path(__file__).resolve().parents[1] / "src" / "ui" / "app.py",
    )
    # We can't fully execute the module (Streamlit sets_page_config at import),
    # but we can verify the file parses without SyntaxError.
    import ast
    source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "app.py").read_text(encoding="utf-8")
    ast.parse(source)
    check("src/ui/app.py parses without SyntaxError", True)
except SyntaxError as e:
    check("src/ui/app.py parses without SyntaxError", False, str(e))
except Exception as e:
    check("src/ui/app.py parses without SyntaxError", False, str(e))

# ---------------------------------------------------------------------------
# Check 2: pipeline run() signature has history / stream / progress_cb
# ---------------------------------------------------------------------------
print("\nCheck 2: QAPipeline.run() has new parameters")
try:
    from src.qa.qa_pipeline import QAPipeline
    sig = inspect.signature(QAPipeline.run)
    params = list(sig.parameters.keys())
    has_history = "history" in params
    has_stream = "stream" in params
    has_progress_cb = "progress_cb" in params
    check("run() has history parameter", has_history, f"params={params}")
    check("run() has stream parameter", has_stream, f"params={params}")
    check("run() has progress_cb parameter", has_progress_cb, f"params={params}")
except Exception as e:
    check("QAPipeline import", False, str(e))

# ---------------------------------------------------------------------------
# Check 3: _detect_subquestions exists and is callable
# ---------------------------------------------------------------------------
print("\nCheck 3: _detect_subquestions method exists")
try:
    has_detect = callable(getattr(QAPipeline, "_detect_subquestions", None))
    check("_detect_subquestions is callable", has_detect)
except Exception as e:
    check("_detect_subquestions is callable", False, str(e))

# ---------------------------------------------------------------------------
# Check 4: prompt_templates exports build_generation_messages
# ---------------------------------------------------------------------------
print("\nCheck 4: prompt_templates exports build_generation_messages")
try:
    from src.qa.prompt_templates import build_generation_messages, format_rewrite_prompt
    import inspect as _inspect
    rewrite_sig = _inspect.signature(format_rewrite_prompt)
    has_history_param = "history" in rewrite_sig.parameters
    check("build_generation_messages is importable", True)
    check("format_rewrite_prompt accepts history param", has_history_param,
          f"params={list(rewrite_sig.parameters.keys())}")
except Exception as e:
    check("prompt_templates exports", False, str(e))

# ---------------------------------------------------------------------------
# Check 5: build_generation_messages without history matches original structure
# ---------------------------------------------------------------------------
print("\nCheck 5: build_generation_messages(history=None) produces correct structure")
try:
    from src.qa.prompt_templates import SYSTEM_PROMPT
    msgs = build_generation_messages("test query", "context text", "", history=None)
    is_list = isinstance(msgs, list)
    has_system = msgs[0]["role"] == "system" and msgs[0]["content"] == SYSTEM_PROMPT
    last_is_user = msgs[-1]["role"] == "user"
    last_has_query = "test query" in msgs[-1]["content"]
    ok = is_list and has_system and last_is_user and last_has_query
    check("Messages structure correct (no history)", ok,
          f"len={len(msgs)}, first_role={msgs[0]['role']}, last_role={msgs[-1]['role']}")
except Exception as e:
    check("build_generation_messages", False, str(e))

# ---------------------------------------------------------------------------
# Check 6: SYSTEM_PROMPT Rule 5 uses numbered inline citations
# ---------------------------------------------------------------------------
print("\nCheck 6: SYSTEM_PROMPT uses numbered citations format")
try:
    uses_numbered = "1." in SYSTEM_PROMPT and "[1]" in SYSTEM_PROMPT
    check("SYSTEM_PROMPT Rule 5 has inline [1] citation format", uses_numbered,
          f"snippet: {repr(SYSTEM_PROMPT[SYSTEM_PROMPT.find('CITATIONS'):SYSTEM_PROMPT.find('CITATIONS')+200])}")
except Exception as e:
    check("SYSTEM_PROMPT check", False, str(e))

# ---------------------------------------------------------------------------
# Check 7: UI helper functions parse correctly
# ---------------------------------------------------------------------------
print("\nCheck 7: UI helper _linkify_sources and _compress_history importable via exec")
try:
    # Extract only the helper functions via AST — avoids running st.set_page_config
    import ast, types
    source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    # Collect function defs at module level
    func_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    has_linkify = "_linkify_sources" in func_names
    has_compress = "_compress_history" in func_names
    has_maybe_compress = "_maybe_compress" in func_names
    check("_linkify_sources defined in app.py", has_linkify)
    check("_compress_history defined in app.py", has_compress)
    check("_maybe_compress defined in app.py", has_maybe_compress)
except Exception as e:
    check("UI helper functions", False, str(e))

# ---------------------------------------------------------------------------
# Check 8: Live pipeline test with history (requires API key)
# ---------------------------------------------------------------------------
print("\nCheck 8: Live pipeline test with history parameter")
api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
if not api_key:
    print("  [SKIP] DEEPSEEK_API_KEY not set — skipping live pipeline checks")
else:
    try:
        from src.qa.qa_pipeline import build_pipeline
        pipeline = build_pipeline()

        # Test that passing history=[] does not break the pipeline
        result = pipeline.run(
            "What is the MSADS program?",
            history=[],
            stream=False,
            progress_cb=None,
        )
        has_answer = isinstance(result.get("answer"), str) and len(result["answer"]) > 0
        check("run() with history=[] returns valid answer", has_answer,
              f"completeness={result.get('completeness')}, attempts={result.get('attempts')}")

        # Test that progress_cb is called
        cb_calls: list[str] = []
        result2 = pipeline.run(
            "What are the tuition fees?",
            history=None,
            stream=False,
            progress_cb=lambda msg: cb_calls.append(msg),
        )
        check("progress_cb is called during run()", len(cb_calls) > 0,
              f"cb_calls={cb_calls}")

    except Exception as e:
        check("Live pipeline with history", False, str(e))

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

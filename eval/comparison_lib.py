"""Shared helpers for running/logging the FlatRAG vs. GRAG v2 comparison.

Used by both the Streamlit UI (`ui/app.py`) and the batch evaluation
notebook (`eval/reports/comparison_evaluation.ipynb`) so both write the same
`eval/comparison_runs/` log schema instead of maintaining two copies.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = REPO_ROOT / "eval" / "questions.json"
RUNS_DIR = REPO_ROOT / "eval" / "comparison_runs"

SCHEMA_VERSION = 2

QUESTION_META_FIELDS = ("category", "type", "difficulty", "expected_behavior")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower())[:48].strip("_") or "query"


def _load_questions() -> List[Dict[str, Any]]:
    if not QUESTIONS_PATH.exists():
        return []
    try:
        data = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [q for q in data if isinstance(q, dict) and q.get("question")]
    return []


def _question_meta(question_record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Snapshot the classification fields of a questions.json record.

    A snapshot (not a live reference) so that later edits to questions.json
    don't retroactively change what a historical run log says it tested.
    """
    if not question_record:
        return None
    return {field: question_record.get(field) for field in QUESTION_META_FIELDS}


def _write_run_log(
    question: str,
    results: Dict[str, Dict[str, Any]],
    question_id: Optional[str] = None,
    question_meta: Optional[Dict[str, Any]] = None,
    run_batch_id: Optional[str] = None,
) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now()
    path = RUNS_DIR / f"{ts.strftime('%Y%m%d_%H%M%S')}_{_slug(question)}.json"
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": ts.isoformat(),
        "question": question,
        "question_id": question_id,
        "question_meta": question_meta,
        "results": results,
    }
    if run_batch_id is not None:
        payload["run_batch_id"] = run_batch_id
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

"""Minimal DeepSeek JSON/text client used by the free agent loop."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from openai import OpenAI


DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"


def _extract_json(text: str) -> Dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            raise ValueError(f"Model returned non-JSON output: {cleaned[:300]}")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Model JSON response must be an object.")
    return value


class DeepSeekClient:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        load_dotenv(root / ".env")
        load_dotenv(root / "pipeline_grag_v2" / ".env")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            max_retries=0,
        )
        self.call_count = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def _record_usage(self, response: Any) -> None:
        self.call_count += 1
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.prompt_tokens += usage.prompt_tokens or 0
        self.completion_tokens += usage.completion_tokens or 0
        self.total_tokens += usage.total_tokens or 0

    def chat_json(self, system: str, user: str, temperature: float = 0.0, timeout: int = 60) -> Dict[str, Any]:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(system, user),
            temperature=temperature,
            timeout=timeout,
            response_format={"type": "json_object"},
        )
        self._record_usage(response)
        return _extract_json(response.choices[0].message.content or "")

    def chat_text(self, system: str, user: str, temperature: float = 0.2, timeout: int = 90) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(system, user),
            temperature=temperature,
            timeout=timeout,
        )
        self._record_usage(response)
        return (response.choices[0].message.content or "").strip()

    @staticmethod
    def _messages(system: str, user: str) -> List[Dict[str, str]]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        return messages

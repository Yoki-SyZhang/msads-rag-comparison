"""DeepSeek API client for the MSADS GRAG agent.

This replaces the historical Ollama client on the comparison path while
preserving the small chat_json/chat_text interface used by the agent.
"""

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


def _load_env() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    pipeline_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")
    load_dotenv(pipeline_root / ".env")


def _extract_json(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            raise ValueError(f"DeepSeek returned non-JSON output:\n{cleaned[:500]}")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError(f"DeepSeek returned JSON that is not an object:\n{cleaned[:500]}")
    return parsed


class DeepSeekClient:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
    ) -> None:
        _load_env()
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def chat_json(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        messages = self._messages(system, user)
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            timeout=timeout,
            response_format={"type": "json_object"},
        )
        text = resp.choices[0].message.content or ""
        return _extract_json(text)

    def chat_text(
        self,
        system: str,
        user: str,
        temperature: float = 0.3,
        timeout: int = 180,
    ) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(system, user),
            temperature=temperature,
            timeout=timeout,
        )
        return (resp.choices[0].message.content or "").strip()

    def is_available(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _messages(system: str, user: str) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        return messages

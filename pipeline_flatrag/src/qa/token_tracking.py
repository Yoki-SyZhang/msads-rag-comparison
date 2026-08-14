"""Token-usage tracking wrapper for the OpenAI-compatible DeepSeek client.

QAPipeline and its collaborators (page_selector, query_router) all call
``client.chat.completions.create(...)`` directly with no shared client
wrapper. Rather than editing every call site, TrackedClient wraps the real
client once and transparently accumulates ``response.usage`` on every call,
so any code that already does ``client.chat.completions.create(...)`` gets
tracked for free.
"""

from __future__ import annotations

from typing import Any


class TokenAccumulator:
    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.llm_calls = 0

    def record(self, response: Any) -> None:
        self.llm_calls += 1
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.prompt_tokens += usage.prompt_tokens or 0
        self.completion_tokens += usage.completion_tokens or 0
        self.total_tokens += usage.total_tokens or 0

    def reset(self) -> None:
        self.__init__()

    def as_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls": self.llm_calls,
        }


class _TrackedCompletions:
    def __init__(self, real_completions: Any, accumulator: TokenAccumulator) -> None:
        self._completions = real_completions
        self._accumulator = accumulator

    def create(self, *args: Any, **kwargs: Any) -> Any:
        response = self._completions.create(*args, **kwargs)
        self._accumulator.record(response)
        return response


class _TrackedChat:
    def __init__(self, real_chat: Any, accumulator: TokenAccumulator) -> None:
        self.completions = _TrackedCompletions(real_chat.completions, accumulator)


class TrackedClient:
    """Drop-in wrapper around an OpenAI-compatible client that accumulates token usage."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.usage = TokenAccumulator()
        self.chat = _TrackedChat(client.chat, self.usage)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

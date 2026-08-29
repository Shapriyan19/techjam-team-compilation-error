from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

from starter.llm.config import PhaseSixConfig


ORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "order": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Candidate numbers from the list, best match first.",
        }
    },
    "required": ["order"],
    "additionalProperties": False,
}


class LLMUnavailable(RuntimeError):
    """Raised when no model can be reached; the caller must fall back."""


@dataclass(frozen=True)
class RerankRequest:
    system: str
    user_message: str


@dataclass(frozen=True)
class RerankReply:
    order: tuple[int, ...]
    prompt_tokens: int
    completion_tokens: int


class RerankClient(Protocol):
    """Minimal seam so tests and offline runs never touch the network."""

    def rerank(self, request: RerankRequest) -> RerankReply: ...


class AnthropicRerankClient:
    """Official Anthropic SDK adapter for the optional semantic reranker."""

    def __init__(self, config: PhaseSixConfig) -> None:
        self.config = config
        if not os.getenv(config.api_key_variable):
            raise LLMUnavailable(f"{config.api_key_variable} is not set")
        try:
            import anthropic
        except ImportError as exc:
            raise LLMUnavailable("the anthropic package is not installed") from exc
        self._client = anthropic.Anthropic(
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
        )

    def rerank(self, request: RerankRequest) -> RerankReply:
        response = self._client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_output_tokens,
            system=request.system,
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.config.effort,
                "format": {"type": "json_schema", "schema": ORDER_SCHEMA},
            },
            messages=[{"role": "user", "content": request.user_message}],
        )
        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMUnavailable("model declined the reranking request")
        text = next(
            (block.text for block in response.content if getattr(block, "type", "") == "text"),
            "",
        )
        payload = json.loads(text)
        usage = response.usage
        return RerankReply(
            order=tuple(int(value) for value in payload.get("order", [])),
            prompt_tokens=max(int(getattr(usage, "input_tokens", 0) or 0), 0),
            completion_tokens=max(int(getattr(usage, "output_tokens", 0) or 0), 0),
        )

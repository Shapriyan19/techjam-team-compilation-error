from __future__ import annotations

import json
import os
import time

from starter.llm.client import LLMUnavailable, RerankReply, RerankRequest, ORDER_SCHEMA
from starter.llm.config import PhaseSixConfig


class GeminiRerankClient:
    """Google Gemini adapter for the optional semantic reranker.

    Mirrors AnthropicRerankClient's contract exactly (same RerankClient
    protocol, same failure-to-LLMUnavailable mapping) so SemanticReranker's
    orchestration, fallback, and tests do not need to know which provider is
    behind the call.
    """

    def __init__(self, config: PhaseSixConfig) -> None:
        self.config = config
        api_key = os.getenv(config.api_key_variable)
        if not api_key:
            raise LLMUnavailable(f"{config.api_key_variable} is not set")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise LLMUnavailable("the google-genai package is not installed") from exc
        self._types = types
        self._client = genai.Client(api_key=api_key)
        self._last_call_monotonic: float | None = None

    def rerank(self, request: RerankRequest) -> RerankReply:
        types = self._types
        # thinking_budget=0 ("disabled"), omit thinking_config
        # for "low" effort instead and let the model use its own default.
        thinking_budget = {"medium": 512, "high": 2048}.get(self.config.effort)
        config_kwargs = dict(
            system_instruction=request.system,
            max_output_tokens=self.config.max_output_tokens,
            response_mime_type="application/json",
            response_json_schema=ORDER_SCHEMA,
            http_options=types.HttpOptions(timeout=int(self.config.timeout_seconds * 1000)),
        )
        if thinking_budget is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)
        self._respect_pacing()
        try:
            response = self._client.models.generate_content(
                model=self.config.model,
                contents=request.user_message,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:  # network, auth, quota, timeout - caller falls back
            raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc

        text = response.text
        if not text:
            raise LLMUnavailable("model returned no text content")
        payload = json.loads(text)
        usage = response.usage_metadata
        return RerankReply(
            order=tuple(int(value) for value in payload.get("order", [])),
            prompt_tokens=max(int(getattr(usage, "prompt_token_count", 0) or 0), 0),
            completion_tokens=max(int(getattr(usage, "candidates_token_count", 0) or 0), 0),
        )

    def _respect_pacing(self) -> None:
        interval = self.config.min_request_interval_seconds
        if interval <= 0.0 or self._last_call_monotonic is None:
            self._last_call_monotonic = time.monotonic()
            return
        elapsed = time.monotonic() - self._last_call_monotonic
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_call_monotonic = time.monotonic()

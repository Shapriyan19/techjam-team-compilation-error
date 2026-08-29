from __future__ import annotations

import json
import os

from starter.llm.client import LLMUnavailable, RerankReply, RerankRequest
from starter.llm.config import PhaseSixConfig

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


class NvidiaRerankClient:
    """NVIDIA NIM adapter (OpenAI-compatible endpoint) for the optional
    semantic reranker.

    Same RerankClient protocol as the Anthropic/Gemini adapters, so
    SemanticReranker's orchestration, fallback, and tests are unchanged.
    This model line is recent enough that behavior here is verified against
    the live API, not assumed from prior knowledge - see the bisection notes
    in docs/EXPERIMENT_LOG.md before changing the request shape.
    """

    def __init__(self, config: PhaseSixConfig) -> None:
        self.config = config
        api_key = os.getenv(config.api_key_variable)
        if not api_key:
            raise LLMUnavailable(f"{config.api_key_variable} is not set")
        try:
            import openai
        except ImportError as exc:
            raise LLMUnavailable("the openai package is not installed") from exc
        self._client = openai.OpenAI(
            base_url=NVIDIA_BASE_URL,
            api_key=api_key,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
        )

    def rerank(self, request: RerankRequest) -> RerankReply:
        # enable_thinking left off for "low" effort: reasoning tokens on a
        # 550B model are expensive for a task this structured, and the
        # response has to be clean JSON with no interleaved reasoning text.
        enable_thinking = self.config.effort not in ("low",)
        try:
            completion = self._client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user_message},
                ],
                temperature=0.0,
                max_tokens=self.config.max_output_tokens,
                response_format={"type": "json_object"},
                extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
                stream=False,
            )
        except Exception as exc:  # network, auth, quota, timeout - caller falls back
            raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc

        message = completion.choices[0].message
        text = message.content
        if not text:
            raise LLMUnavailable("model returned no text content")
        payload = json.loads(text)
        usage = completion.usage
        return RerankReply(
            order=tuple(int(value) for value in payload.get("order", [])),
            prompt_tokens=max(int(getattr(usage, "prompt_tokens", 0) or 0), 0),
            completion_tokens=max(int(getattr(usage, "completion_tokens", 0) or 0), 0),
        )

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from starter.llm.client import LLMUnavailable, RerankReply, RerankRequest, ORDER_SCHEMA
from starter.llm.config import PhaseSixConfig


# Appended to the system prompt only when the model rejected the JSON schema and
# the request has to fall back to unconstrained JSON mode.
_JSON_MODE_INSTRUCTION = (
    '\nRespond with a single JSON object of the form {"order": [3, 1, 2]} and nothing else.'
)
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
# Reasoning models on NIM (gpt-oss, nemotron) think in `reasoning_content` and
# answer in `content`. Left unconstrained they spend the whole token budget
# thinking and return an empty answer, so the request caps the thinking effort.
# Measured on openai/gpt-oss-120b with a real 40-candidate prompt: unconstrained
# it burned all 2048 tokens on reasoning and returned nothing in 15.1 s, while
# `reasoning_effort=low` answered with valid JSON in 7.6 s using 539 tokens.
REASONING_EFFORT_BY_EFFORT = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}


class _ParameterRejected(RuntimeError):
    """The endpoint refused an optional parameter; retry without that one."""

    def __init__(self, parameter: str, detail: str = "") -> None:
        super().__init__(f"{parameter} rejected: {detail}")
        self.parameter = parameter


class _RetryableError(RuntimeError):
    """Transient transport failure; retried up to `max_retries` times."""


class NvidiaRerankClient:
    """NVIDIA NIM adapter for the optional semantic reranker.

    Talks to the OpenAI-compatible `/chat/completions` endpoint at
    `config.base_url` (the hosted free tier at build.nvidia.com by default, or
    any self-hosted NIM) over stdlib HTTP, so Phase 6 stays dependency-free.
    Mirrors AnthropicRerankClient's contract exactly - same RerankClient
    protocol, same failure-to-LLMUnavailable mapping - so SemanticReranker's
    orchestration, fallback, and tests do not need to know which provider is
    behind the call.
    """

    def __init__(self, config: PhaseSixConfig, transport=None) -> None:
        self.config = config
        api_key = os.getenv(config.api_key_variable)
        if not api_key:
            raise LLMUnavailable(f"{config.api_key_variable} is not set")
        if not config.base_url:
            raise LLMUnavailable("no base URL configured; set TECHJAM_LLM_BASE_URL")
        self._api_key = api_key
        self._endpoint = config.base_url.rstrip("/") + "/chat/completions"
        # Seam for tests: any callable taking the request body and returning the
        # decoded response body, so no test needs the network.
        self._transport = transport or self._post
        self._last_call_monotonic: float | None = None
        # Guided decoding and reasoning_effort are both per-model on NIM; a
        # rejection downgrades the whole client for the rest of the session
        # rather than costing a retry on every turn.
        self._schema_supported = True
        self._reasoning_effort_supported = True

    def rerank(self, request: RerankRequest) -> RerankReply:
        self._respect_pacing()
        # At most two downgrades: one per optional parameter the model refuses.
        for _ in range(3):
            try:
                body = self._send(self._payload(request))
                break
            except _ParameterRejected as exc:
                if exc.parameter == "reasoning_effort" and self._reasoning_effort_supported:
                    self._reasoning_effort_supported = False
                elif exc.parameter == "response_format" and self._schema_supported:
                    self._schema_supported = False
                else:
                    raise LLMUnavailable(str(exc)) from exc
        else:
            raise LLMUnavailable("the endpoint refused every supported request shape")

        choices = body.get("choices") or []
        if not choices:
            raise LLMUnavailable("model returned no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        if message.get("refusal"):
            raise LLMUnavailable("model declined the reranking request")
        text = message.get("content") or ""
        if not text.strip():
            # Reasoning models hit this when thinking consumes max_tokens: the
            # answer never starts. Name the cause so the trace is diagnosable.
            reasoning = len(message.get("reasoning_content") or "")
            if choice.get("finish_reason") == "length":
                raise LLMUnavailable(
                    f"answer truncated: max_tokens spent before content began "
                    f"({reasoning} reasoning chars); raise TECHJAM_LLM_MAX_TOKENS or "
                    f"lower TECHJAM_LLM_EFFORT"
                )
            raise LLMUnavailable("model returned no text content")
        payload = _parse_json_object(text)
        usage = body.get("usage") or {}
        return RerankReply(
            order=tuple(int(value) for value in payload.get("order", [])),
            prompt_tokens=max(int(usage.get("prompt_tokens") or 0), 0),
            completion_tokens=max(int(usage.get("completion_tokens") or 0), 0),
        )

    def _payload(self, request: RerankRequest) -> dict:
        schema = self._schema_supported
        system = request.system if schema else request.system + _JSON_MODE_INSTRUCTION
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": request.user_message},
            ],
            "max_tokens": self.config.max_output_tokens,
            # Reranking must be reproducible across runs of the same shortlist.
            "temperature": 0.0,
            "top_p": 1.0,
            "stream": False,
        }
        if schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "candidate_order", "strict": True, "schema": ORDER_SCHEMA},
            }
        else:
            payload["response_format"] = {"type": "json_object"}
        if self._reasoning_effort_supported:
            payload["reasoning_effort"] = REASONING_EFFORT_BY_EFFORT.get(self.config.effort, "low")
        return payload

    def _send(self, payload: dict) -> dict:
        attempts = self.config.max_retries + 1
        for attempt in range(attempts):
            try:
                return self._transport(payload)
            except _ParameterRejected:
                raise
            except _RetryableError as exc:
                if attempt == attempts - 1:
                    raise LLMUnavailable(str(exc)) from exc
                time.sleep(min(2.0 ** attempt, self.config.timeout_seconds))
            except LLMUnavailable:
                raise
            except Exception as exc:  # malformed body, decode error - caller falls back
                raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
        raise LLMUnavailable("request was not attempted")

    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            # 400 on a guided-decoding request means this model has no schema
            # support; everything else is either transient or terminal.
            if exc.code == 400:
                # NIM names the offending field in the 400 body; prefer dropping
                # reasoning_effort, since guided decoding is worth more here.
                lowered = detail.lower()
                if "reasoning_effort" in lowered and "reasoning_effort" in payload:
                    raise _ParameterRejected("reasoning_effort", detail) from exc
                if (payload.get("response_format") or {}).get("type") == "json_schema":
                    raise _ParameterRejected("response_format", detail) from exc
            if exc.code in _RETRYABLE_STATUS:
                raise _RetryableError(f"HTTP {exc.code}: {detail}") from exc
            raise LLMUnavailable(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:  # DNS, connection reset, timeout
            raise _RetryableError(f"{type(exc).__name__}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise _RetryableError(f"timeout after {self.config.timeout_seconds}s") from exc

    def _respect_pacing(self) -> None:
        interval = self.config.min_request_interval_seconds
        if interval <= 0.0 or self._last_call_monotonic is None:
            self._last_call_monotonic = time.monotonic()
            return
        elapsed = time.monotonic() - self._last_call_monotonic
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_call_monotonic = time.monotonic()


def _parse_json_object(text: str) -> dict:
    """Tolerate code fences and stray prose around the JSON object.

    Guided decoding returns bare JSON, but the json_object fallback path and
    reasoning-style models sometimes wrap it.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise LLMUnavailable("model returned no JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"unparsable JSON response: {exc}") from exc

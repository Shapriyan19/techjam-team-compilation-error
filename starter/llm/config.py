from __future__ import annotations

import os
from dataclasses import dataclass


PHASE6_MODES = frozenset({"off", "shadow", "rerank"})
PHASE6_PROVIDERS = frozenset({"anthropic", "nvidia"})

# Claude Opus 5 is the current default model. The reranking prompt is short and
# highly structured, so the request runs at low effort with adaptive thinking.
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MODEL_BY_PROVIDER = {
    "anthropic": "claude-opus-5",
    # NVIDIA's free hosted tier (build.nvidia.com). Measured 2026-08-29 against
    # the live endpoint: ~1.2 s per reranking call, honours json_schema guided
    # decoding, and returns bare JSON with no reasoning preamble. Note that
    # meta/llama-3.3-70b-instruct reached end of life on 2026-08-26 and now
    # returns HTTP 410, and both deepseek-v4 models hang without responding.
    "nvidia": "openai/gpt-oss-120b",
}
DEFAULT_KEY_VARIABLE_BY_PROVIDER = {
    "anthropic": "ANTHROPIC_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
}
# OpenAI-compatible endpoint root. Point this at a self-hosted NIM to run the
# same client against a local model; the Anthropic route uses its own SDK
# default and ignores the field.
DEFAULT_BASE_URL_BY_PROVIDER = {
    "anthropic": "",
    "nvidia": "https://integrate.api.nvidia.com/v1",
}


@dataclass(frozen=True)
class PhaseSixConfig:
    """Optional small-shortlist LLM reranking. Off unless explicitly enabled."""

    mode: str = "off"
    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    shortlist_size: int = 40
    max_calls_per_session: int = 3
    first_turn: int = 1
    last_turn: int = 8
    timeout_seconds: float = 12.0
    max_retries: int = 1
    max_output_tokens: int = 2048
    effort: str = "low"
    api_key_variable: str = "ANTHROPIC_API_KEY"
    base_url: str = ""
    # Enforced minimum gap between calls, seconds. 0 = no pacing (Anthropic's
    # tier handled our test volume fine). NVIDIA's free hosted tier has a strict
    # requests-per-minute cap, so set this (e.g. 1.5) when a burst of evaluator
    # sessions starts drawing 429s.
    min_request_interval_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.mode not in PHASE6_MODES:
            raise ValueError(f"unsupported Phase 6 mode: {self.mode}")
        if self.provider not in PHASE6_PROVIDERS:
            raise ValueError(f"unsupported Phase 6 provider: {self.provider}")
        if self.provider == "nvidia" and not self.base_url:
            raise ValueError("the nvidia provider needs a base_url")
        if not 2 <= self.shortlist_size <= 100:
            raise ValueError("shortlist_size must be between 2 and 100")
        if self.max_calls_per_session < 1:
            raise ValueError("max_calls_per_session must be positive")
        if self.first_turn < 1 or self.last_turn < self.first_turn:
            raise ValueError("invalid Phase 6 turn window")
        if self.timeout_seconds <= 0.0:
            raise ValueError("timeout_seconds must be positive")
        if self.min_request_interval_seconds < 0.0:
            raise ValueError("min_request_interval_seconds must not be negative")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if self.max_output_tokens < 64:
            raise ValueError("max_output_tokens must be at least 64")
        if self.effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError(f"unsupported effort level: {self.effort}")

    @property
    def calls_model(self) -> bool:
        return self.mode in {"shadow", "rerank"}

    @property
    def applies_ordering(self) -> bool:
        return self.mode == "rerank"

    def allows_turn(self, turn: int) -> bool:
        return self.first_turn <= turn <= self.last_turn

    @classmethod
    def from_environment(cls) -> "PhaseSixConfig":
        # Provider is read first so its own model/key-variable defaults apply
        # unless the caller overrides them explicitly.
        provider = os.getenv("TECHJAM_LLM_PROVIDER", "anthropic").strip().casefold()
        default_model = DEFAULT_MODEL_BY_PROVIDER.get(provider, DEFAULT_MODEL)
        default_key_variable = DEFAULT_KEY_VARIABLE_BY_PROVIDER.get(provider, "ANTHROPIC_API_KEY")
        default_base_url = DEFAULT_BASE_URL_BY_PROVIDER.get(provider, "")
        return cls(
            mode=os.getenv("TECHJAM_PHASE6_MODE", "off").strip().casefold(),
            provider=provider,
            model=os.getenv("TECHJAM_LLM_MODEL", default_model).strip(),
            shortlist_size=_environment_int("TECHJAM_LLM_SHORTLIST", 40),
            max_calls_per_session=_environment_int("TECHJAM_LLM_MAX_CALLS", 3),
            first_turn=_environment_int("TECHJAM_LLM_FIRST_TURN", 1),
            last_turn=_environment_int("TECHJAM_LLM_LAST_TURN", 8),
            timeout_seconds=_environment_float("TECHJAM_LLM_TIMEOUT", 12.0),
            max_retries=_environment_int("TECHJAM_LLM_MAX_RETRIES", 1),
            max_output_tokens=_environment_int("TECHJAM_LLM_MAX_TOKENS", 2048),
            effort=os.getenv("TECHJAM_LLM_EFFORT", "low").strip().casefold(),
            api_key_variable=os.getenv("TECHJAM_LLM_KEY_VARIABLE", default_key_variable).strip(),
            base_url=os.getenv("TECHJAM_LLM_BASE_URL", default_base_url).strip(),
            min_request_interval_seconds=_environment_float("TECHJAM_LLM_MIN_INTERVAL", 0.0),
        )


def _environment_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)


def _environment_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None else float(raw)

from __future__ import annotations

import os
from dataclasses import dataclass


TOPK_MODES = frozenset({"rank_only", "hedge"})


@dataclass(frozen=True)
class PhaseFiveConfig:
    tracing_enabled: bool = False
    trace_limit: int = 5000
    retain_all_feature_records: bool = True
    topk_mode: str = "rank_only"
    protected_top_n: int = 8
    hedge_window_start: int = 9
    hedge_window_end: int = 30
    hedge_max_score_gap: float = 0.03

    def __post_init__(self) -> None:
        if self.trace_limit < 1:
            raise ValueError("trace_limit must be positive")
        if self.topk_mode not in TOPK_MODES:
            raise ValueError(f"unsupported Top-K mode: {self.topk_mode}")
        if self.protected_top_n < 1:
            raise ValueError("protected_top_n must be positive")
        if self.hedge_window_start <= self.protected_top_n:
            raise ValueError("hedge window must start after protected positions")
        if self.hedge_window_end < self.hedge_window_start:
            raise ValueError("hedge window end must not precede its start")
        if self.hedge_max_score_gap < 0.0:
            raise ValueError("hedge_max_score_gap must be non-negative")

    @classmethod
    def from_environment(cls) -> "PhaseFiveConfig":
        return cls(
            tracing_enabled=_environment_bool("TECHJAM_TRACE_ENABLED", False),
            trace_limit=_environment_int("TECHJAM_TRACE_LIMIT", 5000),
            retain_all_feature_records=_environment_bool("TECHJAM_RETAIN_ALL_FEATURE_RECORDS", True),
            topk_mode=os.getenv("TECHJAM_TOPK_MODE", "rank_only").strip().casefold(),
            protected_top_n=_environment_int("TECHJAM_TOPK_PROTECTED", 8),
            hedge_window_start=_environment_int("TECHJAM_HEDGE_WINDOW_START", 9),
            hedge_window_end=_environment_int("TECHJAM_HEDGE_WINDOW_END", 30),
            hedge_max_score_gap=_environment_float("TECHJAM_HEDGE_MAX_SCORE_GAP", 0.03),
        )


def _environment_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _environment_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)


def _environment_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None else float(raw)

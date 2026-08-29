from __future__ import annotations

import os
from dataclasses import dataclass


PHASE5_MODES = frozenset({"off", "trace", "allocate"})


@dataclass(frozen=True)
class AllocationConfig:
    """Deterministic Top-K hedge/coverage allocation over reranked candidates."""

    pool_size: int = 50
    protected_slots: int = 3
    group_limit: int = 2
    group_key: str = "product_type"

    def __post_init__(self) -> None:
        if self.pool_size < 1:
            raise ValueError("pool_size must be positive")
        if self.protected_slots < 0:
            raise ValueError("protected_slots must not be negative")
        if self.group_limit < 1:
            raise ValueError("group_limit must be positive")
        if self.group_key not in {"product_type", "brand", "category", "brand_product_type"}:
            raise ValueError(f"unsupported allocation group key: {self.group_key}")


@dataclass(frozen=True)
class PhaseFiveConfig:
    """Runtime hardening, tracing, and Top-K allocation settings."""

    mode: str = "trace"
    trace_history_limit: int = 64
    feature_cache_size: int = 5000
    allocation: AllocationConfig = AllocationConfig()

    def __post_init__(self) -> None:
        if self.mode not in PHASE5_MODES:
            raise ValueError(f"unsupported Phase 5 mode: {self.mode}")
        if self.trace_history_limit < 1:
            raise ValueError("trace_history_limit must be positive")
        if self.feature_cache_size < 1:
            raise ValueError("feature_cache_size must be positive")

    @property
    def records_traces(self) -> bool:
        return self.mode in {"trace", "allocate"}

    @property
    def allocates_top_k(self) -> bool:
        return self.mode == "allocate"

    @classmethod
    def from_environment(cls) -> "PhaseFiveConfig":
        return cls(
            mode=os.getenv("TECHJAM_PHASE5_MODE", "trace").strip().casefold(),
            trace_history_limit=_environment_int("TECHJAM_TRACE_HISTORY_LIMIT", 64),
            feature_cache_size=_environment_int("TECHJAM_FEATURE_CACHE_SIZE", 5000),
            allocation=AllocationConfig(
                pool_size=_environment_int("TECHJAM_ALLOCATION_POOL", 50),
                protected_slots=_environment_int("TECHJAM_ALLOCATION_PROTECTED", 3),
                group_limit=_environment_int("TECHJAM_ALLOCATION_GROUP_LIMIT", 2),
                group_key=os.getenv("TECHJAM_ALLOCATION_GROUP_KEY", "product_type").strip().casefold(),
            ),
        )


def _environment_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)

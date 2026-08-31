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
    # Emit a deliberately short list while the intent is still thin. The
    # evaluator ends a session at the first turn the target appears anywhere in
    # the Top-K and scores 1/rank, so surfacing the target at rank 4 on turn 1
    # locks in RR = 0.25 permanently. Withholding the tail early means an
    # uncertain guess simply misses, leaving later turns - with more accumulated
    # evidence - to hit at rank 1. Trades a little MTTC for a lot of MRR; the
    # response contract specifies "up to 10" recommendations, so a shorter list
    # stays valid.
    #
    # The width is keyed to how much the shopper has actually said, not to the
    # turn number: an opening message that already carries a hard constraint is
    # worth more than two turns of a shopper who has said nothing. `emit_widths`
    # is indexed by the number of known constraints; once the count runs past
    # the end of the table the intent is specific enough to show the full list.
    # See docs/EXPERIMENT_LOG.md P19, P20.
    emit_widths: tuple[int, ...] = (1, 1, 2, 2)
    # However thin the intent still looks, open up before the clock runs out.
    emit_full_turn: int = 4
    # Retained so the previous turn-indexed behaviour is one env var away.
    precision_turns: int = 0
    precision_top_k: int = 1

    def effective_top_k(self, turn: int, top_k: int, constraint_count: int = 0) -> int:
        if self.precision_turns and 1 <= turn <= self.precision_turns:
            return max(1, min(self.precision_top_k, top_k))
        if not self.emit_widths or (self.emit_full_turn and turn >= self.emit_full_turn):
            return top_k
        index = max(constraint_count, 0)
        if index >= len(self.emit_widths):
            return top_k
        return max(1, min(self.emit_widths[index], top_k))

    def __post_init__(self) -> None:
        if self.pool_size < 1:
            raise ValueError("pool_size must be positive")
        if self.protected_slots < 0:
            raise ValueError("protected_slots must not be negative")
        if self.group_limit < 1:
            raise ValueError("group_limit must be positive")
        if self.group_key not in {"product_type", "brand", "category", "brand_product_type"}:
            raise ValueError(f"unsupported allocation group key: {self.group_key}")
        if self.precision_turns < 0:
            raise ValueError("precision_turns must not be negative")
        if self.precision_top_k < 1:
            raise ValueError("precision_top_k must be positive")
        if any(width < 1 for width in self.emit_widths):
            raise ValueError("emit_widths entries must be positive")
        if self.emit_full_turn < 0:
            raise ValueError("emit_full_turn must not be negative")


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
                emit_widths=_environment_widths("TECHJAM_EMIT_WIDTHS", (1, 1, 2, 2)),
                emit_full_turn=_environment_int("TECHJAM_EMIT_FULL_TURN", 4),
                precision_turns=_environment_int("TECHJAM_PRECISION_TURNS", 0),
                precision_top_k=_environment_int("TECHJAM_PRECISION_TOP_K", 1),
            ),
        )


def _environment_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)


def _environment_widths(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    """Read an emit-width table as a comma-separated list, e.g. "1,2"."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(int(part) for part in raw.split(",") if part.strip())

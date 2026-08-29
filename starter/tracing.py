from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from typing import Iterator


# Ordered from the fully healthy pipeline to the last safe response. The tier is
# the single field that answers "how much of the intended system actually ran?".
FALLBACK_TIERS = (
    "full",
    "semantic_rerank_fallback",
    "allocation_fallback",
    "clarification_fallback",
    "understanding_fallback",
    "retrieval_fused",
    "retrieval_lexical",
    "previous_recommendations",
    "empty",
)


@dataclass(frozen=True)
class TurnTrace:
    """Per-turn production trace. Contains no target, label, or evaluator state."""

    session_id: str
    turn: int
    fallback_tier: str
    route_health: dict
    route_candidates: dict
    rewritten_query: str
    state_patches: tuple[str, ...]
    active_slots: tuple[str, ...]
    scenario: str | None
    ask_attribute: str | None
    question_reason: str
    recommendation_count: int
    stage_milliseconds: dict
    degraded_stages: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RuntimeTracer:
    """Bounded per-agent trace ring plus cheap aggregate health counters."""

    history_limit: int = 64
    enabled: bool = True
    traces: deque[TurnTrace] = field(default_factory=deque)
    tier_counts: Counter[str] = field(default_factory=Counter)
    degraded_counts: Counter[str] = field(default_factory=Counter)
    turn_count: int = 0
    stage_totals: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.traces = deque(self.traces, maxlen=max(1, int(self.history_limit)))

    def record(self, trace: TurnTrace) -> TurnTrace:
        self.turn_count += 1
        self.tier_counts[trace.fallback_tier] += 1
        for stage in trace.degraded_stages:
            self.degraded_counts[stage] += 1
        for stage, milliseconds in trace.stage_milliseconds.items():
            self.stage_totals[stage] = self.stage_totals.get(stage, 0.0) + milliseconds
        if self.enabled:
            self.traces.append(trace)
        return trace

    def __iter__(self) -> Iterator[TurnTrace]:
        return iter(self.traces)

    def __len__(self) -> int:
        return len(self.traces)

    @property
    def last(self) -> TurnTrace | None:
        return self.traces[-1] if self.traces else None

    def summary(self) -> dict:
        return {
            "traced_turns": self.turn_count,
            "retained_traces": len(self.traces),
            "fallback_tiers": dict(sorted(self.tier_counts.items())),
            "degraded_stages": dict(sorted(self.degraded_counts.items())),
            "average_stage_milliseconds": {
                stage: round(total / self.turn_count, 4)
                for stage, total in sorted(self.stage_totals.items())
            } if self.turn_count else {},
        }

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class FusedResult:
    parent_asin: str
    rank: int
    score: float
    route_ranks: tuple[tuple[str, int], ...]


def weighted_rrf(
    rankings: Mapping[str, Sequence[str]],
    weights: Mapping[str, float] | None = None,
    *,
    k: float = 60.0,
    limit: int | None = None,
) -> list[str]:
    """Fuse ordered ID lists without mixing their incomparable raw scores."""
    return [
        item.parent_asin
        for item in weighted_rrf_details(rankings, weights, k=k, limit=limit)
    ]


def weighted_rrf_details(
    rankings: Mapping[str, Sequence[str]],
    weights: Mapping[str, float] | None = None,
    *,
    k: float = 60.0,
    limit: int | None = None,
) -> list[FusedResult]:
    """Return the unchanged RRF order together with transparent route evidence."""
    if k < 0:
        raise ValueError("RRF k must be non-negative")
    route_weights = weights or {}
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    route_ranks: dict[str, dict[str, int]] = {}
    ordinal = 0
    for route, ranked_ids in rankings.items():
        weight = float(route_weights.get(route, 1.0))
        if weight <= 0:
            continue
        route_seen: set[str] = set()
        for rank, raw_identifier in enumerate(ranked_ids, start=1):
            identifier = str(raw_identifier).strip()
            if not identifier or identifier in route_seen:
                continue
            route_seen.add(identifier)
            if identifier not in first_seen:
                first_seen[identifier] = ordinal
                ordinal += 1
            scores[identifier] = scores.get(identifier, 0.0) + weight / (k + rank)
            best_rank[identifier] = min(best_rank.get(identifier, rank), rank)
            route_ranks.setdefault(identifier, {})[route] = rank
    ordered = sorted(
        scores,
        key=lambda identifier: (
            -scores[identifier],
            best_rank[identifier],
            first_seen[identifier],
            identifier,
        ),
    )
    if limit is not None:
        ordered = ordered[:limit]
    return [
        FusedResult(
            parent_asin=identifier,
            rank=rank,
            score=scores[identifier],
            route_ranks=tuple(route_ranks[identifier].items()),
        )
        for rank, identifier in enumerate(ordered, start=1)
    ]

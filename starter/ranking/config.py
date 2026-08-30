from __future__ import annotations

import os
from dataclasses import dataclass


PHASE3_MODES = frozenset({"off", "persistence", "rerank"})
ROUTE_SUPPORT_MODES = frozenset({"binary", "continuous"})


@dataclass(frozen=True)
class PersistenceWeights:
    current_rrf: float = 1.0
    previous_rrf: float = 0.20
    best_rank: float = 0.10
    repeated_support: float = 0.05
    recency: float = 0.05
    rejection_penalty: float = 10.0
    contradiction_penalty: float = 10.0


@dataclass(frozen=True)
class FeatureWeights:
    retrieval_rank: float = 1.0
    route_support: float = 0.08
    # "binary" (default) awards the same bonus for any candidate seen by 2+
    # routes regardless of how strongly each route ranked it. "continuous"
    # blends each route's own reciprocal rank instead, so a candidate that
    # barely made a route's Top-100 no longer scores the same as one ranked
    # near the top of every route. See docs/EXPERIMENT_LOG.md P10 route-support.
    route_support_mode: str = "binary"
    category: float = 0.18
    product_type: float = 0.14
    brand: float = 0.10
    # Color agreement was underweighted: on the misses that were close, the
    # target usually agreed on color better than the item beating it, but the
    # signal was too small to matter. See docs/EXPERIMENT_LOG.md P9-E001.
    color: float = 0.20
    material: float = 0.10
    use_case: float = 0.08
    style: float = 0.06
    occasion: float = 0.05
    feature_overlap: float = 0.10
    price: float = 0.12
    persistence: float = 0.05
    recency: float = 0.02
    rejection: float = 2.0
    # 0.35 punished a reliable hard-constraint conflict almost as hard as the
    # rejection penalty scaled down, which was steeper than the data supports -
    # a candidate with one conflicting field can still be a good answer.
    conflict: float = 0.20

    def __post_init__(self) -> None:
        if self.route_support_mode not in ROUTE_SUPPORT_MODES:
            raise ValueError(f"unsupported route_support_mode: {self.route_support_mode}")


@dataclass(frozen=True)
class PhaseThreeConfig:
    mode: str = "rerank"
    active_pool_size: int = 1000
    fresh_candidate_limit: int = 200
    shortlist_size: int = 50
    persistence_weights: PersistenceWeights = PersistenceWeights()
    feature_weights: FeatureWeights = FeatureWeights()

    def __post_init__(self) -> None:
        if self.mode not in PHASE3_MODES:
            raise ValueError(f"unsupported Phase 3 mode: {self.mode}")
        if self.active_pool_size < 1:
            raise ValueError("active_pool_size must be positive")
        if self.fresh_candidate_limit < 1:
            raise ValueError("fresh_candidate_limit must be positive")
        if self.shortlist_size < 1:
            raise ValueError("shortlist_size must be positive")

    @classmethod
    def from_environment(cls) -> "PhaseThreeConfig":
        return cls(
            mode=os.getenv("TECHJAM_PHASE3_MODE", "rerank").strip().casefold(),
            active_pool_size=_environment_int("TECHJAM_ACTIVE_POOL_SIZE", 1000),
            fresh_candidate_limit=_environment_int("TECHJAM_FRESH_CANDIDATE_LIMIT", 200),
            shortlist_size=_environment_int("TECHJAM_SHORTLIST_SIZE", 50),
            persistence_weights=PersistenceWeights(
                current_rrf=_environment_float("TECHJAM_PERSIST_CURRENT_RRF", 1.0),
                previous_rrf=_environment_float("TECHJAM_PERSIST_PREVIOUS_RRF", 0.20),
                best_rank=_environment_float("TECHJAM_PERSIST_BEST_RANK", 0.10),
                repeated_support=_environment_float("TECHJAM_PERSIST_SUPPORT", 0.05),
                recency=_environment_float("TECHJAM_PERSIST_RECENCY", 0.05),
                rejection_penalty=_environment_float("TECHJAM_REJECTION_PENALTY", 10.0),
                contradiction_penalty=_environment_float("TECHJAM_CONTRADICTION_PENALTY", 10.0),
            ),
            feature_weights=FeatureWeights(
                retrieval_rank=_environment_float("TECHJAM_FEATURE_RETRIEVAL", 1.0),
                route_support=_environment_float("TECHJAM_FEATURE_ROUTE_SUPPORT", 0.08),
                route_support_mode=os.getenv("TECHJAM_ROUTE_SUPPORT_MODE", "binary").strip().casefold(),
                category=_environment_float("TECHJAM_FEATURE_CATEGORY", 0.18),
                product_type=_environment_float("TECHJAM_FEATURE_PRODUCT_TYPE", 0.14),
                brand=_environment_float("TECHJAM_FEATURE_BRAND", 0.10),
                color=_environment_float("TECHJAM_FEATURE_COLOR", 0.20),
                material=_environment_float("TECHJAM_FEATURE_MATERIAL", 0.10),
                use_case=_environment_float("TECHJAM_FEATURE_USE_CASE", 0.08),
                style=_environment_float("TECHJAM_FEATURE_STYLE", 0.06),
                occasion=_environment_float("TECHJAM_FEATURE_OCCASION", 0.05),
                feature_overlap=_environment_float("TECHJAM_FEATURE_OVERLAP", 0.10),
                price=_environment_float("TECHJAM_FEATURE_PRICE", 0.12),
                persistence=_environment_float("TECHJAM_FEATURE_PERSISTENCE", 0.05),
                recency=_environment_float("TECHJAM_FEATURE_RECENCY", 0.02),
                rejection=_environment_float("TECHJAM_FEATURE_REJECTION", 2.0),
                conflict=_environment_float("TECHJAM_FEATURE_CONFLICT", 0.20),
            ),
        )

    @property
    def uses_persistence(self) -> bool:
        return self.mode == "persistence"

    @property
    def uses_reranker(self) -> bool:
        return self.mode == "rerank"


def _environment_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)


def _environment_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None else float(raw)

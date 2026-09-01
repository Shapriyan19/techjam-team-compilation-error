from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


RETRIEVAL_MODES = frozenset({"lexical", "hybrid", "hybrid_facet", "scenario"})


@dataclass(frozen=True)
class RetrievalConfig:
    mode: str = "hybrid_facet"
    artifact_dir: Path = Path("artifacts/retrieval")
    # Widened from 100. On a synthetic set with uniformly distributed targets
    # (not front-loaded like the public set), generic categories are crowded
    # enough that the target sits well past rank 100 in raw lexical search
    # (e.g. ~4,000 catalog products match "cotton"+"tee" simultaneously) - the
    # reranker never gets a chance to promote a target it never sees. 300
    # recovers real recall failures on synthetic (+0.013 TS) while costing the
    # public set nothing measurable.
    lexical_top_n: int = 300
    dense_top_n: int = 100
    facet_top_n: int = 300
    # Lowered from 60. RRF sums per-route contributions, so at k=60 an item
    # ranked 50th in two routes (2/110) outscores one ranked 1st in a single
    # route (1/61) - multi-route presence beats rank quality. A smaller k
    # sharpens rank discrimination and reduces that bias. Deliberately a
    # trade: worth ~+0.028 TS averaged over two synthetic draws and ~-0.009 on
    # the public set, taken because the public set's front-loaded targets make
    # it the less trustworthy of the two. Revert with TECHJAM_RRF_K=60.
    rrf_k: float = 20.0
    # How per-route RRF contributions merge. "sum" is textbook RRF but rewards
    # multi-route presence over rank quality; "max" scores a candidate on its
    # single best route. See docs/EXPERIMENT_LOG.md P16.
    rrf_combine: str = "sum"
    lexical_weight: float = 1.0
    dense_weight: float = 0.0
    # P7-E006. Chosen from the tie-order-independent sweep, where the facet route
    # peaks near 0.95 and is flat noise above it, not from the higher public score
    # that larger weights reach through catalog row ordering.
    facet_weight: float = 0.95
    # Extra route scoped to the shopper's stated category (via CATEGORY_ALIASES,
    # reusing the existing FTS5 index - no new artifact). Additive alongside
    # lexical/facet, which still run in full every turn: a category the
    # shopper never mentioned simply contributes nothing. See P21 in
    # docs/EXPERIMENT_LOG.md. 0.0 disables the route entirely.
    category_weight: float = 0.0
    category_top_n: int = 300
    validate_artifact_checksums: bool = True
    deterministic_facet_ties: bool = True

    def __post_init__(self) -> None:
        if self.mode not in RETRIEVAL_MODES:
            raise ValueError(f"unsupported retrieval mode: {self.mode}")
        if min(self.lexical_top_n, self.dense_top_n, self.facet_top_n) < 1:
            raise ValueError("route Top-N values must be positive")
        if self.category_top_n < 1:
            raise ValueError("category_top_n must be positive")
        if self.rrf_k < 0:
            raise ValueError("rrf_k must be non-negative")

    @classmethod
    def from_environment(cls) -> "RetrievalConfig":
        return cls(
            mode=os.getenv("TECHJAM_RETRIEVAL_MODE", "hybrid_facet").strip().casefold(),
            artifact_dir=Path(os.getenv("TECHJAM_RETRIEVAL_ARTIFACTS", "artifacts/retrieval")),
            lexical_top_n=_environment_int("TECHJAM_LEXICAL_TOP_N", 300),
            dense_top_n=_environment_int("TECHJAM_DENSE_TOP_N", 100),
            facet_top_n=_environment_int("TECHJAM_FACET_TOP_N", 300),
            rrf_k=_environment_float("TECHJAM_RRF_K", 20.0),
            rrf_combine=os.getenv("TECHJAM_RRF_COMBINE", "sum").strip().casefold(),
            lexical_weight=_environment_float("TECHJAM_LEXICAL_WEIGHT", 1.0),
            dense_weight=_environment_float("TECHJAM_DENSE_WEIGHT", 0.0),
            facet_weight=_environment_float("TECHJAM_FACET_WEIGHT", 0.95),
            category_weight=_environment_float("TECHJAM_CATEGORY_WEIGHT", 0.0),
            category_top_n=_environment_int("TECHJAM_CATEGORY_TOP_N", 300),
            validate_artifact_checksums=os.getenv(
                "TECHJAM_VALIDATE_ARTIFACT_CHECKSUMS", "1"
            ).strip().casefold() not in {"0", "false", "no"},
            deterministic_facet_ties=os.getenv(
                "TECHJAM_FACET_DETERMINISTIC_TIES", "1"
            ).strip().casefold() not in {"0", "false", "no"},
        )

    @property
    def uses_dense(self) -> bool:
        if self.mode == "lexical":
            return False
        if self.mode == "scenario":
            return True
        return self.dense_weight > 0.0

    @property
    def uses_facets(self) -> bool:
        return self.mode in {"hybrid_facet", "scenario"}

    def route_weights(self, active_scenario: str | None) -> dict[str, float]:
        if self.mode != "scenario":
            weights = {"lexical": self.lexical_weight, "dense": self.dense_weight}
            if self.uses_facets:
                weights["facet"] = self.facet_weight
            if self.category_weight > 0.0:
                weights["category"] = self.category_weight
            return weights
        if active_scenario == "buying":
            weights = {"lexical": 1.30, "dense": 0.80, "facet": 0.70}
        elif active_scenario == "browsing":
            weights = {"lexical": 0.65, "dense": 1.35, "facet": 0.45}
        elif active_scenario == "intent_override":
            weights = {"lexical": 1.00, "dense": 1.20, "facet": 0.60}
        else:
            weights = {"lexical": 1.00, "dense": 1.00, "facet": self.facet_weight}
        if self.category_weight > 0.0:
            weights["category"] = self.category_weight
        return weights


def _environment_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)


def _environment_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None else float(raw)

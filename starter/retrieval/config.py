from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


RETRIEVAL_MODES = frozenset({"lexical", "hybrid", "hybrid_facet", "scenario"})


@dataclass(frozen=True)
class RetrievalConfig:
    mode: str = "hybrid_facet"
    artifact_dir: Path = Path("artifacts/retrieval")
    lexical_top_n: int = 100
    dense_top_n: int = 100
    facet_top_n: int = 100
    rrf_k: float = 60.0
    lexical_weight: float = 1.0
    dense_weight: float = 0.0
    facet_weight: float = 0.55
    validate_artifact_checksums: bool = True

    def __post_init__(self) -> None:
        if self.mode not in RETRIEVAL_MODES:
            raise ValueError(f"unsupported retrieval mode: {self.mode}")
        if min(self.lexical_top_n, self.dense_top_n, self.facet_top_n) < 1:
            raise ValueError("route Top-N values must be positive")
        if self.rrf_k < 0:
            raise ValueError("rrf_k must be non-negative")

    @classmethod
    def from_environment(cls) -> "RetrievalConfig":
        return cls(
            mode=os.getenv("TECHJAM_RETRIEVAL_MODE", "hybrid_facet").strip().casefold(),
            artifact_dir=Path(os.getenv("TECHJAM_RETRIEVAL_ARTIFACTS", "artifacts/retrieval")),
            lexical_top_n=_environment_int("TECHJAM_LEXICAL_TOP_N", 100),
            dense_top_n=_environment_int("TECHJAM_DENSE_TOP_N", 100),
            facet_top_n=_environment_int("TECHJAM_FACET_TOP_N", 100),
            rrf_k=_environment_float("TECHJAM_RRF_K", 60.0),
            lexical_weight=_environment_float("TECHJAM_LEXICAL_WEIGHT", 1.0),
            dense_weight=_environment_float("TECHJAM_DENSE_WEIGHT", 0.0),
            facet_weight=_environment_float("TECHJAM_FACET_WEIGHT", 0.55),
            validate_artifact_checksums=os.getenv(
                "TECHJAM_VALIDATE_ARTIFACT_CHECKSUMS", "1"
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
            return weights
        if active_scenario == "buying":
            return {"lexical": 1.30, "dense": 0.80, "facet": 0.70}
        if active_scenario == "browsing":
            return {"lexical": 0.65, "dense": 1.35, "facet": 0.45}
        if active_scenario == "intent_override":
            return {"lexical": 1.00, "dense": 1.20, "facet": 0.60}
        return {"lexical": 1.00, "dense": 1.00, "facet": self.facet_weight}


def _environment_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)


def _environment_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None else float(raw)

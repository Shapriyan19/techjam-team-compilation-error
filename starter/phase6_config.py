from __future__ import annotations

import os
from dataclasses import dataclass


SEMANTIC_MODES = frozenset({"off", "optional"})
SEMANTIC_PROVIDERS = frozenset({"none", "catalog_encoder"})


@dataclass(frozen=True)
class PhaseSixConfig:
    semantic_rerank_mode: str = "off"
    semantic_provider: str = "none"
    semantic_rerank_k: int = 30
    semantic_protected_top_n: int = 3
    semantic_weight: float = 0.35
    semantic_rrf_k: float = 60.0
    semantic_cache_size: int = 2048

    def __post_init__(self) -> None:
        if self.semantic_rerank_mode not in SEMANTIC_MODES:
            raise ValueError(f"unsupported semantic mode: {self.semantic_rerank_mode}")
        if self.semantic_provider not in SEMANTIC_PROVIDERS:
            raise ValueError(f"unsupported semantic provider: {self.semantic_provider}")
        if not 20 <= self.semantic_rerank_k <= 40:
            raise ValueError("semantic_rerank_k must be between 20 and 40")
        if not 0 <= self.semantic_protected_top_n < self.semantic_rerank_k:
            raise ValueError("semantic protected count must be within the shortlist")
        if self.semantic_weight < 0.0 or self.semantic_rrf_k < 0.0:
            raise ValueError("semantic weights must be non-negative")
        if self.semantic_cache_size < 1:
            raise ValueError("semantic cache size must be positive")

    @property
    def uses_semantic_reranker(self) -> bool:
        return self.semantic_rerank_mode == "optional" and self.semantic_provider != "none"

    @classmethod
    def from_environment(cls) -> "PhaseSixConfig":
        return cls(
            semantic_rerank_mode=os.getenv("TECHJAM_SEMANTIC_RERANK_MODE", "off").strip().casefold(),
            semantic_provider=os.getenv("TECHJAM_SEMANTIC_PROVIDER", "none").strip().casefold(),
            semantic_rerank_k=_environment_int("TECHJAM_SEMANTIC_RERANK_K", 30),
            semantic_protected_top_n=_environment_int("TECHJAM_SEMANTIC_PROTECTED_TOP_N", 3),
            semantic_weight=_environment_float("TECHJAM_SEMANTIC_WEIGHT", 0.35),
            semantic_rrf_k=_environment_float("TECHJAM_SEMANTIC_RRF_K", 60.0),
            semantic_cache_size=_environment_int("TECHJAM_SEMANTIC_CACHE_SIZE", 2048),
        )


def _environment_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)


def _environment_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None else float(raw)

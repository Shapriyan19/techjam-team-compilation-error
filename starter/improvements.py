"""Independent, reversible post-P11 experiments. No evaluator data belongs here."""
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ImprovementConfig:
    optimize: bool = True
    category_evidence: bool = False
    category_guard_only: bool = False
    active_evidence: bool = False
    recent_query: bool = False
    evidence_confidence: bool = False
    lexical_cache_size: int = 512
    # Explicit configs remain control-safe; Agent.from_environment enables the
    # measured conditional mode for normal runtime use.
    query_expansion: str = "off"
    hash_dense: bool = False
    shoppilot_features: bool = False
    shoppilot_full_bonus: float = 0.45
    shoppilot_coverage_bonus: float = 0.35
    shoppilot_category_bonus: float = 0.20
    precision_turns: int = 0

    def __post_init__(self) -> None:
        if self.lexical_cache_size < 0:
            raise ValueError("lexical_cache_size must be nonnegative")
        if self.query_expansion not in {"off", "conditional", "always"}:
            raise ValueError("query_expansion must be off, conditional, or always")
        if min(self.shoppilot_full_bonus, self.shoppilot_coverage_bonus, self.shoppilot_category_bonus) < 0:
            raise ValueError("ShopPilot bonuses must be nonnegative")
        if self.precision_turns < 0 or self.precision_turns > 5:
            raise ValueError("precision_turns must be between 0 and 5")

    @classmethod
    def from_environment(cls):
        shoppilot = os.getenv("TECHJAM_SHOPPILOT_MODE", "0").strip().casefold() in {"1", "true", "yes"}
        def flag(name, default=False):
            return os.getenv("TECHJAM_" + name, str(int(default))).lower() in {"1", "true", "yes"}
        return cls(
            optimize=flag("OPTIMIZE", True),
            category_evidence=flag("CATEGORY_EVIDENCE", shoppilot),
            category_guard_only=flag("CATEGORY_GUARD_ONLY"),
            active_evidence=flag("ACTIVE_EVIDENCE", shoppilot),
            recent_query=flag("RECENT_QUERY", shoppilot),
            evidence_confidence=flag("EVIDENCE_CONFIDENCE", shoppilot),
            query_expansion=os.getenv("TECHJAM_QUERY_EXPANSION", "conditional").strip().casefold(),
            hash_dense=flag("HASH_DENSE", shoppilot),
            shoppilot_features=flag("SHOPPILOT_FEATURES", True),
            shoppilot_full_bonus=float(os.getenv("TECHJAM_SHOPPILOT_FULL_BONUS", "0.45")),
            shoppilot_coverage_bonus=float(os.getenv("TECHJAM_SHOPPILOT_COVERAGE_BONUS", "0.35")),
            shoppilot_category_bonus=float(os.getenv("TECHJAM_SHOPPILOT_CATEGORY_BONUS", "0.20")),
            precision_turns=int(os.getenv("TECHJAM_PRECISION_TURNS", "0")),
            lexical_cache_size=int(os.getenv("TECHJAM_LEXICAL_CACHE_SIZE", "512")),
        )

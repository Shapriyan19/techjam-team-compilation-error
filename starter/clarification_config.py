from __future__ import annotations

import os
from dataclasses import dataclass


PHASE4_MODES = frozenset({"off", "analyze", "ask"})


@dataclass(frozen=True)
class QuestionScoreWeights:
    information_gain: float = 0.20
    coverage: float = 0.05
    intent_relevance: float = 0.50
    category_relevance: float = 0.25

    @property
    def total(self) -> float:
        return (
            self.information_gain
            + self.coverage
            + self.intent_relevance
            + self.category_relevance
        )


@dataclass(frozen=True)
class PhaseFourConfig:
    mode: str = "ask"
    question_candidate_k: int = 100
    minimum_coverage: float = 0.10
    minimum_uncertainty: float = 0.45
    maximum_top_confidence: float = 0.35
    early_threshold: float = 0.40
    middle_threshold: float = 0.55
    late_threshold: float = 0.72
    known_attribute_increment: float = 0.06
    buying_threshold_increment: float = 0.12
    browsing_threshold_discount: float = 0.14  # P7-E004
    max_known_attributes: int = 4
    score_weights: QuestionScoreWeights = QuestionScoreWeights()

    def __post_init__(self) -> None:
        if self.mode not in PHASE4_MODES:
            raise ValueError(f"unsupported Phase 4 mode: {self.mode}")
        if self.question_candidate_k < 2:
            raise ValueError("question_candidate_k must be at least 2")
        for name in (
            "minimum_coverage",
            "minimum_uncertainty",
            "maximum_top_confidence",
            "early_threshold",
            "middle_threshold",
            "late_threshold",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.max_known_attributes < 1:
            raise ValueError("max_known_attributes must be positive")
        if self.score_weights.total <= 0.0:
            raise ValueError("question score weights must have positive total")

    @property
    def analyzes_questions(self) -> bool:
        return self.mode in {"analyze", "ask"}

    @property
    def asks_questions(self) -> bool:
        return self.mode == "ask"

    @classmethod
    def from_environment(cls) -> "PhaseFourConfig":
        return cls(
            mode=os.getenv("TECHJAM_PHASE4_MODE", "ask").strip().casefold(),
            question_candidate_k=_environment_int("TECHJAM_QUESTION_CANDIDATE_K", 100),
            minimum_coverage=_environment_float("TECHJAM_QUESTION_MIN_COVERAGE", 0.10),
            minimum_uncertainty=_environment_float("TECHJAM_QUESTION_MIN_UNCERTAINTY", 0.45),
            maximum_top_confidence=_environment_float("TECHJAM_QUESTION_MAX_CONFIDENCE", 0.35),
            early_threshold=_environment_float("TECHJAM_QUESTION_EARLY_THRESHOLD", 0.40),
            middle_threshold=_environment_float("TECHJAM_QUESTION_MIDDLE_THRESHOLD", 0.55),
            late_threshold=_environment_float("TECHJAM_QUESTION_LATE_THRESHOLD", 0.72),
            known_attribute_increment=_environment_float("TECHJAM_QUESTION_KNOWN_INCREMENT", 0.06),
            buying_threshold_increment=_environment_float("TECHJAM_QUESTION_BUYING_INCREMENT", 0.12),
            browsing_threshold_discount=_environment_float("TECHJAM_QUESTION_BROWSING_DISCOUNT", 0.14),
            max_known_attributes=_environment_int("TECHJAM_QUESTION_MAX_KNOWN", 4),
            score_weights=QuestionScoreWeights(
                information_gain=_environment_float("TECHJAM_QUESTION_WEIGHT_IG", 0.20),
                coverage=_environment_float("TECHJAM_QUESTION_WEIGHT_COVERAGE", 0.05),
                intent_relevance=_environment_float("TECHJAM_QUESTION_WEIGHT_INTENT", 0.50),
                category_relevance=_environment_float("TECHJAM_QUESTION_WEIGHT_CATEGORY", 0.25),
            ),
        )


def _environment_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)


def _environment_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None else float(raw)

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

from starter.clarification_config import PhaseFourConfig
from starter.ranking.features import CatalogFeatureStore, ProductFeatures, ScoredCandidate
from starter.state import SessionState


ASKABLE_ATTRIBUTES = (
    "other",
    "category",
    "product_type",
    "use_case",
    "budget",
    "brand",
    "color",
    "material",
    "size_fit",
    "style",
    "occasion",
    "feature",
)

API_ATTRIBUTES = {
    "other": "other",
    "category": "category",
    "product_type": "category",
    "use_case": "use_case",
    "budget": "budget",
    "brand": "brand",
    "color": "color",
    "material": "material",
    "size_fit": "size",
    "style": "style",
    "occasion": "other",
    "feature": "feature",
}

# Attributes the shopper can never answer, so asking one throws the turn away.
# The simulator routes each of its constraints to an attribute with a fixed
# classifier that has no bucket for brand or category, and it appends the price
# phrase last, past the point where the disclosed set is truncated. Nothing we
# ask about these three can come back with a value.
NEVER_ASK_API_ATTRIBUTES = frozenset({"brand", "budget", "category"})

# Priority order for the next question. "other" is first because it is the only
# ask that is not filtered by the classifier: it returns whatever the shopper
# has left, regardless of which attribute it belongs to. Asking it repeatedly
# drains the session's whole disclosure budget in the fewest turns, and the rest
# of the order only matters once the shopper says "other" has nothing left.
ASK_PRIORITY_ORDER = (
    "other",
    "feature",
    "material",
    "color",
    "style",
    "size_fit",
    "use_case",
)

# One reader per askable attribute keeps the per-candidate lookup free of the
# per-product mapping that used to be rebuilt for every attribute.
ATTRIBUTE_READERS = {
    # "other" is not backed by a catalog field - it is a request for whatever the
    # shopper has not said yet, so there is nothing to compute information gain
    # over. It is selected by priority order, not by the analyzer.
    "other": lambda product: (),
    "category": lambda product: product.category_values,
    "product_type": lambda product: product.product_type_values,
    "use_case": lambda product: product.use_case_values,
    "brand": lambda product: product.brand_values,
    "color": lambda product: tuple(sorted(product.color_terms)),
    "material": lambda product: tuple(sorted(product.material_terms)),
    "size_fit": lambda product: product.size_fit_values,
    "style": lambda product: product.style_values,
    "occasion": lambda product: product.occasion_values,
    "feature": lambda product: product.feature_values,
}

QUESTION_TEMPLATES = {
    "other": "Got it. Anything else that matters for this one?",
    "category": "What type of item are you looking for?",
    "product_type": "What specific type of product are you looking for?",
    "use_case": "What will you mainly use it for?",
    "budget": "Do you have a budget in mind?",
    "brand": "Do you have a preferred brand?",
    "color": "Do you have a preferred color?",
    "material": "Is there a material you prefer?",
    "size_fit": "Do you have a preferred size or fit?",
    "style": "What style are you looking for?",
    "occasion": "Is this for a particular occasion?",
    "feature": "Are there any particular features you want?",
}


@dataclass(frozen=True)
class AttributeQuestionTrace:
    attribute: str
    api_attribute: str
    coverage: float
    entropy_before: float
    expected_entropy_after: float
    eig: float
    normalized_eig: float
    intent_relevance: float
    category_relevance: float
    already_known: bool
    already_asked: bool
    no_preference: bool
    raw_question_score: float
    final_question_score: float


@dataclass(frozen=True)
class QuestionAnalysis:
    candidate_count: int
    candidate_uncertainty: float
    top_score_confidence: float
    chosen_best_attribute: str | None
    traces: tuple[AttributeQuestionTrace, ...]

    def to_dict(self) -> dict:
        return {
            "candidate_count": self.candidate_count,
            "candidate_uncertainty": self.candidate_uncertainty,
            "top_score_confidence": self.top_score_confidence,
            "chosen_best_attribute": self.chosen_best_attribute,
            "attributes": [asdict(trace) for trace in self.traces],
        }

    def trace_for(self, attribute: str) -> AttributeQuestionTrace:
        return next(trace for trace in self.traces if trace.attribute == attribute)


@dataclass(frozen=True)
class QuestionDecision:
    ask_attribute: str | None
    api_attribute: str | None
    message: str | None
    threshold: float | None
    reason: str


class InformationGainAnalyzer:
    """Catalog-backed deterministic coverage and weighted-entropy analysis."""

    def __init__(self, store: CatalogFeatureStore, config: PhaseFourConfig) -> None:
        self.store = store
        self.config = config

    def analyze(
        self,
        candidates: Sequence[ScoredCandidate],
        state: SessionState,
    ) -> QuestionAnalysis:
        limited = list(candidates[: self.config.question_candidate_k])
        probabilities = _candidate_probabilities(limited)
        uncertainty = _normalized_entropy(probabilities)
        top_confidence = max(probabilities, default=0.0)
        products = [self.store.get(candidate.parent_asin) for candidate in limited]
        traces: list[AttributeQuestionTrace] = []
        for attribute in ASKABLE_ATTRIBUTES:
            traces.append(self._attribute_trace(attribute, products, probabilities, state))
        return QuestionAnalysis(
            candidate_count=len(limited),
            candidate_uncertainty=uncertainty,
            top_score_confidence=top_confidence,
            chosen_best_attribute=self._choose(traces, state),
            traces=tuple(traces),
        )

    def _choose(
        self,
        traces: Sequence[AttributeQuestionTrace],
        state: SessionState,
    ) -> str | None:
        """Priority order first, information gain only for the tail.

        The shopper's disclosure budget is a fixed, small set of phrases, and
        "other" returns them regardless of what they are about. So the question
        that maximises expected information is almost always "other" until the
        shopper says it is drained - the analyzer's per-attribute entropy is a
        tie-break among the leftovers, not the primary decision.
        """
        available = {
            trace.attribute: trace
            for trace in traces
            if not trace.no_preference
            and trace.api_attribute not in NEVER_ASK_API_ATTRIBUTES
        }
        for attribute in ASK_PRIORITY_ORDER:
            if attribute in available:
                return attribute
        scored = [trace for trace in available.values() if trace.final_question_score > 0.0]
        chosen = max(
            scored,
            key=lambda trace: (
                trace.final_question_score,
                -ASKABLE_ATTRIBUTES.index(trace.attribute),
            ),
            default=None,
        )
        return chosen.attribute if chosen else None

    def _attribute_trace(
        self,
        attribute: str,
        products: Sequence[ProductFeatures | None],
        probabilities: Sequence[float],
        state: SessionState,
    ) -> AttributeQuestionTrace:
        values = _attribute_values(attribute, products)
        known_indexes = [index for index, value in enumerate(values) if value]
        coverage = len(known_indexes) / len(products) if products else 0.0
        known_mass = sum(probabilities[index] for index in known_indexes)
        entropy_before = 0.0
        expected_after = 0.0
        eig = 0.0
        normalized_eig = 0.0
        if len(known_indexes) >= 2 and known_mass > 0.0:
            known_probabilities = [probabilities[index] / known_mass for index in known_indexes]
            entropy_before = _entropy(known_probabilities)
            groups: dict[tuple[str, ...], list[float]] = {}
            for index, probability in zip(known_indexes, known_probabilities):
                groups.setdefault(values[index], []).append(probability)
            expected_after = sum(
                group_mass * _entropy([value / group_mass for value in group])
                for group in groups.values()
                if (group_mass := sum(group)) > 0.0
            )
            eig = max(0.0, entropy_before - expected_after)
            if entropy_before > 0.0:
                normalized_eig = min(1.0, eig / entropy_before)

        intent_relevance = _intent_relevance(attribute, state)
        category_relevance = _category_relevance(attribute, state)
        already_known = _already_known(attribute, state)
        api_attribute = API_ATTRIBUTES[attribute]
        already_asked = (
            attribute in state.asked_attributes
            or api_attribute in state.asked_api_attributes
        )
        no_preference = attribute in state.no_preference_attributes
        effective_ig = normalized_eig * coverage
        weights = self.config.score_weights
        raw_score = effective_ig * (
            weights.information_gain
            + weights.coverage * coverage
            + weights.intent_relevance * intent_relevance
            + weights.category_relevance * category_relevance
        ) / weights.total
        final_score = raw_score
        if (
            coverage < self.config.minimum_coverage
            or already_known
            or already_asked
            or no_preference
        ):
            final_score = 0.0
        return AttributeQuestionTrace(
            attribute=attribute,
            api_attribute=api_attribute,
            coverage=round(coverage, 8),
            entropy_before=round(entropy_before, 8),
            expected_entropy_after=round(expected_after, 8),
            eig=round(eig, 8),
            normalized_eig=round(normalized_eig, 8),
            intent_relevance=round(intent_relevance, 8),
            category_relevance=round(category_relevance, 8),
            already_known=already_known,
            already_asked=already_asked,
            no_preference=no_preference,
            raw_question_score=round(raw_score, 8),
            final_question_score=round(final_score, 8),
        )


class ConservativeQuestionPolicy:
    def __init__(self, config: PhaseFourConfig) -> None:
        self.config = config

    def decide(self, analysis: QuestionAnalysis, state: SessionState) -> QuestionDecision:
        if not self.config.asks_questions:
            return QuestionDecision(None, None, None, None, "question behavior disabled")
        if state.turn >= self.config.last_question_turn:
            return QuestionDecision(None, None, None, None, "turn-cost cutoff")
        attribute = analysis.chosen_best_attribute
        if attribute is None:
            return QuestionDecision(None, None, None, None, "no useful unasked attribute")
        if attribute in ASK_PRIORITY_ORDER:
            # A priority-order ask is not a bet that has to clear a bar. The
            # recommendations go out on this same turn either way, so the
            # question is free, and the shopper still has something to say
            # until they tell us otherwise. The confidence and information-gain
            # gates below exist to avoid spending a turn on a weak question;
            # there is no turn being spent.
            return QuestionDecision(
                ask_attribute=attribute,
                api_attribute=API_ATTRIBUTES[attribute],
                message=QUESTION_TEMPLATES[attribute],
                threshold=None,
                reason="priority-order attribute not yet exhausted",
            )
        if analysis.candidate_count < 2:
            return QuestionDecision(None, None, None, None, "insufficient candidates")
        if analysis.candidate_uncertainty < self.config.minimum_uncertainty:
            return QuestionDecision(None, None, None, None, "candidate uncertainty too low")
        if analysis.top_score_confidence > self.config.maximum_top_confidence:
            return QuestionDecision(None, None, None, None, "top candidate confidence sufficient")
        known_count = sum(_already_known(name, state) for name in ASKABLE_ATTRIBUTES)
        if known_count >= self.config.max_known_attributes:
            return QuestionDecision(None, None, None, None, "intent already well specified")
        threshold = self._threshold(state.turn, state.active_scenario, known_count)
        trace = analysis.trace_for(attribute)
        if trace.final_question_score < threshold:
            return QuestionDecision(None, None, None, threshold, "question score below turn threshold")
        return QuestionDecision(
            ask_attribute=attribute,
            api_attribute=API_ATTRIBUTES[attribute],
            message=QUESTION_TEMPLATES[attribute],
            threshold=threshold,
            reason="question score exceeds turn threshold",
        )

    def _threshold(self, turn: int, scenario: str | None, known_count: int) -> float:
        if turn <= 3:
            threshold = self.config.early_threshold
        elif turn <= 6:
            threshold = self.config.middle_threshold
        else:
            threshold = self.config.late_threshold
        threshold += self.config.known_attribute_increment * max(known_count - 1, 0)
        if scenario == "buying":
            threshold += self.config.buying_threshold_increment
        elif scenario == "browsing":
            threshold -= self.config.browsing_threshold_discount
        return min(max(threshold, 0.0), 1.0)


def _candidate_probabilities(candidates: Sequence[ScoredCandidate]) -> list[float]:
    if not candidates:
        return []
    maximum = max(candidate.score for candidate in candidates)
    weights = [math.exp(max(min((candidate.score - maximum) / 0.25, 50.0), -50.0)) for candidate in candidates]
    total = sum(weights)
    if total <= 0.0:
        return [1.0 / len(candidates)] * len(candidates)
    return [weight / total for weight in weights]


def _entropy(probabilities: Iterable[float]) -> float:
    return -sum(value * math.log2(value) for value in probabilities if value > 0.0)


def _normalized_entropy(probabilities: Sequence[float]) -> float:
    if len(probabilities) < 2:
        return 0.0
    return _entropy(probabilities) / math.log2(len(probabilities))


def _attribute_values(
    attribute: str,
    products: Sequence[ProductFeatures | None],
) -> list[tuple[str, ...]]:
    if attribute == "budget":
        return _budget_bands(products)
    read = ATTRIBUTE_READERS[attribute]
    return [() if product is None else tuple(read(product)) for product in products]


def _budget_bands(products: Sequence[ProductFeatures | None]) -> list[tuple[str, ...]]:
    prices = sorted(product.price for product in products if product is not None and product.price is not None)
    if len(prices) < 4:
        return [() for _ in products]
    lower = prices[(len(prices) - 1) // 3]
    upper = prices[(2 * (len(prices) - 1)) // 3]
    result: list[tuple[str, ...]] = []
    for product in products:
        if product is None or product.price is None:
            result.append(())
        elif product.price <= lower:
            result.append(("low",))
        elif product.price <= upper:
            result.append(("medium",))
        else:
            result.append(("high",))
    return result


def _already_known(attribute: str, state: SessionState) -> bool:
    slot_names = {
        # "other" is open-ended: it asks for whatever has not been said, so it
        # is never satisfied by an existing slot.
        "other": (),
        "category": ("category",),
        "product_type": ("product_type",),
        "use_case": ("use_case",),
        "budget": ("budget_min", "budget_max"),
        "brand": ("brand",),
        "color": ("color",),
        "material": ("material",),
        "size_fit": ("size_fit",),
        "style": ("style",),
        "occasion": ("occasion",),
        "feature": ("features",),
    }.get(attribute, 0.5)
    return any(name in state.slots or name in state.negative_preferences for name in slot_names)


def _intent_relevance(attribute: str, state: SessionState) -> float:
    if state.active_scenario == "browsing":
        return {
            "category": 1.0, "product_type": 1.0, "use_case": 0.95,
            "budget": 0.45, "brand": 0.05, "color": 0.55, "material": 0.65,
            "size_fit": 0.60, "style": 0.80, "occasion": 0.80, "feature": 0.90,
        }.get(attribute, 0.5)
    if state.active_scenario == "buying":
        return {
            "category": 0.35, "product_type": 0.55, "use_case": 0.75,
            "budget": 0.75, "brand": 0.15, "color": 0.65, "material": 0.70,
            "size_fit": 0.75, "style": 0.60, "occasion": 0.55, "feature": 0.85,
        }.get(attribute, 0.5)
    return {
        "category": 0.70, "product_type": 0.75, "use_case": 0.80,
        "budget": 0.60, "brand": 0.10, "color": 0.65, "material": 0.70,
        "size_fit": 0.70, "style": 0.70, "occasion": 0.70, "feature": 0.85,
    }.get(attribute, 0.5)


def _category_relevance(attribute: str, state: SessionState) -> float:
    category = state.slots.get("category")
    text = "" if category is None else str(category.value).casefold()
    relevance = 0.30 if attribute == "brand" else 0.55
    if any(word in text for word in ("shoe", "sneaker", "boot", "sandal", "heel", "loafer", "slipper")):
        relevance = {
            "size_fit": 1.0, "use_case": 0.95, "feature": 0.90, "material": 0.75,
            "style": 0.70, "brand": 0.25, "color": 0.60, "budget": 0.65,
            "occasion": 0.55, "category": 0.35, "product_type": 0.60,
        }.get(attribute, 0.5)
    elif any(word in text for word in ("ring", "necklace", "bracelet", "earring", "jewelry", "watch")):
        relevance = {
            "occasion": 1.0, "style": 0.95, "material": 0.90, "color": 0.70,
            "feature": 0.65, "brand": 0.20, "budget": 0.65, "size_fit": 0.40,
            "use_case": 0.35, "category": 0.35, "product_type": 0.55,
        }.get(attribute, 0.5)
    elif any(word in text for word in ("dress", "shirt", "top", "pant", "jean", "short", "skirt", "jacket", "coat", "sweater", "hoodie", "clothing")):
        relevance = {
            "size_fit": 1.0, "style": 0.90, "material": 0.85, "color": 0.80,
            "occasion": 0.75, "feature": 0.70, "brand": 0.25, "budget": 0.60,
            "use_case": 0.65, "category": 0.35, "product_type": 0.60,
        }.get(attribute, 0.5)
    return relevance

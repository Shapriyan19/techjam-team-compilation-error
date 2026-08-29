from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from starter.phase5_config import PhaseFiveConfig
from starter.ranking.features import CatalogFeatureStore, ProductFeatures, ScoredCandidate
from starter.state import ConstraintStrength, SessionState


HEDGE_ATTRIBUTES = ("brand", "style", "material", "color")


@dataclass(frozen=True)
class AllocationDecision:
    recommendations: tuple[str, ...]
    activated: bool
    changed_positions: tuple[int, ...]
    hedge_attribute: str | None
    replacement_source_rank: int | None
    reason: str


class ConservativeTopKAllocator:
    """Protects ranks 1-8 and optionally uses rank 10 for one near-top hedge."""

    def __init__(self, store: CatalogFeatureStore | None, config: PhaseFiveConfig) -> None:
        self.store = store
        self.config = config

    def allocate(
        self,
        ranked: Sequence[ScoredCandidate],
        state: SessionState,
        top_k: int,
    ) -> AllocationDecision:
        control = tuple(candidate.parent_asin for candidate in ranked[:top_k])
        if self.config.topk_mode == "rank_only":
            return AllocationDecision(control, False, (), None, None, "rank-only control")
        if self.store is None or top_k < 10 or len(ranked) < 11:
            return AllocationDecision(control, False, (), None, None, "allocator prerequisites unavailable")

        protected = min(self.config.protected_top_n, top_k)
        top_products = [self.store.get(item.parent_asin) for item in ranked[:protected]]
        dominant = self._dominant_unknown_attribute(top_products, state)
        if dominant is None:
            return AllocationDecision(control, False, (), None, None, "no meaningful alternative axis")
        attribute, dominant_value = dominant
        baseline = ranked[top_k - 1]
        baseline_routes = _feature_value(baseline, "route_support")
        start = max(self.config.hedge_window_start, top_k + 1) - 1
        stop = min(self.config.hedge_window_end, len(ranked))
        for source_index in range(start, stop):
            candidate = ranked[source_index]
            if candidate.parent_asin in state.rejected_product_ids:
                continue
            if baseline.score - candidate.score > self.config.hedge_max_score_gap:
                continue
            if _feature_value(candidate, "conflict") < 0.0:
                continue
            if _feature_value(candidate, "rejection") < 0.0:
                continue
            if _feature_value(candidate, "route_support") < baseline_routes:
                continue
            product = self.store.get(candidate.parent_asin)
            values = _values_for(attribute, product)
            if not values or dominant_value in values:
                continue
            allocated = list(control)
            allocated[-1] = candidate.parent_asin
            if len(set(allocated)) != len(allocated):
                continue
            return AllocationDecision(
                recommendations=tuple(allocated),
                activated=True,
                changed_positions=(top_k,),
                hedge_attribute=attribute,
                replacement_source_rank=source_index + 1,
                reason="near-top alternative on uncertain dominant attribute",
            )
        return AllocationDecision(control, False, (), attribute, None, "no eligible near-top hedge")

    def _dominant_unknown_attribute(
        self,
        products: Sequence[ProductFeatures | None],
        state: SessionState,
    ) -> tuple[str, str] | None:
        minimum_count = max(2, len(products) - 2)
        for attribute in HEDGE_ATTRIBUTES:
            slot = state.slots.get(attribute)
            if slot is not None and slot.strength == ConstraintStrength.HARD:
                continue
            counts: Counter[str] = Counter()
            for product in products:
                counts.update(_values_for(attribute, product))
            if not counts:
                continue
            value, count = counts.most_common(1)[0]
            if count >= minimum_count:
                return attribute, value
        return None


def _values_for(attribute: str, product: ProductFeatures | None) -> tuple[str, ...]:
    if product is None:
        return ()
    return {
        "brand": product.brand_values,
        "style": product.style_values,
        "material": tuple(sorted(product.material_terms)),
        "color": tuple(sorted(product.color_terms)),
    }[attribute]


def _feature_value(candidate: ScoredCandidate, name: str) -> float:
    return dict(candidate.features).get(name, 0.0)

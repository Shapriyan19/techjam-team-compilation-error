from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from starter.ranking.features import CatalogFeatureStore, ScoredCandidate
from starter.runtime_config import AllocationConfig


@dataclass(frozen=True)
class AllocationResult:
    """Deterministic Top-K allocation with an auditable reason per slot."""

    recommendations: tuple[str, ...]
    slot_groups: tuple[str, ...]
    deferred_count: int
    distinct_groups: int

    def to_dict(self) -> dict:
        return {
            "recommendations": list(self.recommendations),
            "slot_groups": list(self.slot_groups),
            "deferred_count": self.deferred_count,
            "distinct_groups": self.distinct_groups,
        }


class TopKAllocator:
    """Hedge the tail of the Top-K across catalog-backed candidate groups.

    The head of the ranking is protected because MRR rewards an exact early
    rank. Only the slots after ``protected_slots`` are capped, and a candidate
    whose group metadata is missing is never capped: absent catalog values must
    stay neutral rather than becoming a penalty.
    """

    def __init__(self, store: CatalogFeatureStore, config: AllocationConfig) -> None:
        self.store = store
        self.config = config

    def allocate(self, candidates: Sequence[ScoredCandidate], top_k: int) -> AllocationResult:
        if top_k <= 0 or not candidates:
            return AllocationResult((), (), 0, 0)
        pool = list(candidates[: self.config.pool_size])
        selected: list[tuple[str, str]] = []
        deferred: list[ScoredCandidate] = []
        counts: Counter[str] = Counter()
        for candidate in pool:
            if len(selected) >= top_k:
                break
            group = self._group(candidate.parent_asin)
            protected = len(selected) < self.config.protected_slots
            if protected or not group or counts[group] < self.config.group_limit:
                selected.append((candidate.parent_asin, group))
                counts[group] += 1
            else:
                deferred.append(candidate)
        for candidate in (*deferred, *candidates[self.config.pool_size:]):
            if len(selected) >= top_k:
                break
            selected.append((candidate.parent_asin, self._group(candidate.parent_asin)))
        return AllocationResult(
            recommendations=tuple(identifier for identifier, _ in selected),
            slot_groups=tuple(group for _, group in selected),
            deferred_count=len(deferred),
            distinct_groups=len({group for _, group in selected if group}),
        )

    def _group(self, identifier: str) -> str:
        product = self.store.get(identifier)
        if product is None:
            return ""
        key = self.config.group_key
        if key == "brand":
            return product.brand_values[0] if product.brand_values else ""
        if key == "category":
            return product.category_values[0] if product.category_values else ""
        product_type = product.product_type_values[0] if product.product_type_values else ""
        if key == "product_type":
            return product_type
        brand = product.brand_values[0] if product.brand_values else ""
        if not brand and not product_type:
            return ""
        return f"{brand}|{product_type}"

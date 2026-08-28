from __future__ import annotations

import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from starter.ranking.config import FeatureWeights
from starter.ranking.evidence import CandidateEvidence, FreshCandidate
from starter.state import ConstraintStrength, SessionState, SlotValue


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
KNOWN_COLORS = frozenset({
    "black", "white", "blue", "red", "pink", "green", "brown", "gray", "grey",
    "purple", "yellow", "orange", "beige", "navy",
})
KNOWN_MATERIALS = frozenset({
    "cotton", "polyester", "nylon", "leather", "wool", "spandex", "silk", "rayon",
    "linen", "denim", "suede", "fabric", "rubber", "mesh",
})

KNOWN_USE_CASES = (
    "running", "walking", "hiking", "travel", "gym", "work", "everyday",
    "outdoor", "winter", "swimming", "cycling", "training",
)
KNOWN_STYLES = (
    "casual", "formal", "sporty", "athletic", "vintage", "retro", "minimalist",
    "elegant", "classic", "modern", "bohemian", "western",
)
KNOWN_OCCASIONS = (
    "wedding", "party", "interview", "funeral", "date night", "formal event",
    "vacation", "holiday", "christmas", "halloween", "prom", "birthday",
)
KNOWN_FEATURES = (
    "water resistant", "waterproof", "comfortable", "lightweight", "breathable",
    "durable", "insulated", "non slip", "slip resistant", "machine washable",
    "stretch", "supportive", "arch support", "pockets", "zipper", "hooded",
    "warm", "hypoallergenic", "uv protection", "moisture wicking", "quick dry",
    "adjustable", "reversible", "wrinkle resistant",
)
FIT_RE = re.compile(
    r"\b(?:extra\s+wide|wide|narrow|slim|regular|relaxed|oversized|compression|"
    r"loose|standard)\s+(?:fit|width)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProductFeatures:
    parent_asin: str
    all_terms: frozenset[str]
    category_terms: frozenset[str]
    brand_terms: frozenset[str]
    color_terms: frozenset[str]
    material_terms: frozenset[str]
    style_terms: frozenset[str]
    price: float | None
    category_values: tuple[str, ...]
    product_type_values: tuple[str, ...]
    brand_values: tuple[str, ...]
    use_case_values: tuple[str, ...]
    size_fit_values: tuple[str, ...]
    style_values: tuple[str, ...]
    occasion_values: tuple[str, ...]
    feature_values: tuple[str, ...]


@dataclass(frozen=True)
class ScoredCandidate:
    parent_asin: str
    score: float
    fresh_rank: int
    features: tuple[tuple[str, float], ...]


class CatalogFeatureStore:
    """Offset-backed catalog metadata with a bounded decoded-feature cache."""

    def __init__(self, catalog_path: str | Path, cache_size: int = 5000) -> None:
        self.catalog_path = Path(catalog_path)
        self.cache_size = max(1, int(cache_size))
        self._offsets: dict[str, tuple[int, int]] = {}
        self._cache: OrderedDict[str, ProductFeatures] = OrderedDict()
        with self.catalog_path.open("rb") as handle:
            while True:
                offset = handle.tell()
                line = handle.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                product = json.loads(line)
                identifier = str(product.get("parent_asin", "")).strip()
                if identifier:
                    self._offsets[identifier] = (offset, len(line))
        self._handle = self.catalog_path.open("rb")

    def __len__(self) -> int:
        return len(self._offsets)

    def close(self) -> None:
        self._handle.close()

    def get(self, identifier: str) -> ProductFeatures | None:
        identifier = str(identifier)
        cached = self._cache.pop(identifier, None)
        if cached is not None:
            self._cache[identifier] = cached
            return cached
        location = self._offsets.get(identifier)
        if location is None:
            return None
        offset, length = location
        self._handle.seek(offset)
        product = json.loads(self._handle.read(length))
        features = _product_features(product)
        self._cache[identifier] = features
        if len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return features


class DeterministicFeatureScorer:
    def __init__(self, store: CatalogFeatureStore, weights: FeatureWeights) -> None:
        self.store = store
        self.weights = weights

    def rank(
        self,
        fresh_candidates: list[FreshCandidate],
        state: SessionState,
        evidence: Mapping[str, CandidateEvidence] | None = None,
    ) -> list[ScoredCandidate]:
        scored: list[ScoredCandidate] = []
        for candidate in fresh_candidates:
            product = self.store.get(candidate.parent_asin)
            if product is None:
                continue
            feature_values = self._feature_values(candidate, product, state, evidence)
            total = sum(feature_values[name] * weight for name, weight in _weighted_items(self.weights))
            scored.append(
                ScoredCandidate(
                    parent_asin=candidate.parent_asin,
                    score=total,
                    fresh_rank=candidate.fused_rank,
                    features=tuple(feature_values.items()),
                )
            )
        return sorted(
            scored,
            key=lambda item: (-item.score, item.fresh_rank, item.parent_asin),
        )

    def _feature_values(
        self,
        candidate: FreshCandidate,
        product: ProductFeatures,
        state: SessionState,
        evidence: Mapping[str, CandidateEvidence] | None,
    ) -> dict[str, float]:
        conflict = 0.0
        category, category_conflict = _slot_agreement(
            state.slots.get("category"), product.category_terms | product.all_terms, product.category_terms
        )
        product_type, product_type_conflict = _slot_agreement(
            state.slots.get("product_type"), product.category_terms | product.all_terms, product.category_terms
        )
        brand, brand_conflict = _slot_agreement(
            state.slots.get("brand"), product.brand_terms | product.all_terms, product.brand_terms
        )
        color, color_conflict = _slot_agreement(
            state.slots.get("color"), product.color_terms, product.color_terms
        )
        material, material_conflict = _slot_agreement(
            state.slots.get("material"), product.material_terms, product.material_terms
        )
        use_case, use_case_conflict = _slot_agreement(
            state.slots.get("use_case"), product.all_terms, frozenset()
        )
        style, style_conflict = _slot_agreement(
            state.slots.get("style"), product.style_terms | product.all_terms, product.style_terms
        )
        occasion, occasion_conflict = _slot_agreement(
            state.slots.get("occasion"), product.all_terms, frozenset()
        )
        feature_overlap, feature_conflict = _slot_agreement(
            state.slots.get("features"), product.all_terms, frozenset()
        )
        conflict = max(
            category_conflict,
            product_type_conflict,
            brand_conflict,
            color_conflict,
            material_conflict,
            use_case_conflict,
            style_conflict,
            occasion_conflict,
            feature_conflict,
            _negative_conflict(state, product),
        )
        price, price_conflict = _price_compatibility(state, product.price)
        conflict = max(conflict, price_conflict)

        record = evidence.get(candidate.parent_asin) if evidence else None
        persistence = 0.0 if record is None else min(max(record.support_count - 1, 0), 4) / 4.0
        recency = 0.0 if record is None else 1.0 / (1.0 + record.recency)
        rejection = float(
            candidate.parent_asin in state.rejected_product_ids
            or (record is not None and record.rejected)
        )
        route_support = min(max(len(candidate.route_ranks) - 1, 0), 1)
        return {
            "retrieval_rank": 1.0 / math.sqrt(max(candidate.fused_rank, 1)),
            "route_support": float(route_support),
            "category": category,
            "product_type": product_type,
            "brand": brand,
            "color": color,
            "material": material,
            "use_case": use_case,
            "style": style,
            "occasion": occasion,
            "feature_overlap": feature_overlap,
            "price": price,
            "persistence": persistence,
            "recency": recency,
            "rejection": -rejection,
            "conflict": -conflict,
        }


def _weighted_items(weights: FeatureWeights) -> tuple[tuple[str, float], ...]:
    return tuple((name, float(getattr(weights, name))) for name in weights.__dataclass_fields__)


def _slot_agreement(
    slot: SlotValue | None,
    searchable_terms: frozenset[str],
    reliable_terms: frozenset[str],
) -> tuple[float, float]:
    if slot is None:
        return 0.0, 0.0
    values = slot.value if isinstance(slot.value, tuple) else (slot.value,)
    matches = [bool(_tokens(value) and _tokens(value).issubset(searchable_terms)) for value in values]
    if any(matches):
        strength = 1.0 if slot.strength == ConstraintStrength.HARD else 0.65
        return strength * (sum(matches) / len(matches)), 0.0
    if slot.strength == ConstraintStrength.HARD and reliable_terms:
        return 0.0, 1.0
    return 0.0, 0.0


def _negative_conflict(state: SessionState, product: ProductFeatures) -> float:
    for values in state.negative_preferences.values():
        for value in values:
            terms = _tokens(value)
            if terms and terms.issubset(product.all_terms):
                return 1.0
    return 0.0


def _price_compatibility(state: SessionState, price: float | None) -> tuple[float, float]:
    minimum = state.slots.get("budget_min")
    maximum = state.slots.get("budget_max")
    if minimum is None and maximum is None:
        return 0.0, 0.0
    if price is None:
        return 0.0, 0.0
    if minimum is not None and price < float(minimum.value):
        return 0.0, 1.0
    if maximum is not None and price > float(maximum.value):
        return 0.0, 1.0
    return 1.0, 0.0


def _product_features(product: dict) -> ProductFeatures:
    identifier = str(product.get("parent_asin", "")).strip()
    categories = _text(product.get("categories"))
    title = _text(product.get("title"))
    features = _text(product.get("features"))
    description = _text(product.get("description"))
    details = product.get("details") if isinstance(product.get("details"), dict) else {}
    detail_text = _text(details)
    store = _text(product.get("store"))
    corpus = " ".join((title, categories, features, description, detail_text, store))
    normalized_corpus = _normalize_phrase(corpus)
    all_terms = _tokens(corpus)
    brand_detail = " ".join(
        str(value)
        for key, value in details.items()
        if str(key).casefold().strip() in {"brand", "brand name", "manufacturer"}
    )
    style_detail = " ".join(
        str(value)
        for key, value in details.items()
        if str(key).casefold().strip() in {"style", "pattern", "sport type", "closure type"}
    )
    color_terms = frozenset(term for term in all_terms if term in KNOWN_COLORS)
    material_terms = frozenset(term for term in all_terms if term in KNOWN_MATERIALS)
    category_values = _category_values(product.get("categories"))
    product_type_values = category_values[-1:] if category_values else ()
    brand_values = _brand_values(store, brand_detail)
    return ProductFeatures(
        parent_asin=identifier,
        all_terms=all_terms,
        category_terms=_tokens(categories),
        brand_terms=_tokens(" ".join((store, brand_detail))),
        color_terms=color_terms,
        material_terms=material_terms,
        style_terms=_tokens(style_detail),
        price=_price(product.get("price")),
        category_values=category_values,
        product_type_values=product_type_values,
        brand_values=brand_values,
        use_case_values=_phrases_present(normalized_corpus, KNOWN_USE_CASES),
        size_fit_values=tuple(dict.fromkeys(_normalize_phrase(value) for value in FIT_RE.findall(corpus))),
        style_values=_phrases_present(normalized_corpus, KNOWN_STYLES),
        occasion_values=_phrases_present(normalized_corpus, KNOWN_OCCASIONS),
        feature_values=_phrases_present(normalized_corpus, KNOWN_FEATURES),
    )


def _category_values(value: object) -> tuple[str, ...]:
    values = value if isinstance(value, list) else [value]
    excluded = {"", "clothing shoes jewelry", "clothing shoes and jewelry"}
    normalized = [
        phrase
        for item in values
        if (phrase := _normalize_phrase(item)) not in excluded
    ]
    return tuple(dict.fromkeys(normalized[-3:]))


def _brand_values(store: str, brand_detail: str) -> tuple[str, ...]:
    value = _normalize_phrase(brand_detail or store)
    if not value or value in {"generic", "unknown"}:
        return ()
    return (value,)


def _phrases_present(corpus: str, phrases: tuple[str, ...]) -> tuple[str, ...]:
    present = [
        phrase
        for phrase in phrases
        if re.search(r"(?<![a-z0-9])" + re.escape(phrase).replace(r"\ ", r"\s+") + r"(?![a-z0-9])", corpus)
    ]
    return tuple(present)


def _normalize_phrase(value: object) -> str:
    return " ".join(TOKEN_RE.findall(str(value).casefold()))


def _tokens(value: object) -> frozenset[str]:
    return frozenset(_singular(token.casefold()) for token in TOKEN_RE.findall(str(value)))


def _singular(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _price(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

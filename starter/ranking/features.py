from __future__ import annotations

import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from starter.ranking.config import FeatureWeights
from starter.ranking.evidence import CandidateEvidence, FreshCandidate
from starter.ranking.rarity import TermRarity
from starter.state import ConstraintStrength, SessionState, SlotValue


_NON_WEIGHT_FIELDS = frozenset({"route_support_mode"})

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
# Longest controlled phrase, used to bound the n-gram window that replaces one
# regular-expression pass per phrase. The phrase vocabularies are lowercase
# alphanumeric words, so contiguous n-gram membership over the normalized corpus
# is exactly equivalent to the previous boundary-anchored search.
_PHRASE_VOCABULARIES = (KNOWN_USE_CASES, KNOWN_STYLES, KNOWN_OCCASIONS, KNOWN_FEATURES)
# Number of fully-matched fragments at which the conjunction signal saturates.
_FRAGMENT_SATURATION = 3.0
# Extra credit when the phrase appears verbatim, on top of graded coverage.
_EXACT_PHRASE_BONUS = 0.5
_MAX_PHRASE_TOKENS = max(
    len(phrase.split()) for vocabulary in _PHRASE_VOCABULARIES for phrase in vocabulary
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
    # Whitespace-normalized, lowercased catalog text. Kept as a string (not just
    # the n-gram set) because shopper fragments run longer than the n-gram cap.
    normalized_text: str


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
        self._descriptions: OrderedDict[str, dict] = OrderedDict()
        self.cache_hits = 0
        self.cache_misses = 0
        self.unknown_lookups = 0
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

    def cache_statistics(self) -> dict:
        lookups = self.cache_hits + self.cache_misses
        return {
            "cache_size": self.cache_size,
            "cached_products": len(self._cache),
            "hits": self.cache_hits,
            "misses": self.cache_misses,
            "unknown_lookups": self.unknown_lookups,
            "hit_rate": round(self.cache_hits / lookups, 6) if lookups else None,
        }

    def get(self, identifier: str) -> ProductFeatures | None:
        identifier = str(identifier)
        cached = self._cache.pop(identifier, None)
        if cached is not None:
            self._cache[identifier] = cached
            self.cache_hits += 1
            return cached
        product = self._read(identifier)
        if product is None:
            self.unknown_lookups += 1
            return None
        self.cache_misses += 1
        features = _product_features(product)
        self._cache[identifier] = features
        if len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return features


    def describe(self, identifier: str) -> dict | None:
        """Compact catalog record for optional prompt construction."""
        identifier = str(identifier)
        cached = self._descriptions.pop(identifier, None)
        if cached is not None:
            self._descriptions[identifier] = cached
            return cached
        product = self._read(identifier)
        if product is None:
            return None
        categories = _category_values(product.get("categories"))
        description = {
            "parent_asin": identifier,
            "title": _compact(_text(product.get("title")), 140),
            "store": _compact(_text(product.get("store")), 40),
            "category": categories[-1] if categories else "",
            "price": _price(product.get("price")),
            "features": _compact(_text(product.get("features")), 160),
        }
        self._descriptions[identifier] = description
        if len(self._descriptions) > min(self.cache_size, 2000):
            self._descriptions.popitem(last=False)
        return description

    def _read(self, identifier: str) -> dict | None:
        location = self._offsets.get(identifier)
        if location is None:
            return None
        offset, length = location
        self._handle.seek(offset)
        return json.loads(self._handle.read(length))


class DeterministicFeatureScorer:
    def __init__(
        self,
        store: CatalogFeatureStore,
        weights: FeatureWeights,
        rarity: "TermRarity | None" = None,
    ) -> None:
        self.store = store
        self.weights = weights
        self.rarity = rarity
        self._weighted_items = _weighted_items(weights)

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
            total = sum(feature_values[name] * weight for name, weight in self._weighted_items)
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
        route_support = _route_support(candidate.route_ranks, self.weights.route_support_mode)
        return {
            "retrieval_rank": 1.0 / math.sqrt(max(candidate.fused_rank, 1)),
            "route_support": route_support,
            "category": category,
            "product_type": product_type,
            "brand": brand,
            "color": color,
            "material": material,
            "use_case": use_case,
            "style": style,
            "occasion": occasion,
            "feature_overlap": feature_overlap,
            "fragment_agreement": _fragment_agreement(state, product, self.rarity),
            "price": price,
            "persistence": persistence,
            "recency": recency,
            "rejection": -rejection,
            "conflict": -conflict,
        }


def _weighted_items(weights: FeatureWeights) -> tuple[tuple[str, float], ...]:
    return tuple(
        (name, float(getattr(weights, name)))
        for name in weights.__dataclass_fields__
        if name not in _NON_WEIGHT_FIELDS
    )


def _route_support(route_ranks: tuple[tuple[str, int], ...], mode: str) -> float:
    if mode == "continuous":
        # Reciprocal rank per route rewards a candidate that a route ranked
        # highly more than one that barely made that route's Top-N, unlike
        # the binary mode below, which awards the same bonus for any 2+ route
        # presence regardless of how strong each route's own ranking was.
        return min(sum(1.0 / rank for _, rank in route_ranks if rank > 0), 1.0)
    return float(min(max(len(route_ranks) - 1, 0), 1))


def _slot_agreement(
    slot: SlotValue | None,
    searchable_terms: frozenset[str],
    reliable_terms: frozenset[str],
) -> tuple[float, float]:
    if slot is None:
        return 0.0, 0.0
    values = slot.value if isinstance(slot.value, tuple) else (slot.value,)
    value_terms = [_tokens(value) for value in values]
    matches = [bool(terms and terms.issubset(searchable_terms)) for terms in value_terms]
    if any(matches):
        strength = 1.0 if slot.strength == ConstraintStrength.HARD else 0.65
        # Rarity-weighting this coverage was measured and reverted: every color and
        # material slot in the evaluation data holds exactly one value, so the ratio
        # is always 1/1 and the weighting is inert. See docs/EXPERIMENT_LOG.md P18-E002.
        return strength * (sum(matches) / len(matches)), 0.0
    if slot.strength == ConstraintStrength.HARD and reliable_terms:
        return 0.0, 1.0
    return 0.0, 0.0


def _fragment_agreement(
    state: SessionState,
    product: ProductFeatures,
    rarity: TermRarity | None = None,
) -> float:
    """How well this product accounts for the shopper's literal statements.

    Exact phrase presence scores 1.0. Anything else falls back to squared token
    coverage, so a reworded fragment still earns partial credit while incidental
    overlap on common words ("imported", "closure") stays near zero. The squaring
    matters: without it every candidate picks up a similar floor from boilerplate
    and the feature stops discriminating.

    Deliberately a score, never a filter - Amazon metadata is patchy enough that
    excluding non-matches would drop the true target whenever one phrase is
    simply absent from its listing.
    """
    fragments = state.verbatim_fragments
    if not fragments:
        return 0.0
    total = 0.0
    for fragment in fragments:
        normalized = _normalize_phrase(fragment)
        if not normalized:
            continue
        tokens = _tokens(fragment)
        if not tokens:
            continue
        # Graded rather than binary. An exact phrase hit is worth more, but a
        # flat 1.0 for it would tie together every product sharing that phrase
        # (often dozens), losing the ability to order within the tie. Squared
        # coverage keeps incidental common-word overlap near zero.
        matched = tokens & product.all_terms
        if rarity is None:
            covered = len(matched) / len(tokens)
        else:
            # Rarity-weighted coverage. Counting tokens equally made the feature
            # tie across a crowded category - every candidate matches "cotton"
            # and "women". Weighting by IDF means accounting for the one rare
            # word the shopper used outscores matching several common ones,
            # which is the distinction the ranker previously could not make.
            # Same [0, 1] scale as the unweighted ratio, so the saturation
            # constant and phrase bonus below keep their calibration.
            denominator = rarity.mass(tokens)
            covered = rarity.mass(matched) / denominator if denominator > 0 else 0.0
        total += covered * covered
        if normalized in product.normalized_text:
            total += _EXACT_PHRASE_BONUS
    # Saturating sum rather than a mean: matching three stated phrases is much
    # stronger evidence than matching one, but averaging would score 1-of-1
    # above 3-of-4. Normalizing by a constant keeps the feature bounded while
    # still rewarding accumulated agreement.
    return min(total / _FRAGMENT_SATURATION, 1.0)


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
    corpus_ngrams = _corpus_ngrams(normalized_corpus)
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
        use_case_values=_phrases_present(corpus_ngrams, KNOWN_USE_CASES),
        size_fit_values=tuple(dict.fromkeys(_normalize_phrase(value) for value in FIT_RE.findall(corpus))),
        style_values=_phrases_present(corpus_ngrams, KNOWN_STYLES),
        occasion_values=_phrases_present(corpus_ngrams, KNOWN_OCCASIONS),
        feature_values=_phrases_present(corpus_ngrams, KNOWN_FEATURES),
        normalized_text=normalized_corpus,
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


def _corpus_ngrams(corpus: str) -> frozenset[str]:
    tokens = corpus.split()
    grams = set(tokens)
    for size in range(2, _MAX_PHRASE_TOKENS + 1):
        grams.update(
            " ".join(tokens[start:start + size])
            for start in range(len(tokens) - size + 1)
        )
    return frozenset(grams)


def _phrases_present(ngrams: frozenset[str], phrases: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(phrase for phrase in phrases if phrase in ngrams)


def _compact(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    return text[:limit].rstrip()


def _normalize_phrase(value: object) -> str:
    return " ".join(TOKEN_RE.findall(str(value).casefold()))


def _tokens(value: object) -> frozenset[str]:
    return frozenset(singular_token(token.casefold()) for token in TOKEN_RE.findall(str(value)))


@lru_cache(maxsize=131072)
def singular_token(token: str) -> str:
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

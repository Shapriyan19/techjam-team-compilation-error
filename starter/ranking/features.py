from __future__ import annotations

import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, NamedTuple

from starter.ranking.config import FeatureWeights
from starter.improvements import ImprovementConfig
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
    title: str
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
<<<<<<< Updated upstream
=======
    # Whitespace-normalized, lowercased catalog text. Kept as a string (not just
    # the n-gram set) because shopper fragments run longer than the n-gram cap.
    normalized_text: str
    identity_terms: frozenset[str] = frozenset()
    specific_category_terms: frozenset[str] = frozenset()
>>>>>>> Stashed changes


@dataclass(frozen=True)
class ScoredCandidate:
    parent_asin: str
    score: float
    fresh_rank: int
    features: tuple[tuple[str, float], ...]


class CompiledSlot(NamedTuple):
    values: tuple[frozenset[str], ...]
    hard: bool


@dataclass(frozen=True)
class CompiledRankingState:
    slots: Mapping[str, CompiledSlot]
    negative_terms: tuple[frozenset[str], ...]
    budget_min: float | None
    budget_max: float | None
    rejected_product_ids: frozenset[str]


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

    def contains(self, identifier: str) -> bool:
        return str(identifier) in self._offsets

    def retain_all(self) -> None:
        """Keep decoded immutable records once seen, without changing extraction."""
        self.cache_size = max(self.cache_size, len(self._offsets))

    @property
    def cached_record_count(self) -> int:
        return len(self._cache)

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
    def __init__(self, store: CatalogFeatureStore, weights: FeatureWeights,
                 improvements: ImprovementConfig | None = None) -> None:
        self.store = store
        self.weights = weights
<<<<<<< Updated upstream
        self._weighted_feature_items = _weighted_items(weights)
=======
        self.improvements = improvements or ImprovementConfig()
        self._weighted_items = _weighted_items(weights)
>>>>>>> Stashed changes

    def rank(
        self,
        fresh_candidates: list[FreshCandidate],
        state: SessionState,
        evidence: Mapping[str, CandidateEvidence] | None = None,
    ) -> list[ScoredCandidate]:
        scored: list[ScoredCandidate] = []
<<<<<<< Updated upstream
        compiled_state = _compile_state(state)
=======
        # This cache lives for exactly one rank call: state changes cannot leave
        # stale tokens or fragments behind. No candidate-dependent values enter it.
        prepared = _prepare_state(state) if self.improvements.optimize else None
>>>>>>> Stashed changes
        for candidate in fresh_candidates:
            product = self.store.get(candidate.parent_asin)
            if product is None:
                continue
<<<<<<< Updated upstream
            feature_values = self._feature_values(candidate, product, compiled_state, evidence)
            total = sum(feature_values[name] * weight for name, weight in self._weighted_feature_items)
=======
            feature_values = self._feature_values(candidate, product, state, evidence, prepared)
            total = sum(feature_values[name] * weight for name, weight in self._weighted_items)
            if self.improvements.shoppilot_features:
                total += _shoppilot_bonus(state, product, self.improvements)
>>>>>>> Stashed changes
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
        state: CompiledRankingState,
        evidence: Mapping[str, CandidateEvidence] | None,
        prepared: dict | None = None,
    ) -> dict[str, float]:
        def agreement(slot, searchable, reliable):
            return _slot_agreement(slot, searchable, reliable, None if prepared is None else prepared["tokens"])
        conflict = 0.0
        category, category_conflict = agreement(
            state.slots.get("category"), product.category_terms | product.all_terms, product.category_terms
        )
        product_type, product_type_conflict = agreement(
            state.slots.get("product_type"), product.category_terms | product.all_terms, product.category_terms
        )
        if self.improvements.category_evidence:
            checked_category = _category_agreement(state.slots.get("category"), product)
            checked_type = _category_agreement(state.slots.get("product_type"), product)
            if not self.improvements.category_guard_only or checked_category[1]:
                category, category_conflict = checked_category
            if not self.improvements.category_guard_only or checked_type[1]:
                product_type, product_type_conflict = checked_type
        brand, brand_conflict = agreement(
            state.slots.get("brand"), product.brand_terms | product.all_terms, product.brand_terms
        )
        color, color_conflict = agreement(
            state.slots.get("color"), product.color_terms, product.color_terms
        )
        material, material_conflict = agreement(
            state.slots.get("material"), product.material_terms, product.material_terms
        )
        use_case, use_case_conflict = agreement(
            state.slots.get("use_case"), product.all_terms, frozenset()
        )
        style, style_conflict = agreement(
            state.slots.get("style"), product.style_terms | product.all_terms, product.style_terms
        )
        occasion, occasion_conflict = agreement(
            state.slots.get("occasion"), product.all_terms, frozenset()
        )
        feature_overlap, feature_conflict = agreement(
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
<<<<<<< Updated upstream
            _negative_conflict_compiled(state, product),
=======
            _negative_conflict(state, product, prepared),
>>>>>>> Stashed changes
        )
        price, price_conflict = _price_compatibility_compiled(state, product.price)
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
<<<<<<< Updated upstream
=======
            "fragment_agreement": _fragment_agreement(state, product, prepared),
>>>>>>> Stashed changes
            "price": price,
            "persistence": persistence,
            "recency": recency,
            "rejection": -rejection,
            "conflict": -conflict,
        }


def _weighted_items(weights: FeatureWeights) -> tuple[tuple[str, float], ...]:
    return tuple((name, float(getattr(weights, name))) for name in weights.__dataclass_fields__)


def _compile_state(state: SessionState) -> CompiledRankingState:
    slots: dict[str, CompiledSlot] = {}
    for name, slot in state.slots.items():
        raw_values = slot.value if isinstance(slot.value, tuple) else (slot.value,)
        slots[name] = CompiledSlot(
            tuple(_tokens(value) for value in raw_values),
            slot.strength == ConstraintStrength.HARD,
        )
    negative_terms = tuple(
        terms
        for values in state.negative_preferences.values()
        for value in values
        if (terms := _tokens(value))
    )
    minimum = state.slots.get("budget_min")
    maximum = state.slots.get("budget_max")
    return CompiledRankingState(
        slots=slots,
        negative_terms=negative_terms,
        budget_min=None if minimum is None else float(minimum.value),
        budget_max=None if maximum is None else float(maximum.value),
        rejected_product_ids=frozenset(state.rejected_product_ids),
    )


<<<<<<< Updated upstream
=======
def _shoppilot_bonus(state: SessionState, product: ProductFeatures, config: ImprovementConfig) -> float:
    """Small conjunction bonuses inspired by ShopPilot's evidence stack.

    These bonuses are deliberately bounded so lexical/facet retrieval remains
    the primary signal. They reward exact agreement only for known active slots.
    """
    active = []
    for name in ("category", "product_type", "brand", "color", "material", "style", "use_case", "occasion", "features"):
        slot = state.slots.get(name)
        if slot is None:
            continue
        values = slot.value if isinstance(slot.value, tuple) else (slot.value,)
        terms = [_tokens(str(value)) for value in values]
        if not terms:
            continue
        active.append((name, terms, product))
    if not active:
        return 0.0
    matched = 0
    for name, values, item in active:
        searchable = item.all_terms
        if name == "color":
            searchable = item.color_terms
        elif name == "material":
            searchable = item.material_terms
        elif name == "brand":
            searchable = item.brand_terms | item.all_terms
        if any(value and value.issubset(searchable) for value in values):
            matched += 1
    coverage = matched / len(active)
    bonus = config.shoppilot_coverage_bonus * coverage
    if matched == len(active) and len(active) >= 2:
        bonus += config.shoppilot_full_bonus  # bounded full-match conjunction bonus
    category = state.slots.get("category")
    if category is not None and _tokens(str(category.value)).issubset(_tokens(product.normalized_text)):
        bonus += config.shoppilot_category_bonus  # category-tail/title exactness
    return min(bonus, config.shoppilot_coverage_bonus + config.shoppilot_full_bonus + config.shoppilot_category_bonus)


def _route_support(route_ranks: tuple[tuple[str, int], ...], mode: str) -> float:
    if mode == "continuous":
        # Reciprocal rank per route rewards a candidate that a route ranked
        # highly more than one that barely made that route's Top-N, unlike
        # the binary mode below, which awards the same bonus for any 2+ route
        # presence regardless of how strong each route's own ranking was.
        return min(sum(1.0 / rank for _, rank in route_ranks if rank > 0), 1.0)
    return float(min(max(len(route_ranks) - 1, 0), 1))


>>>>>>> Stashed changes
def _slot_agreement(
    slot: CompiledSlot | None,
    searchable_terms: frozenset[str],
    reliable_terms: frozenset[str],
    token_cache: dict | None = None,
) -> tuple[float, float]:
    if slot is None:
        return 0.0, 0.0
<<<<<<< Updated upstream
    matches = [bool(value and value.issubset(searchable_terms)) for value in slot.values]
=======
    values = slot.value if isinstance(slot.value, tuple) else (slot.value,)
    value_terms = [token_cache[str(value)] if token_cache is not None else _tokens(value) for value in values]
    matches = [bool(terms and terms.issubset(searchable_terms)) for terms in value_terms]
>>>>>>> Stashed changes
    if any(matches):
        strength = 1.0 if slot.hard else 0.65
        return strength * (sum(matches) / len(matches)), 0.0
    if slot.hard and reliable_terms:
        return 0.0, 1.0
    return 0.0, 0.0


<<<<<<< Updated upstream
def _negative_conflict(state: SessionState, product: ProductFeatures) -> float:
    for values in state.negative_preferences.values():
        for value in values:
            terms = _tokens(value)
            if terms and terms.issubset(product.all_terms):
                return 1.0
    return 0.0


def _negative_conflict_compiled(state: CompiledRankingState, product: ProductFeatures) -> float:
    return float(any(terms.issubset(product.all_terms) for terms in state.negative_terms))
=======
def _fragment_agreement(state: SessionState, product: ProductFeatures, prepared: dict | None = None) -> float:
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
    compiled = prepared["fragments"] if prepared is not None else [
        (_normalize_phrase(fragment), _tokens(fragment)) for fragment in fragments
    ]
    for normalized, tokens in compiled:
        if not normalized:
            continue
        if not tokens:
            continue
        # Graded rather than binary. An exact phrase hit is worth more, but a
        # flat 1.0 for it would tie together every product sharing that phrase
        # (often dozens), losing the ability to order within the tie. Squared
        # coverage keeps incidental common-word overlap near zero.
        covered = len(tokens & product.all_terms) / len(tokens)
        total += covered * covered
        if normalized in product.normalized_text:
            total += _EXACT_PHRASE_BONUS
    # Saturating sum rather than a mean: matching three stated phrases is much
    # stronger evidence than matching one, but averaging would score 1-of-1
    # above 3-of-4. Normalizing by a constant keeps the feature bounded while
    # still rewarding accumulated agreement.
    return min(total / _FRAGMENT_SATURATION, 1.0)


def _negative_conflict(state: SessionState, product: ProductFeatures, prepared: dict | None = None) -> float:
    negatives = prepared["negatives"] if prepared is not None else [
        _tokens(value) for values in state.negative_preferences.values() for value in values
    ]
    for terms in negatives:
        if terms and terms.issubset(product.all_terms):
            return 1.0
    return 0.0


def _prepare_state(state: SessionState) -> dict:
    return {
        "tokens": {str(value): _tokens(value) for slot in state.slots.values()
                   for value in (slot.value if isinstance(slot.value, tuple) else (slot.value,))},
        "fragments": tuple((_normalize_phrase(value), _tokens(value)) for value in state.verbatim_fragments),
        "negatives": tuple(_tokens(value) for values in state.negative_preferences.values() for value in values),
    }


_FAMILIES = {
    "footwear": frozenset("shoe sneaker boot sandal heel loafer slipper footwear moccasin".split()),
    "bottoms": frozenset("pant short legging trouser jean skirt".split()),
    "tops": frozenset("shirt blouse tee jacket coat sweater hoodie sweatshirt".split()),
    "jewelry": frozenset("necklace bracelet earring ring pendant jewelry".split()),
    "bags": frozenset("bag backpack handbag purse luggage".split()),
}


def _families(terms: frozenset[str]) -> set[str]:
    return {name for name, vocabulary in _FAMILIES.items() if terms & vocabulary}


def _category_agreement(slot: SlotValue | None, product: ProductFeatures) -> tuple[float, float]:
    if slot is None:
        return 0.0, 0.0
    wanted = _tokens(slot.value)
    requested_family = _families(wanted)
    # Specific taxonomy is more reliable than incidental nouns in a long title.
    actual_family = _families(product.specific_category_terms) or _families(product.identity_terms)
    contradiction = bool(requested_family and actual_family and requested_family.isdisjoint(actual_family))
    if contradiction:
        return 0.0, float(slot.strength == ConstraintStrength.HARD)
    matched = wanted.issubset(product.identity_terms)
    # A generic request for shoes may legitimately match a sneaker/boot listing.
    if len(wanted) == 1 and requested_family and requested_family == actual_family:
        matched = True
    return (1.0 if slot.strength == ConstraintStrength.HARD else 0.65) * float(matched), 0.0
>>>>>>> Stashed changes


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


def _price_compatibility_compiled(
    state: CompiledRankingState,
    price: float | None,
) -> tuple[float, float]:
    if state.budget_min is None and state.budget_max is None:
        return 0.0, 0.0
    if price is None:
        return 0.0, 0.0
    if state.budget_min is not None and price < state.budget_min:
        return 0.0, 1.0
    if state.budget_max is not None and price > state.budget_max:
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
        title=title,
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
<<<<<<< Updated upstream
        style_values=_phrases_present(normalized_corpus, KNOWN_STYLES),
        occasion_values=_phrases_present(normalized_corpus, KNOWN_OCCASIONS),
        feature_values=_phrases_present(normalized_corpus, KNOWN_FEATURES),
=======
        style_values=_phrases_present(corpus_ngrams, KNOWN_STYLES),
        occasion_values=_phrases_present(corpus_ngrams, KNOWN_OCCASIONS),
        feature_values=_phrases_present(corpus_ngrams, KNOWN_FEATURES),
        normalized_text=normalized_corpus,
        identity_terms=_tokens(title + " " + " ".join(category_values)),
        specific_category_terms=_tokens(" ".join(category_values)),
>>>>>>> Stashed changes
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

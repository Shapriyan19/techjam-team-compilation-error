from __future__ import annotations

import hashlib
import json
import statistics
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from starter.phase6_config import PhaseSixConfig
from starter.ranking.features import CatalogFeatureStore, ProductFeatures, ScoredCandidate
from starter.retrieval.dense import DenseRetriever
from starter.state import SessionState


@dataclass(frozen=True)
class SemanticCandidate:
    parent_asin: str
    title: str
    categories: tuple[str, ...]
    brand: tuple[str, ...]
    features: tuple[str, ...]
    price: float | None


@dataclass(frozen=True)
class SemanticRequest:
    rewritten_query: str
    slots: tuple[tuple[str, object, str], ...]
    negatives: tuple[tuple[str, tuple[str, ...]], ...]
    candidates: tuple[SemanticCandidate, ...]


class SemanticProvider(Protocol):
    version: str

    def rank(self, request: SemanticRequest) -> Sequence[str]: ...


class CatalogEncoderSemanticProvider:
    """Offline shortlist-only use of the packaged catalog encoder; never retrieves globally."""

    version = "catalog_random_indexing_v1_shortlist_rerank_v1"

    def __init__(self, artifact_dir: Path, catalog_path: Path, validate_checksums: bool = True) -> None:
        self._encoder = DenseRetriever(
            artifact_dir,
            catalog_path,
            validate_checksums=validate_checksums,
        )
        self._row_by_id = {
            str(identifier): index
            for index, identifier in enumerate(self._encoder.product_ids)
        }

    def rank(self, request: SemanticRequest) -> Sequence[str]:
        vector = self._encoder.encode(request.rewritten_query)
        if vector is None:
            raise RuntimeError("semantic query contains no known encoder terms")
        identifiers = [item.parent_asin for item in request.candidates]
        rows = [self._row_by_id[identifier] for identifier in identifiers]
        scores = self._encoder._np.asarray(
            self._encoder.embeddings[rows] @ vector,
            dtype=self._encoder._np.float32,
        )
        return [
            identifiers[index]
            for index in sorted(range(len(identifiers)), key=lambda index: (-float(scores[index]), index))
        ]


class SemanticShortlistReranker:
    def __init__(
        self,
        provider: SemanticProvider | None,
        store: CatalogFeatureStore | None,
        config: PhaseSixConfig,
    ) -> None:
        self.provider = provider
        self.store = store
        self.config = config
        self._cache: OrderedDict[str, tuple[str, ...]] = OrderedDict()
        self.calls = 0
        self.cache_hits = 0
        self.failures = 0
        self.fallbacks = 0
        self.latencies_ms: list[float] = []
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def rerank(
        self,
        ranked: Sequence[ScoredCandidate],
        state: SessionState,
        rewritten_query: str,
    ) -> list[ScoredCandidate]:
        control = list(ranked)
        if self.provider is None or self.store is None or len(control) < 2:
            self.fallbacks += int(self.config.semantic_rerank_mode == "optional")
            return control
        shortlist = control[: self.config.semantic_rerank_k]
        request = self._request(shortlist, state, rewritten_query)
        key = _request_key(request, self.provider.version)
        cached = self._cache.pop(key, None)
        if cached is not None:
            self._cache[key] = cached
            self.cache_hits += 1
            semantic_order = list(cached)
        else:
            started = time.perf_counter()
            try:
                raw_order = self.provider.rank(request)
                semantic_order = _validated_order(raw_order, [item.parent_asin for item in shortlist])
                self.calls += 1
                self.latencies_ms.append((time.perf_counter() - started) * 1000.0)
            except Exception:
                self.failures += 1
                self.fallbacks += 1
                return control
            self._cache[key] = tuple(semantic_order)
            if len(self._cache) > self.config.semantic_cache_size:
                self._cache.popitem(last=False)
        return self._blend(control, semantic_order)

    def _request(
        self,
        shortlist: Sequence[ScoredCandidate],
        state: SessionState,
        rewritten_query: str,
    ) -> SemanticRequest:
        candidates: list[SemanticCandidate] = []
        for item in shortlist:
            product = self.store.get(item.parent_asin)
            candidates.append(_semantic_candidate(item.parent_asin, product))
        slots = tuple(
            (name, slot.value, slot.strength.value)
            for name, slot in sorted(state.slots.items())
        )
        negatives = tuple(
            (name, tuple(sorted(values)))
            for name, values in sorted(state.negative_preferences.items())
        )
        return SemanticRequest(rewritten_query, slots, negatives, tuple(candidates))

    def _blend(
        self,
        control: list[ScoredCandidate],
        semantic_order: Sequence[str],
    ) -> list[ScoredCandidate]:
        shortlist_count = min(self.config.semantic_rerank_k, len(control))
        protected_count = min(self.config.semantic_protected_top_n, shortlist_count)
        protected = control[:protected_count]
        semantic_rank = {identifier: rank for rank, identifier in enumerate(semantic_order, 1)}
        remainder = control[protected_count:shortlist_count]
        eligible = []
        ineligible = []
        for deterministic_rank, candidate in enumerate(remainder, protected_count + 1):
            feature_map = dict(candidate.features)
            if feature_map.get("conflict", 0.0) < 0.0 or feature_map.get("rejection", 0.0) < 0.0:
                ineligible.append((deterministic_rank, candidate))
                continue
            rank = semantic_rank.get(candidate.parent_asin, shortlist_count + 1)
            fused = (
                1.0 / (self.config.semantic_rrf_k + deterministic_rank)
                + self.config.semantic_weight / (self.config.semantic_rrf_k + rank)
            )
            eligible.append((fused, deterministic_rank, candidate))
        eligible.sort(key=lambda item: (-item[0], item[1], item[2].parent_asin))
        ineligible_by_rank = {rank: candidate for rank, candidate in ineligible}
        eligible_iter = iter(item[2] for item in eligible)
        blended_segment = [
            ineligible_by_rank.get(rank) or next(eligible_iter)
            for rank in range(protected_count + 1, shortlist_count + 1)
        ]
        return [*protected, *blended_segment, *control[shortlist_count:]]

    def stats(self) -> dict:
        ordered = sorted(self.latencies_ms)
        p95 = None if not ordered else ordered[round((len(ordered) - 1) * 0.95)]
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "failures": self.failures,
            "fallbacks": self.fallbacks,
            "average_latency_ms": None if not ordered else round(statistics.fmean(ordered), 6),
            "p95_latency_ms": None if p95 is None else round(p95, 6),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "estimated_cost_usd": 0.0,
        }


def _validated_order(raw_order: Sequence[str], supplied: Sequence[str]) -> list[str]:
    allowed = set(supplied)
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_order:
        identifier = str(raw)
        if identifier in allowed and identifier not in seen:
            seen.add(identifier)
            result.append(identifier)
    result.extend(identifier for identifier in supplied if identifier not in seen)
    return result


def _semantic_candidate(identifier: str, product: ProductFeatures | None) -> SemanticCandidate:
    if product is None:
        return SemanticCandidate(identifier, "", (), (), (), None)
    features = tuple(dict.fromkeys((
        *product.use_case_values,
        *product.style_values,
        *product.occasion_values,
        *product.feature_values,
        *sorted(product.color_terms),
        *sorted(product.material_terms),
    )))
    return SemanticCandidate(
        identifier,
        product.title,
        product.category_values,
        product.brand_values,
        features,
        product.price,
    )


def _request_key(request: SemanticRequest, version: str) -> str:
    payload = {
        "version": version,
        "query": request.rewritten_query,
        "slots": request.slots,
        "negatives": request.negatives,
        "candidate_ids": [candidate.parent_asin for candidate in request.candidates],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

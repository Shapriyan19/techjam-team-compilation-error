from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import OrderedDict
from pathlib import Path

from starter.clarification import (
    ConservativeQuestionPolicy,
    InformationGainAnalyzer,
)
from starter.clarification_config import PhaseFourConfig
from starter.phase5_config import PhaseFiveConfig
from starter.phase6_config import PhaseSixConfig
from starter.runtime_trace import RuntimeTraceRecorder
from starter.semantic import CatalogEncoderSemanticProvider, SemanticShortlistReranker
from starter.topk import ConservativeTopKAllocator
from starter.ranking.config import PhaseThreeConfig
from starter.ranking.evidence import (
    CandidateEvidencePool,
    FreshCandidate,
    explicit_rejected_ids,
)
from starter.ranking.features import CatalogFeatureStore, DeterministicFeatureScorer, ScoredCandidate
from starter.retrieval.config import RetrievalConfig
from starter.retrieval.dense import DenseRetriever
from starter.retrieval.facets import FacetRetriever
from starter.retrieval.rrf import weighted_rrf_details
<<<<<<< Updated upstream
from starter.state import SessionState
=======
from starter.retrieval.query_expansion import expand_query, expansion_is_useful
from starter.retrieval.hash_dense import HashDenseIndex
from starter.runtime_config import PhaseFiveConfig
from starter.state import SessionState
from starter.improvements import ImprovementConfig
from starter.neural import NeuralConfig, PretrainedDenseRetriever, LocalCrossEncoder
from starter.tracing import FALLBACK_TIERS, RuntimeTracer, TurnTrace
>>>>>>> Stashed changes
from starter.understanding import rewrite_query, update_state_from_message


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "please", "some",
    "that", "the", "this", "to", "want", "with", "would", "you", "looking",
}


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _terms(text: str) -> list[str]:
    return [
        token.lower()
        for token in TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in STOPWORDS
    ]


class Agent:
    """Stateful deterministic agent using the starter BM25 retrieval path."""

    def __init__(
        self,
        catalog_path: str | Path = "data/catalog.jsonl",
        retrieval_config: RetrievalConfig | None = None,
        phase3_config: PhaseThreeConfig | None = None,
        phase4_config: PhaseFourConfig | None = None,
        phase5_config: PhaseFiveConfig | None = None,
        phase6_config: PhaseSixConfig | None = None,
<<<<<<< Updated upstream
=======
        rerank_client: RerankClient | None = None,
        improvements: ImprovementConfig | None = None,
        neural_config: NeuralConfig | None = None,
>>>>>>> Stashed changes
    ) -> None:
        started = time.perf_counter()
        self.catalog_path = Path(catalog_path)
        self.improvements = improvements or ImprovementConfig.from_environment()
        self.neural_config = neural_config or NeuralConfig.from_environment()
        self.local_neural_status = "disabled"
        self._pretrained_dense = None
        self._local_cross_encoder = None
        self._lexical_cache: OrderedDict[tuple, tuple[str, ...]] = OrderedDict()
        self.lexical_cache_hits = 0
        self.lexical_cache_misses = 0
        self._stage_details: dict[str, float] = {}
        self._hash_dense: HashDenseIndex | None = None
        self.retrieval_config = retrieval_config or RetrievalConfig.from_environment()
        self.phase3_config = phase3_config or PhaseThreeConfig.from_environment()
        self.phase4_config = phase4_config or PhaseFourConfig.from_environment()
        self.phase5_config = phase5_config or PhaseFiveConfig.from_environment()
        self.phase6_config = phase6_config or PhaseSixConfig.from_environment()
        self.trace_recorder = RuntimeTraceRecorder(
            self.phase5_config.tracing_enabled,
            self.phase5_config.trace_limit,
        )
        self.connection = sqlite3.connect(":memory:")
        self._lexical_cache: OrderedDict[tuple[str, int], tuple[str, ...]] = OrderedDict()
        self._lexical_cache_limit = 4096
        self.lexical_cache_hits = 0
        self.lexical_cache_misses = 0
        self._sessions: dict[str, SessionState] = {}
        self._dense_retriever: DenseRetriever | None = None
        self._facet_retriever: FacetRetriever | None = None
        self._feature_store: CatalogFeatureStore | None = None
        self._feature_scorer: DeterministicFeatureScorer | None = None
        self._question_analyzer: InformationGainAnalyzer | None = None
        self._question_policy = ConservativeQuestionPolicy(self.phase4_config)
        self._last_scored_candidates: dict[str, list[ScoredCandidate]] = {}
        self._topk_allocator: ConservativeTopKAllocator | None = None
        self._semantic_reranker: SemanticShortlistReranker | None = None
        self.topk_history: list[dict] = []
        self.dense_status = "disabled"
        self.facet_status = "disabled"
        self.feature_scorer_status = "disabled"
        self.clarification_status = "disabled"
        self.semantic_status = "disabled"
        self.startup_components: dict[str, float] = {}
        component_started = time.perf_counter()
        self._build_index()
<<<<<<< Updated upstream
        self.startup_components["fts5_catalog_build"] = time.perf_counter() - component_started
        component_started = time.perf_counter()
=======
        if self.improvements.hash_dense:
            rows = self.connection.execute(
                "SELECT parent_asin, title || ' ' || categories || ' ' || features || ' ' || details || ' ' || store || ' ' || description FROM products"
            ).fetchall()
            self._hash_dense = HashDenseIndex([(str(row[0]), str(row[1] or "")) for row in rows])
>>>>>>> Stashed changes
        self._load_optional_retrievers()
        self.startup_components["retrieval_artifacts"] = time.perf_counter() - component_started
        component_started = time.perf_counter()
        self._load_optional_feature_scorer()
        self.startup_components["feature_store"] = time.perf_counter() - component_started
        component_started = time.perf_counter()
        self._load_optional_clarification()
        self.startup_components["clarification"] = time.perf_counter() - component_started
        component_started = time.perf_counter()
        self._load_optional_semantic_reranker()
<<<<<<< Updated upstream
        self.startup_components["semantic_optional"] = time.perf_counter() - component_started
        self._topk_allocator = ConservativeTopKAllocator(self._feature_store, self.phase5_config)
=======
        if self.neural_config.mode != "off":
            try:
                if self.neural_config.mode == "dense":
                    self._pretrained_dense = PretrainedDenseRetriever(self.catalog_path, self.neural_config)
                else:
                    if self._feature_store is None:
                        raise RuntimeError("local reranking needs the feature store")
                    self._local_cross_encoder = LocalCrossEncoder(self._feature_store, self.neural_config)
                self.local_neural_status = "ready"
            except Exception as exc:
                self.local_neural_status = f"disabled: {type(exc).__name__}: {exc}"
>>>>>>> Stashed changes
        self.startup_seconds = time.perf_counter() - started

    def _build_index(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            "CREATE VIRTUAL TABLE products USING fts5("
            "parent_asin UNINDEXED, title, categories, features, details, store, description, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        batch: list[tuple[str, str, str, str, str, str, str]] = []
        with self.catalog_path.open(encoding="utf-8") as handle:
            for line in handle:
                product = json.loads(line)
                batch.append(
                    (
                        str(product["parent_asin"]),
                        _text(product.get("title")),
                        _text(product.get("categories")),
                        _text(product.get("features")),
                        _text(product.get("details")),
                        _text(product.get("store")),
                        _text(product.get("description")),
                    )
                )
                if len(batch) >= 1000:
                    cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
                    batch.clear()
        if batch:
            cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
        self.connection.commit()

    def reset(self, session_id: str, user_profile: dict) -> None:
        # Store the anonymized profile for later phases, but do not rank with it yet.
        self._sessions[session_id] = SessionState(
            session_id=session_id,
            user_profile=dict(user_profile),
            candidate_pool=CandidateEvidencePool(self.phase3_config.active_pool_size),
        )
        self._last_scored_candidates.pop(session_id, None)

    def session_state(self, session_id: str) -> SessionState:
        """Expose deterministic state for local debugging and unit tests."""
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise RuntimeError("reset must be called before respond") from exc

    def close(self) -> None:
        if self._feature_store is not None:
            self._feature_store.close()
            self._feature_store = None
        self.connection.close()

    def _load_optional_retrievers(self) -> None:
        if self.retrieval_config.uses_dense:
            try:
                self._dense_retriever = DenseRetriever(
                    self.retrieval_config.artifact_dir,
                    self.catalog_path,
                    validate_checksums=self.retrieval_config.validate_artifact_checksums,
                )
                self.dense_status = "ready"
            except Exception as exc:
                self.dense_status = f"fallback: {type(exc).__name__}: {exc}"
        if self.retrieval_config.uses_facets:
            try:
                self._facet_retriever = FacetRetriever(
                    self.retrieval_config.artifact_dir,
                    self.catalog_path,
                    validate_checksums=self.retrieval_config.validate_artifact_checksums,
                )
                self.facet_status = "ready"
            except Exception as exc:
                self.facet_status = f"disabled: {type(exc).__name__}: {exc}"

    def _load_optional_feature_scorer(self) -> None:
        if not self.phase3_config.uses_reranker:
            return
        try:
            self._feature_store = CatalogFeatureStore(self.catalog_path)
            if (
                self.phase4_config.analyzes_questions
                and self.phase5_config.retain_all_feature_records
            ):
                self._feature_store.retain_all()
            self._feature_scorer = DeterministicFeatureScorer(
                self._feature_store,
                self.phase3_config.feature_weights,
                self.improvements,
            )
            self.feature_scorer_status = "ready"
        except Exception as exc:
            self.feature_scorer_status = f"fallback: {type(exc).__name__}: {exc}"

    def _load_optional_clarification(self) -> None:
        if not self.phase4_config.analyzes_questions:
            return
        if self._feature_store is None:
            self.clarification_status = "disabled: feature store unavailable"
            return
        self._question_analyzer = InformationGainAnalyzer(
            self._feature_store,
            self.phase4_config,
        )
        self.clarification_status = "ready"

    def _load_optional_semantic_reranker(self) -> None:
        if self.phase6_config.semantic_rerank_mode == "off":
            return
        provider = None
        if self.phase6_config.semantic_provider == "catalog_encoder":
            try:
                provider = CatalogEncoderSemanticProvider(
                    self.retrieval_config.artifact_dir,
                    self.catalog_path,
                    validate_checksums=self.retrieval_config.validate_artifact_checksums,
                )
                self.semantic_status = "ready: catalog_encoder"
            except Exception as exc:
                self.semantic_status = f"fallback: {type(exc).__name__}: {exc}"
        else:
            self.semantic_status = "fallback: no provider configured"
        self._semantic_reranker = SemanticShortlistReranker(
            provider,
            self._feature_store,
            self.phase6_config,
        )

    def _lexical_search(self, query: str, top_k: int) -> list[dict]:
        started = time.perf_counter()
        unique_terms = list(dict.fromkeys(_terms(query)))[:40]
        expression = " OR ".join(f'"{term}"' for term in unique_terms)
        if not expression:
            return []
<<<<<<< Updated upstream
        cache_key = (expression, int(top_k))
        cached = self._lexical_cache.pop(cache_key, None)
        if cached is not None:
            self._lexical_cache[cache_key] = cached
            self.lexical_cache_hits += 1
=======
        key = (expression, top_k)
        cache_enabled = self.improvements.optimize and self.improvements.lexical_cache_size > 0
        cached = self._lexical_cache.get(key) if cache_enabled else None
        if cached is not None:
            self._lexical_cache.move_to_end(key)
            self.lexical_cache_hits += 1
            self._stage_details["lexical"] = _elapsed_ms(started)
>>>>>>> Stashed changes
            return [{"parent_asin": identifier} for identifier in cached]
        self.lexical_cache_misses += 1
        rows = self.connection.execute(
            "SELECT parent_asin FROM products WHERE products MATCH ? "
            "ORDER BY bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) LIMIT ?",
            (expression, top_k),
        ).fetchall()
<<<<<<< Updated upstream
        identifiers = tuple(str(row[0]) for row in rows)
        self._lexical_cache[cache_key] = identifiers
        if len(self._lexical_cache) > self._lexical_cache_limit:
            self._lexical_cache.popitem(last=False)
        return [{"parent_asin": identifier} for identifier in identifiers]
=======
        if cache_enabled:
            self._lexical_cache[key] = tuple(str(row[0]) for row in rows)
            if len(self._lexical_cache) > self.improvements.lexical_cache_size:
                self._lexical_cache.popitem(last=False)
        self._stage_details["lexical"] = _elapsed_ms(started)
        return [{"parent_asin": str(row[0])} for row in rows]
>>>>>>> Stashed changes

    def _fresh_retrieval(
        self,
        query: str,
        limit: int,
        state: SessionState,
    ) -> list[FreshCandidate]:
        if self.retrieval_config.mode == "lexical":
            lexical = self._lexical_search(query, limit)
            return [
                FreshCandidate(
                    parent_asin=item["parent_asin"],
                    fused_rank=rank,
                    fused_score=1.0 / (self.retrieval_config.rrf_k + rank),
                    route_ranks=(("lexical", rank),),
                )
                for rank, item in enumerate(lexical, start=1)
            ]

        with self.trace_recorder.measure(
            state.session_id, state.turn, "retrieval_lexical"
        ):
            lexical = self._lexical_search(query, self.retrieval_config.lexical_top_n)
        rankings: dict[str, list[str]] = {
            "lexical": [item["parent_asin"] for item in lexical],
        }
        expansion_mode = self.improvements.query_expansion
        expanded_query = expand_query(query) if expansion_mode != "off" else query
        if (expanded_query != query and
                (expansion_mode == "always" or expansion_is_useful(query))):
            expanded = self._lexical_search(expanded_query, self.retrieval_config.lexical_top_n)
            rankings["lexical_expanded"] = [item["parent_asin"] for item in expanded]
        if self.retrieval_config.uses_dense:
            if self._dense_retriever is None:
                return [
                    FreshCandidate(
                        parent_asin=item["parent_asin"],
                        fused_rank=rank,
                        fused_score=1.0 / (self.retrieval_config.rrf_k + rank),
                        route_ranks=(("lexical", rank),),
                    )
                    for rank, item in enumerate(lexical[:limit], start=1)
                ]
            try:
                rankings["dense"] = self._dense_retriever.search(
                    query,
                    self.retrieval_config.dense_top_n,
                )
            except Exception as exc:
                self.dense_status = f"fallback: {type(exc).__name__}: {exc}"
                return [
                    FreshCandidate(
                        parent_asin=item["parent_asin"],
                        fused_rank=rank,
                        fused_score=1.0 / (self.retrieval_config.rrf_k + rank),
                        route_ranks=(("lexical", rank),),
                    )
                    for rank, item in enumerate(lexical[:limit], start=1)
                ]
        if self.retrieval_config.uses_facets and self._facet_retriever is not None:
            facet_started = time.perf_counter()
            try:
                with self.trace_recorder.measure(
                    state.session_id, state.turn, "retrieval_facet"
                ):
                    rankings["facet"] = self._facet_retriever.search(
                        query,
                        self.retrieval_config.facet_top_n,
                    )
            except Exception as exc:
                self.facet_status = f"disabled: {type(exc).__name__}: {exc}"
<<<<<<< Updated upstream
        with self.trace_recorder.measure(state.session_id, state.turn, "rrf"):
            fused = weighted_rrf_details(
                rankings,
                self.retrieval_config.route_weights(state.active_scenario),
                k=self.retrieval_config.rrf_k,
                limit=limit,
            )
=======
            finally:
                self._stage_details["facet"] = _elapsed_ms(facet_started)
        route_weights = self.retrieval_config.route_weights(state.active_scenario)
        if self._hash_dense is not None:
            rankings["hash_dense"] = self._hash_dense.search(query, self.retrieval_config.dense_top_n)
            route_weights["hash_dense"] = 0.20
        if "lexical_expanded" in rankings:
            route_weights["lexical_expanded"] = 0.35
        if self._pretrained_dense is not None:
            neural_started = time.perf_counter()
            try:
                rankings["pretrained_dense"] = self._pretrained_dense.search(query, self.neural_config.top_n)
                route_weights["pretrained_dense"] = self.neural_config.dense_weight
            except Exception as exc:
                self.local_neural_status = f"fallback: {type(exc).__name__}: {exc}"
            self._stage_details["pretrained_dense"] = _elapsed_ms(neural_started)
        fusion_started = time.perf_counter()
        fused = weighted_rrf_details(
            rankings,
            route_weights,
            k=self.retrieval_config.rrf_k,
            limit=limit,
        )
        self._stage_details["fusion"] = _elapsed_ms(fusion_started)
>>>>>>> Stashed changes
        return [
            FreshCandidate(
                parent_asin=item.parent_asin,
                fused_rank=item.rank,
                fused_score=item.score,
                route_ranks=item.route_ranks,
            )
            for item in fused
        ]

    def _search(self, query: str, top_k: int, state: SessionState) -> list[dict]:
        return [
            {"parent_asin": item.parent_asin}
            for item in self._fresh_retrieval(query, top_k, state)
        ]

    def _persistent_search(
        self,
        query: str,
        top_k: int,
        state: SessionState,
        *,
        is_override: bool,
        user_message: str,
    ) -> list[dict]:
        if is_override:
            state.candidate_pool.start_override_epoch()
            state.override_epoch = state.candidate_pool.override_epoch

        rejected = explicit_rejected_ids(user_message, state.last_recommendations)
        state.rejected_product_ids.update(rejected)
        state.candidate_pool.reject(state.rejected_product_ids)

        fresh = self._fresh_retrieval(
            query,
            self.phase3_config.fresh_candidate_limit,
            state,
        )
        ranked = state.candidate_pool.merge(
            fresh,
            turn=state.turn,
            weights=self.phase3_config.persistence_weights,
            rrf_k=self.retrieval_config.rrf_k,
        )
        state.candidate_pool.reject(state.rejected_product_ids)
        if rejected:
            ranked = state.candidate_pool.rank(
                turn=state.turn,
                weights=self.phase3_config.persistence_weights,
                rrf_k=self.retrieval_config.rrf_k,
            )
        return [
            {"parent_asin": record.parent_asin}
            for record in ranked
            if not record.rejected and not record.contradicted
        ][:top_k]

    def _reranked_search(
        self,
        query: str,
        top_k: int,
        state: SessionState,
    ) -> list[dict]:
        fresh = self._fresh_retrieval(
            query,
            self.phase3_config.fresh_candidate_limit,
            state,
        )
        if self._feature_scorer is None:
            state.last_candidate_scores = tuple(
                (candidate.parent_asin, candidate.fused_score)
                for candidate in fresh
            )
            return [
                {"parent_asin": candidate.parent_asin}
                for candidate in fresh[:top_k]
            ]
<<<<<<< Updated upstream
        with self.trace_recorder.measure(state.session_id, state.turn, "reranker"):
            ranked = self._feature_scorer.rank(fresh, state)
=======
        rerank_started = time.perf_counter()
        ranked = self._feature_scorer.rank(fresh, state)
        self._stage_details["feature_rerank"] = _elapsed_ms(rerank_started)
        if self._local_cross_encoder is not None:
            neural_started = time.perf_counter()
            try:
                ranked = self._local_cross_encoder.rank(query, ranked)
            except Exception as exc:
                self.local_neural_status = f"fallback: {type(exc).__name__}: {exc}"
            self._stage_details["cross_encoder"] = _elapsed_ms(neural_started)
>>>>>>> Stashed changes
        state.last_candidate_scores = tuple(
            (candidate.parent_asin, candidate.score)
            for candidate in ranked
        )
        self._last_scored_candidates[state.session_id] = [
            candidate
            for candidate in ranked
            if candidate.parent_asin not in state.rejected_product_ids
        ]
        return [
            {"parent_asin": candidate.parent_asin}
<<<<<<< Updated upstream
            for candidate in ranked
            if candidate.parent_asin not in state.rejected_product_ids
        ][:top_k]
=======
            for candidate in self._reranked_candidates(query, state)[:top_k]
        ]

    def route_health(self) -> dict:
        return {
            "lexical": "ready",
            "dense": self.dense_status,
            "facet": self.facet_status,
            "reranker": self.feature_scorer_status,
            "clarification": self.clarification_status,
            "allocation": self.allocation_status,
            "semantic_rerank": self.semantic_rerank_status,
            "local_neural": self.local_neural_status,
        }

    def last_trace(self) -> dict | None:
        trace = self.tracer.last
        return None if trace is None else trace.to_dict()

    def trace_history(self) -> list[dict]:
        return [trace.to_dict() for trace in self.tracer]
>>>>>>> Stashed changes

    def runtime_stats(self) -> dict:
        return {
            "retrieval_mode": self.retrieval_config.mode,
            "phase3_mode": self.phase3_config.mode,
            "phase4_mode": self.phase4_config.mode,
            "topk_mode": self.phase5_config.topk_mode,
            "tracing_enabled": self.trace_recorder.enabled,
            "semantic_rerank_mode": self.phase6_config.semantic_rerank_mode,
            "semantic_provider": self.phase6_config.semantic_provider,
            "semantic_rerank_k": self.phase6_config.semantic_rerank_k,
            "semantic_status": self.semantic_status,
            "semantic_stats": (
                self._semantic_reranker.stats()
                if self._semantic_reranker is not None else None
            ),
            "question_candidate_k": self.phase4_config.question_candidate_k,
            "active_pool_size": self.phase3_config.active_pool_size,
            "feature_scorer_status": self.feature_scorer_status,
            "clarification_status": self.clarification_status,
            "feature_records_cached": (
                self._feature_store.cached_record_count
                if self._feature_store is not None else 0
            ),
            "startup_seconds": self.startup_seconds,
            "startup_components_seconds": {
                name: round(value, 6)
                for name, value in self.startup_components.items()
            },
            "dense_status": self.dense_status,
            "facet_status": self.facet_status,
            "lexical_cache_hits": self.lexical_cache_hits,
            "lexical_cache_misses": self.lexical_cache_misses,
            "dense_average_query_ms": (
                self._dense_retriever.average_query_latency_ms
                if self._dense_retriever is not None else None
            ),
            "facet_average_query_ms": (
                self._facet_retriever.average_query_latency_ms
                if self._facet_retriever is not None else None
            ),
        }

    def _validate_recommendations(self, recommendations: object, top_k: int) -> list[dict]:
        if not isinstance(recommendations, list):
            return []
        valid: list[dict] = []
        seen: set[str] = set()
        for item in recommendations:
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("parent_asin") or "").strip()
            if not identifier or identifier in seen:
                continue
            if self._feature_store is not None and not self._feature_store.contains(identifier):
                continue
            seen.add(identifier)
            valid.append({"parent_asin": identifier})
            if len(valid) >= max(0, int(top_k)):
                break
        return valid

    def respond(
        self,
        session_id: str,
        user_message: str,
        turn: int,
        top_k: int,
    ) -> dict:
<<<<<<< Updated upstream
        state = self.session_state(session_id)
        with self.trace_recorder.measure(session_id, turn, "state"):
            parsed = update_state_from_message(state, user_message, turn)
        with self.trace_recorder.measure(session_id, turn, "query_rewrite"):
            query = rewrite_query(state)
        state.rejected_product_ids.update(
            explicit_rejected_ids(user_message, state.last_recommendations)
=======
        started = time.perf_counter()
        stage_milliseconds: dict[str, float] = {}
        self._stage_details = {}
        degraded: list[str] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        state = self._session_for_response(session_id)
        parsed, query = self._understand(
            state, user_message, turn, stage_milliseconds, degraded
        )
        recommendations, tier = self._recommend(
            query, top_k, state, parsed, user_message, stage_milliseconds, degraded, usage
>>>>>>> Stashed changes
        )
        if self.phase3_config.uses_persistence:
            recommendations = self._persistent_search(
                query,
                top_k,
                state,
                is_override=parsed.is_override,
                user_message=user_message,
            )
        elif self.phase3_config.uses_reranker:
            recommendations = self._reranked_search(query, top_k, state)
        else:
            recommendations = self._search(query, top_k, state)
        recommendations = self._validate_recommendations(recommendations, top_k)
        state.last_recommendations = tuple(
            str(item["parent_asin"])
            for item in recommendations
            if item.get("parent_asin")
        )
        message = "Here are the closest matches I found."
        internal_ask_attribute = None
        ask_attribute = None
        decision_reason = "Phase 4 disabled"
        question_score = None
        question_threshold = None
        if self._question_analyzer is not None:
            clarification_started = time.perf_counter()
            clarification_succeeded = True
            clarification_error_type = None
            try:
                ranked_for_analysis = [
                    ScoredCandidate(
                        parent_asin=identifier,
                        score=score,
                        fresh_rank=rank,
                        features=(),
                    )
                    for rank, (identifier, score) in enumerate(state.last_candidate_scores, start=1)
                ]
                analysis = self._question_analyzer.analyze(
                    ranked_for_analysis,
                    state,
                    self.trace_recorder,
                )
                analysis_record = {"turn": state.turn, **analysis.to_dict()}
                state.question_analysis_history.append(analysis_record)
                raw_best = max(
                    analysis.traces,
                    key=lambda trace: trace.raw_question_score,
                    default=None,
                )
                if raw_best is not None and raw_best.already_asked:
                    state.repeated_questions_prevented += 1
                if raw_best is not None and raw_best.no_preference:
                    state.no_preference_responses_respected += 1
                selection_started = time.perf_counter()
                decision = self._question_policy.decide(analysis, state)
                self.trace_recorder.record(
                    session_id,
                    turn,
                    "question_selection",
                    (time.perf_counter() - selection_started) * 1000.0,
                )
                internal_ask_attribute = decision.ask_attribute
                ask_attribute = decision.api_attribute
                decision_reason = decision.reason
                question_threshold = decision.threshold
                if internal_ask_attribute is not None:
                    question_score = analysis.trace_for(internal_ask_attribute).final_question_score
                if decision.message:
                    message = decision.message
            except Exception as exc:
                clarification_succeeded = False
                clarification_error_type = type(exc).__name__
                self.clarification_status = f"fallback: {type(exc).__name__}: {exc}"
                decision_reason = "clarification failure; recommendations-only fallback"
            self.trace_recorder.record(
                session_id,
                turn,
                "clarification",
                (time.perf_counter() - clarification_started) * 1000.0,
                success=clarification_succeeded,
                fallback_used=not clarification_succeeded,
                error_type=clarification_error_type,
            )
        state.record_asked_attribute(internal_ask_attribute, ask_attribute)
        ranked_for_output = self._last_scored_candidates.get(session_id, [])
        if self._semantic_reranker is not None:
            semantic_started = time.perf_counter()
            try:
                ranked_for_output = self._semantic_reranker.rerank(
                    ranked_for_output,
                    state,
                    query,
                )
                self.trace_recorder.record(
                    session_id,
                    turn,
                    "semantic_reranker",
                    (time.perf_counter() - semantic_started) * 1000.0,
                )
            except Exception as exc:
                self.trace_recorder.record(
                    session_id,
                    turn,
                    "semantic_reranker",
                    (time.perf_counter() - semantic_started) * 1000.0,
                    success=False,
                    fallback_used=True,
                    error_type=type(exc).__name__,
                )
        allocation_started = time.perf_counter()
        if self._topk_allocator is not None and self.phase3_config.uses_reranker:
            try:
                allocation = self._topk_allocator.allocate(
                    ranked_for_output,
                    state,
                    top_k,
                )
                recommendations = [
                    {"parent_asin": identifier}
                    for identifier in allocation.recommendations
                ]
                self.topk_history.append({
                    "session_id": session_id,
                    "turn": state.turn,
                    "scenario": state.active_scenario,
                    "activated": allocation.activated,
                    "changed_positions": list(allocation.changed_positions),
                    "hedge_attribute": allocation.hedge_attribute,
                    "replacement_source_rank": allocation.replacement_source_rank,
                    "reason": allocation.reason,
                })
                self.trace_recorder.record(
                    session_id,
                    turn,
                    "topk_allocator",
                    (time.perf_counter() - allocation_started) * 1000.0,
                )
            except Exception as exc:
                self.trace_recorder.record(
                    session_id,
                    turn,
                    "topk_allocator",
                    (time.perf_counter() - allocation_started) * 1000.0,
                    success=False,
                    fallback_used=True,
                    error_type=type(exc).__name__,
                )
        recommendations = self._validate_recommendations(recommendations, top_k)
        state.last_recommendations = tuple(
            str(item["parent_asin"])
            for item in recommendations
            if item.get("parent_asin")
        )
        state.phase4_turn_history.append({
            "turn": state.turn,
            "user_message": user_message,
            "rewritten_query": query,
            "ask_attribute": internal_ask_attribute,
            "api_attribute": ask_attribute,
            "question_score": question_score,
            "question_threshold": question_threshold,
            "decision_reason": decision_reason,
            "recommendations": list(state.last_recommendations),
            "slots": {
                name: (
                    list(slot.value) if isinstance(slot.value, tuple) else slot.value
                )
                for name, slot in state.slots.items()
            },
        })
<<<<<<< Updated upstream
        response_started = time.perf_counter()
        response = {
=======
        stage_milliseconds["total"] = _elapsed_ms(started)
        # Detail timings overlap retrieval_ranking; never sum both levels.
        stage_milliseconds.update(self._stage_details)
        self._record_trace(
            state=state,
            tier=tier,
            query=query,
            parsed=parsed,
            ask_attribute=ask_attribute,
            decision_reason=decision_reason,
            recommendation_count=len(recommendations),
            stage_milliseconds=stage_milliseconds,
            degraded=degraded,
        )
        return {
>>>>>>> Stashed changes
            "message": message,
            "ask_attribute": ask_attribute,
            "recommendations": recommendations,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
<<<<<<< Updated upstream
        self.trace_recorder.record(
            session_id,
            turn,
            "response_validation",
            (time.perf_counter() - response_started) * 1000.0,
=======

    def _session_for_response(self, session_id: str) -> SessionState:
        """Never fail a turn because the harness skipped or lost ``reset``."""
        try:
            return self.session_state(session_id)
        except RuntimeError:
            self.reset(session_id, {})
            return self.session_state(session_id)

    def _understand(
        self,
        state: SessionState,
        user_message: str,
        turn: int,
        stage_milliseconds: dict[str, float],
        degraded: list[str],
    ) -> tuple[object | None, str]:
        started = time.perf_counter()
        parsed: object | None = None
        try:
            parsed = update_state_from_message(state, user_message, turn,
                                               active_evidence=self.improvements.active_evidence)
            query = rewrite_query(state, recent_first=self.improvements.recent_query)
        except Exception:
            degraded.append("understanding")
            query = " ".join(str(user_message).split())[:1000]
        stage_milliseconds["understanding"] = _elapsed_ms(started)
        return parsed, query

    def _recommend(
        self,
        query: str,
        top_k: int,
        state: SessionState,
        parsed: object | None,
        user_message: str,
        stage_milliseconds: dict[str, float],
        degraded: list[str],
        usage: dict[str, int],
    ) -> tuple[list[dict], str]:
        started = time.perf_counter()
        effective_top_k = (
            1 if 0 < self.improvements.precision_turns >= state.turn else top_k
        )
        tier = "full"
        if "understanding" in degraded:
            tier = "understanding_fallback"
        recommendations: list[dict] = []
        try:
            state.rejected_product_ids.update(
                explicit_rejected_ids(user_message, state.last_recommendations)
            )
            if self.phase3_config.uses_persistence:
                recommendations = self._persistent_search(
                    query,
                    effective_top_k,
                    state,
                    is_override=bool(getattr(parsed, "is_override", False)),
                    user_message=user_message,
                )
            elif self.phase3_config.uses_reranker:
                candidates = self._reranked_candidates(query, state)
                candidates, tier = self._semantic_rerank(
                    candidates, state, tier, degraded, usage
                )
                recommendations, tier = self._allocate(candidates, effective_top_k, tier, degraded)
            else:
                recommendations = self._search(query, effective_top_k, state)
        except Exception:
            degraded.append("ranking")
            recommendations, tier = self._degraded_recommendations(query, effective_top_k, state)
        recommendations = _unique_recommendations(recommendations, top_k)
        if not recommendations:
            recovered = _unique_recommendations(
                [{"parent_asin": identifier} for identifier in state.last_recommendations],
                top_k,
            )
            if recovered:
                degraded.append("empty_ranking")
                recommendations = recovered
                tier = "previous_recommendations"
            else:
                tier = _worst_tier(tier, "empty")
        stage_milliseconds["retrieval_ranking"] = _elapsed_ms(started)
        return recommendations, tier

    def _semantic_rerank(
        self,
        candidates: list[ScoredCandidate],
        state: SessionState,
        tier: str,
        degraded: list[str],
        usage: dict[str, int],
    ) -> tuple[list[ScoredCandidate], str]:
        if self._semantic_reranker is None or not candidates:
            return candidates, tier
        started = time.perf_counter()
        try:
            result = self._semantic_reranker.rerank(candidates, state)
        except Exception as exc:
            degraded.append("semantic_rerank")
            state.llm_status_history.append(f"error: {type(exc).__name__}")
            return candidates, _worst_tier(tier, "semantic_rerank_fallback")
        state.llm_status_history.append(result.status)
        state.llm_prompt_tokens += result.prompt_tokens
        state.llm_completion_tokens += result.completion_tokens
        usage["prompt_tokens"] += result.prompt_tokens
        usage["completion_tokens"] += result.completion_tokens
        self.semantic_rerank_seconds += time.perf_counter() - started
        if result.status.startswith("fallback:"):
            degraded.append("semantic_rerank")
            tier = _worst_tier(tier, "semantic_rerank_fallback")
        if not result.applied:
            return candidates, tier
        by_identifier = {candidate.parent_asin: candidate for candidate in candidates}
        reordered = [
            by_identifier[identifier]
            for identifier in result.ordered
            if identifier in by_identifier
        ]
        return (reordered or candidates), tier

    def _allocate(
        self,
        candidates: list[ScoredCandidate],
        top_k: int,
        tier: str,
        degraded: list[str],
    ) -> tuple[list[dict], str]:
        ranked = [{"parent_asin": candidate.parent_asin} for candidate in candidates[:top_k]]
        if self._allocator is None:
            return ranked, tier
        try:
            allocation = self._allocator.allocate(candidates, top_k)
        except Exception:
            degraded.append("allocation")
            return ranked, _worst_tier(tier, "allocation_fallback")
        return [
            {"parent_asin": identifier}
            for identifier in allocation.recommendations
        ], tier

    def _degraded_recommendations(
        self,
        query: str,
        top_k: int,
        state: SessionState,
    ) -> tuple[list[dict], str]:
        """Walk down the retrieval tiers until one of them returns something."""
        try:
            return self._search(query, top_k, state), "retrieval_fused"
        except Exception:
            pass
        try:
            return self._lexical_search(query, top_k), "retrieval_lexical"
        except Exception:
            return [], "empty"

    def _clarify(
        self,
        state: SessionState,
    ) -> tuple[str, str | None, str | None, str, float | None, float | None]:
        message = "Here are the closest matches I found."
        internal_ask_attribute = None
        ask_attribute = None
        decision_reason = "Phase 4 disabled"
        question_score = None
        question_threshold = None
        if self._question_analyzer is not None:
            ranked_for_analysis = [
                ScoredCandidate(
                    parent_asin=identifier,
                    score=score,
                    fresh_rank=rank,
                    features=(),
                )
                for rank, (identifier, score) in enumerate(state.last_candidate_scores, start=1)
            ]
            analysis = self._question_analyzer.analyze(ranked_for_analysis, state)
            analysis_record = {"turn": state.turn, **analysis.to_dict()}
            state.question_analysis_history.append(analysis_record)
            raw_best = max(
                analysis.traces,
                key=lambda trace: trace.raw_question_score,
                default=None,
            )
            if raw_best is not None and raw_best.already_asked:
                state.repeated_questions_prevented += 1
            if raw_best is not None and raw_best.no_preference:
                state.no_preference_responses_respected += 1
            allow_confidence_stop = True
            if self.improvements.evidence_confidence and state.last_recommendations:
                from starter.ranking.features import _category_agreement, _negative_conflict
                product = self._feature_store.get(state.last_recommendations[0])
                if product is not None:
                    agreement, conflict = _category_agreement(state.slots.get("category"), product)
                    allow_confidence_stop = not conflict and not _negative_conflict(state, product)
                    if state.slots.get("category") is not None and not agreement:
                        allow_confidence_stop = False
            decision = self._question_policy.decide(analysis, state, allow_confidence_stop=allow_confidence_stop)
            internal_ask_attribute = decision.ask_attribute
            ask_attribute = decision.api_attribute
            decision_reason = decision.reason
            question_threshold = decision.threshold
            if internal_ask_attribute is not None:
                question_score = analysis.trace_for(internal_ask_attribute).final_question_score
            if decision.message:
                message = decision.message
        return (
            message,
            internal_ask_attribute,
            ask_attribute,
            decision_reason,
            question_score,
            question_threshold,
>>>>>>> Stashed changes
        )
        return response

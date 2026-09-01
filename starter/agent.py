from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from starter.allocation import TopKAllocator
from starter.clarification import (
    ConservativeQuestionPolicy,
    InformationGainAnalyzer,
)
from starter.clarification_config import PhaseFourConfig
from starter.llm.client import AnthropicRerankClient, RerankClient
from starter.llm.config import PhaseSixConfig
from starter.llm.gemini_client import GeminiRerankClient
from starter.llm.nvidia_client import NvidiaRerankClient
from starter.llm.rerank import SemanticReranker
from starter.ranking.config import PhaseThreeConfig
from starter.ranking.evidence import (
    CandidateEvidencePool,
    FreshCandidate,
    explicit_rejected_ids,
)
from starter.ranking.features import (
    CatalogFeatureStore,
    DeterministicFeatureScorer,
    ScoredCandidate,
    singular_token,
)
from starter.ranking.rarity import TermRarity
from starter.retrieval.config import RetrievalConfig
from starter.retrieval.dense import DenseRetriever
from starter.retrieval.facets import FacetRetriever
from starter.retrieval.rrf import weighted_rrf_details
from starter.runtime_config import PhaseFiveConfig
from starter.state import SessionState
from starter.understanding import CATEGORY_ALIASES
from starter.tracing import FALLBACK_TIERS, RuntimeTracer, TurnTrace
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


def _elapsed_ms(started: float) -> float:
    return 1000.0 * (time.perf_counter() - started)


def _worst_tier(current: str, candidate: str) -> str:
    """Keep the most degraded tier observed while answering one turn."""
    order = {name: index for index, name in enumerate(FALLBACK_TIERS)}
    return max((current, candidate), key=lambda name: order.get(name, 0))


def _unique_recommendations(items: list[dict], top_k: int) -> list[dict]:
    """Guarantee the official contract: ordered, unique, non-empty identifiers."""
    result: list[dict] = []
    seen: set[str] = set()
    for item in items:
        identifier = str(item.get("parent_asin", "")).strip()
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        result.append({"parent_asin": identifier})
        if len(result) >= top_k:
            break
    return result


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
        rerank_client: RerankClient | None = None,
    ) -> None:
        started = time.perf_counter()
        self.catalog_path = Path(catalog_path)
        self.retrieval_config = retrieval_config or RetrievalConfig.from_environment()
        self.phase3_config = phase3_config or PhaseThreeConfig.from_environment()
        self.phase4_config = phase4_config or PhaseFourConfig.from_environment()
        self.phase5_config = phase5_config or PhaseFiveConfig.from_environment()
        self.phase6_config = phase6_config or PhaseSixConfig.from_environment()
        self._injected_rerank_client = rerank_client
        self.connection = sqlite3.connect(":memory:")
        self._sessions: dict[str, SessionState] = {}
        self._dense_retriever: DenseRetriever | None = None
        self._facet_retriever: FacetRetriever | None = None
        self._feature_store: CatalogFeatureStore | None = None
        self._feature_scorer: DeterministicFeatureScorer | None = None
        self._question_analyzer: InformationGainAnalyzer | None = None
        self._question_policy = ConservativeQuestionPolicy(self.phase4_config)
        self._allocator: TopKAllocator | None = None
        self._semantic_reranker: SemanticReranker | None = None
        self.tracer = RuntimeTracer(
            history_limit=self.phase5_config.trace_history_limit,
            enabled=self.phase5_config.records_traces,
        )
        self.dense_status = "disabled"
        self.facet_status = "disabled"
        self.feature_scorer_status = "disabled"
        self.term_rarity_status = "disabled"
        self.clarification_status = "disabled"
        self.allocation_status = "disabled"
        self.semantic_rerank_status = "disabled"
        self.semantic_rerank_seconds = 0.0
        self._build_index()
        self._load_optional_retrievers()
        self._load_optional_feature_scorer()
        self._load_optional_clarification()
        self._load_optional_allocator()
        self._load_optional_semantic_reranker()
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
                    deterministic_ties=self.retrieval_config.deterministic_facet_ties,
                )
                self.facet_status = "ready"
            except Exception as exc:
                self.facet_status = f"disabled: {type(exc).__name__}: {exc}"

    def _load_optional_feature_scorer(self) -> None:
        if not self.phase3_config.uses_reranker:
            return
        try:
            self._feature_store = CatalogFeatureStore(
                self.catalog_path,
                cache_size=self.phase5_config.feature_cache_size,
            )
            rarity = None
            if self.phase3_config.use_term_rarity:
                try:
                    rarity = TermRarity.from_artifacts(
                        self.retrieval_config.artifact_dir, stemmer=singular_token
                    )
                    self.term_rarity_status = f"ready: {len(rarity)} terms"
                except Exception as exc:
                    # Unweighted coverage is a valid ranking, just a blunter one.
                    self.term_rarity_status = f"fallback: {type(exc).__name__}: {exc}"
            self._feature_scorer = DeterministicFeatureScorer(
                self._feature_store,
                self.phase3_config.feature_weights,
                rarity=rarity,
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

    def _load_optional_allocator(self) -> None:
        if not self.phase5_config.allocates_top_k:
            return
        if self._feature_store is None:
            self.allocation_status = "disabled: feature store unavailable"
            return
        self._allocator = TopKAllocator(self._feature_store, self.phase5_config.allocation)
        self.allocation_status = "ready"

    def _load_optional_semantic_reranker(self) -> None:
        if not self.phase6_config.calls_model:
            return
        if self._feature_store is None:
            self.semantic_rerank_status = "disabled: feature store unavailable"
            return
        client = self._injected_rerank_client
        if client is None:
            client_class = {
                "gemini": GeminiRerankClient,
                "nvidia": NvidiaRerankClient,
            }.get(self.phase6_config.provider, AnthropicRerankClient)
            try:
                client = client_class(self.phase6_config)
            except Exception as exc:
                self.semantic_rerank_status = f"disabled: {type(exc).__name__}: {exc}"
                return
        self._semantic_reranker = SemanticReranker(
            self._feature_store,
            self.phase6_config,
            client,
        )
        self.semantic_rerank_status = "ready"

    def _lexical_search(self, query: str, top_k: int) -> list[dict]:
        unique_terms = list(dict.fromkeys(_terms(query)))[:40]
        expression = " OR ".join(f'"{term}"' for term in unique_terms)
        if not expression:
            return []
        rows = self.connection.execute(
            "SELECT parent_asin FROM products WHERE products MATCH ? "
            "ORDER BY bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) LIMIT ?",
            (expression, top_k),
        ).fetchall()
        return [{"parent_asin": str(row[0])} for row in rows]

    def _category_route_search(self, state: SessionState, top_k: int) -> list[dict]:
        """Extra RRF route scoped to the shopper's stated category.

        Peer comparison (algorathem/Xandurs repos) found a category-scoped
        route to be their single largest lever: it shrinks the pool a crowded
        catalog would otherwise flood with unrelated-but-lexically-matching
        items. Xandurs implements it as a hard partition (candidates() returns
        *only* the matched shelf, nothing else). That would violate CLAUDE.md
        rule 5 (full-catalog search every turn; no persisted narrowing) and
        depends on literal substring reuse of the shopper's exact wording -
        the paraphrase risk that project explicitly flags against itself.

        This version keeps the same underlying signal but folds it into RRF as
        one more route alongside lexical/facet, which already run in full
        every turn regardless of this route's outcome: a wrong or missing
        category can only fail to help, never remove a candidate the other
        routes found. It also matches on the category *slot* (extracted via
        CATEGORY_ALIASES, already tolerant of paraphrase - "tee shirt" and
        "t-shirt" both resolve to the same canonical value) rather than
        substring-matching raw message text. Reuses the existing FTS5 index;
        no new artifact. See docs/EXPERIMENT_LOG.md P21.
        """
        slot = state.slots.get("category")
        if slot is None:
            return []
        value = slot.value[0] if isinstance(slot.value, tuple) else slot.value
        aliases = CATEGORY_ALIASES.get(str(value), (str(value),))
        expression = "categories: (" + " OR ".join(f'"{alias}"' for alias in aliases) + ")"
        try:
            rows = self.connection.execute(
                "SELECT parent_asin FROM products WHERE products MATCH ? "
                "ORDER BY bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) LIMIT ?",
                (expression, top_k),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [{"parent_asin": str(row[0])} for row in rows]

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

        lexical = self._lexical_search(query, self.retrieval_config.lexical_top_n)
        rankings: dict[str, list[str]] = {
            "lexical": [item["parent_asin"] for item in lexical],
        }
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
            try:
                rankings["facet"] = self._facet_retriever.search(
                    query,
                    self.retrieval_config.facet_top_n,
                )
            except Exception as exc:
                self.facet_status = f"disabled: {type(exc).__name__}: {exc}"
        if self.retrieval_config.category_weight > 0.0:
            category_route = self._category_route_search(
                state, self.retrieval_config.category_top_n
            )
            if category_route:
                rankings["category"] = [item["parent_asin"] for item in category_route]
        fused = weighted_rrf_details(
            rankings,
            self.retrieval_config.route_weights(state.active_scenario),
            k=self.retrieval_config.rrf_k,
            limit=limit,
            combine=self.retrieval_config.rrf_combine,
        )
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

    def _reranked_candidates(self, query: str, state: SessionState) -> list[ScoredCandidate]:
        """Full ordered reranked candidate list, before any Top-K truncation."""
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
                ScoredCandidate(
                    parent_asin=candidate.parent_asin,
                    score=candidate.fused_score,
                    fresh_rank=candidate.fused_rank,
                    features=(),
                )
                for candidate in fresh
            ]
        ranked = self._feature_scorer.rank(fresh, state)
        state.last_candidate_scores = tuple(
            (candidate.parent_asin, candidate.score)
            for candidate in ranked
        )
        return [
            candidate
            for candidate in ranked
            if candidate.parent_asin not in state.rejected_product_ids
        ]

    def _reranked_search(
        self,
        query: str,
        top_k: int,
        state: SessionState,
    ) -> list[dict]:
        return [
            {"parent_asin": candidate.parent_asin}
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
        }

    def last_trace(self) -> dict | None:
        trace = self.tracer.last
        return None if trace is None else trace.to_dict()

    def trace_history(self) -> list[dict]:
        return [trace.to_dict() for trace in self.tracer]

    def runtime_stats(self) -> dict:
        return {
            "retrieval_mode": self.retrieval_config.mode,
            "phase3_mode": self.phase3_config.mode,
            "phase4_mode": self.phase4_config.mode,
            "phase5_mode": self.phase5_config.mode,
            "phase6_mode": self.phase6_config.mode,
            "phase6_provider": self.phase6_config.provider,
            "question_candidate_k": self.phase4_config.question_candidate_k,
            "active_pool_size": self.phase3_config.active_pool_size,
            "feature_scorer_status": self.feature_scorer_status,
            "clarification_status": self.clarification_status,
            "allocation_status": self.allocation_status,
            "semantic_rerank_status": self.semantic_rerank_status,
            "llm_model": self.phase6_config.model if self.phase6_config.calls_model else None,
            "startup_seconds": self.startup_seconds,
            "dense_status": self.dense_status,
            "facet_status": self.facet_status,
            "feature_cache": (
                None if self._feature_store is None else self._feature_store.cache_statistics()
            ),
            "runtime_health": self.tracer.summary(),
            "dense_average_query_ms": (
                self._dense_retriever.average_query_latency_ms
                if self._dense_retriever is not None else None
            ),
            "facet_average_query_ms": (
                self._facet_retriever.average_query_latency_ms
                if self._facet_retriever is not None else None
            ),
        }

    def respond(
        self,
        session_id: str,
        user_message: str,
        turn: int,
        top_k: int,
    ) -> dict:
        started = time.perf_counter()
        stage_milliseconds: dict[str, float] = {}
        degraded: list[str] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        state = self._session_for_response(session_id)
        parsed, query = self._understand(
            state, user_message, turn, stage_milliseconds, degraded
        )
        recommendations, tier = self._recommend(
            query, top_k, state, parsed, user_message, stage_milliseconds, degraded, usage
        )
        state.last_recommendations = tuple(
            str(item["parent_asin"])
            for item in recommendations
            if item.get("parent_asin")
        )
        clarify_started = time.perf_counter()
        try:
            message, internal_ask_attribute, ask_attribute, decision_reason, question_score, question_threshold = (
                self._clarify(state)
            )
        except Exception as exc:
            degraded.append("clarification")
            tier = _worst_tier(tier, "clarification_fallback")
            message = "Here are the closest matches I found."
            internal_ask_attribute = None
            ask_attribute = None
            decision_reason = f"clarification fallback: {type(exc).__name__}"
            question_score = None
            question_threshold = None
        stage_milliseconds["clarification"] = _elapsed_ms(clarify_started)
        state.record_asked_attribute(internal_ask_attribute, ask_attribute)
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
        stage_milliseconds["total"] = _elapsed_ms(started)
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
            "message": message,
            "ask_attribute": ask_attribute,
            "recommendations": recommendations,
            "usage": usage,
        }

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
            parsed = update_state_from_message(state, user_message, turn)
            query = rewrite_query(state)
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
                    top_k,
                    state,
                    is_override=bool(getattr(parsed, "is_override", False)),
                    user_message=user_message,
                )
            elif self.phase3_config.uses_reranker:
                candidates = self._reranked_candidates(query, state)
                candidates, tier = self._semantic_rerank(
                    candidates, state, tier, degraded, usage
                )
                recommendations, tier = self._allocate(
                    candidates, top_k, tier, degraded, state.turn
                )
            else:
                recommendations = self._search(query, top_k, state)
        except Exception:
            degraded.append("ranking")
            recommendations, tier = self._degraded_recommendations(query, top_k, state)
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
        turn: int = 0,
    ) -> tuple[list[dict], str]:
        emit_k = self.phase5_config.allocation.effective_top_k(turn, top_k)
        ranked = [{"parent_asin": candidate.parent_asin} for candidate in candidates[:emit_k]]
        if self._allocator is None:
            return ranked, tier
        try:
            allocation = self._allocator.allocate(candidates, emit_k)
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
            decision = self._question_policy.decide(analysis, state)
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
        )

    def _record_trace(
        self,
        *,
        state: SessionState,
        tier: str,
        query: str,
        parsed: object | None,
        ask_attribute: str | None,
        decision_reason: str,
        recommendation_count: int,
        stage_milliseconds: dict[str, float],
        degraded: list[str],
    ) -> TurnTrace:
        patches = getattr(parsed, "patches", ()) or ()
        trace = TurnTrace(
            session_id=state.session_id,
            turn=state.turn,
            fallback_tier=tier,
            route_health=self.route_health(),
            route_candidates={
                "fused_candidates": len(state.last_candidate_scores),
                "returned": recommendation_count,
            },
            rewritten_query=query,
            state_patches=tuple(
                f"{patch.operation.value}:{patch.slot}" for patch in patches
            ),
            active_slots=tuple(sorted(state.slots)),
            scenario=state.active_scenario,
            ask_attribute=ask_attribute,
            question_reason=decision_reason,
            recommendation_count=recommendation_count,
            stage_milliseconds={
                name: round(value, 4) for name, value in stage_milliseconds.items()
            },
            degraded_stages=tuple(degraded),
        )
        return self.tracer.record(trace)

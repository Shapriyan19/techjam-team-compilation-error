from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from starter.clarification import (
    ConservativeQuestionPolicy,
    InformationGainAnalyzer,
)
from starter.clarification_config import PhaseFourConfig
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
from starter.state import SessionState
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
    ) -> None:
        started = time.perf_counter()
        self.catalog_path = Path(catalog_path)
        self.retrieval_config = retrieval_config or RetrievalConfig.from_environment()
        self.phase3_config = phase3_config or PhaseThreeConfig.from_environment()
        self.phase4_config = phase4_config or PhaseFourConfig.from_environment()
        self.connection = sqlite3.connect(":memory:")
        self._sessions: dict[str, SessionState] = {}
        self._dense_retriever: DenseRetriever | None = None
        self._facet_retriever: FacetRetriever | None = None
        self._feature_store: CatalogFeatureStore | None = None
        self._feature_scorer: DeterministicFeatureScorer | None = None
        self._question_analyzer: InformationGainAnalyzer | None = None
        self._question_policy = ConservativeQuestionPolicy(self.phase4_config)
        self.dense_status = "disabled"
        self.facet_status = "disabled"
        self.feature_scorer_status = "disabled"
        self.clarification_status = "disabled"
        self._build_index()
        self._load_optional_retrievers()
        self._load_optional_feature_scorer()
        self._load_optional_clarification()
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
                )
                self.facet_status = "ready"
            except Exception as exc:
                self.facet_status = f"disabled: {type(exc).__name__}: {exc}"

    def _load_optional_feature_scorer(self) -> None:
        if not self.phase3_config.uses_reranker:
            return
        try:
            self._feature_store = CatalogFeatureStore(self.catalog_path)
            self._feature_scorer = DeterministicFeatureScorer(
                self._feature_store,
                self.phase3_config.feature_weights,
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
        fused = weighted_rrf_details(
            rankings,
            self.retrieval_config.route_weights(state.active_scenario),
            k=self.retrieval_config.rrf_k,
            limit=limit,
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
        ranked = self._feature_scorer.rank(fresh, state)
        state.last_candidate_scores = tuple(
            (candidate.parent_asin, candidate.score)
            for candidate in ranked
        )
        return [
            {"parent_asin": candidate.parent_asin}
            for candidate in ranked
            if candidate.parent_asin not in state.rejected_product_ids
        ][:top_k]

    def runtime_stats(self) -> dict:
        return {
            "retrieval_mode": self.retrieval_config.mode,
            "phase3_mode": self.phase3_config.mode,
            "phase4_mode": self.phase4_config.mode,
            "question_candidate_k": self.phase4_config.question_candidate_k,
            "active_pool_size": self.phase3_config.active_pool_size,
            "feature_scorer_status": self.feature_scorer_status,
            "clarification_status": self.clarification_status,
            "startup_seconds": self.startup_seconds,
            "dense_status": self.dense_status,
            "facet_status": self.facet_status,
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
        state = self.session_state(session_id)
        parsed = update_state_from_message(state, user_message, turn)
        query = rewrite_query(state)
        state.rejected_product_ids.update(
            explicit_rejected_ids(user_message, state.last_recommendations)
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
        return {
            "message": message,
            "ask_attribute": ask_attribute,
            "recommendations": recommendations,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }

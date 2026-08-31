from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from starter.agent import Agent, _unique_recommendations
from starter.allocation import TopKAllocator
from starter.clarification_config import PhaseFourConfig
from starter.ranking.config import PhaseThreeConfig
from starter.ranking.features import (
    KNOWN_FEATURES,
    KNOWN_OCCASIONS,
    KNOWN_STYLES,
    KNOWN_USE_CASES,
    CatalogFeatureStore,
    ScoredCandidate,
    _corpus_ngrams,
    _normalize_phrase,
    _phrases_present,
    _product_features,
)
from starter.retrieval.config import RetrievalConfig
from starter.runtime_config import AllocationConfig, PhaseFiveConfig
from starter.tracing import FALLBACK_TIERS, RuntimeTracer, TurnTrace


def _product(identifier: str, title: str, **overrides) -> dict:
    product = {
        "parent_asin": identifier,
        "title": title,
        "features": [],
        "description": [],
        "price": None,
        "categories": ["Clothing, Shoes & Jewelry", "Shoes", "Sneakers"],
        "details": {},
        "average_rating": 4.0,
        "rating_number": 1,
        "store": "Example",
    }
    product.update(overrides)
    return product


def _reference_phrases(corpus: str, phrases: tuple[str, ...]) -> tuple[str, ...]:
    """The pre-optimization boundary-anchored definition, kept as the oracle."""
    return tuple(
        phrase
        for phrase in phrases
        if re.search(
            r"(?<![a-z0-9])" + re.escape(phrase).replace(r"\ ", r"\s+") + r"(?![a-z0-9])",
            corpus,
        )
    )


def _scored(identifier: str, rank: int) -> ScoredCandidate:
    return ScoredCandidate(identifier, 1.0 / rank, rank, ())


class PhraseLookupEquivalenceTest(unittest.TestCase):
    CORPUSES = (
        "lightweight running shoes with arch support and a water resistant upper",
        "non slip resistant sole quick dry moisture wicking travel sneaker",
        "vintage formal event dress with date night styling and uv protection",
        "waterproof insulated winter boot hiking gym training everyday",
        "",
        "slip resistant non slip machine washable adjustable reversible",
    )

    def test_ngram_lookup_matches_boundary_anchored_search(self) -> None:
        for text in self.CORPUSES:
            corpus = _normalize_phrase(text)
            ngrams = _corpus_ngrams(corpus)
            for vocabulary in (KNOWN_USE_CASES, KNOWN_STYLES, KNOWN_OCCASIONS, KNOWN_FEATURES):
                with self.subTest(corpus=text, vocabulary=vocabulary[0]):
                    self.assertEqual(
                        _phrases_present(ngrams, vocabulary),
                        _reference_phrases(corpus, vocabulary),
                    )

    def test_overlapping_phrases_are_both_detected(self) -> None:
        ngrams = _corpus_ngrams(_normalize_phrase("a non slip resistant outsole"))

        self.assertIn("non slip", _phrases_present(ngrams, KNOWN_FEATURES))
        self.assertIn("slip resistant", _phrases_present(ngrams, KNOWN_FEATURES))

    def test_partial_word_does_not_match_phrase(self) -> None:
        ngrams = _corpus_ngrams(_normalize_phrase("waterproofing agent for runningshoes"))

        self.assertNotIn("waterproof", _phrases_present(ngrams, KNOWN_FEATURES))
        self.assertNotIn("running", _phrases_present(ngrams, KNOWN_USE_CASES))

    def test_decoded_product_keeps_expected_catalog_values(self) -> None:
        decoded = _product_features(
            _product(
                "A",
                "Lightweight breathable running sneaker",
                features=["machine washable", "arch support"],
                details={"Brand": "Example", "Style": "casual"},
                price=42.5,
            )
        )

        self.assertEqual(decoded.use_case_values, ("running",))
        self.assertIn("machine washable", decoded.feature_values)
        self.assertIn("arch support", decoded.feature_values)
        self.assertEqual(decoded.style_values, ("casual",))
        self.assertEqual(decoded.price, 42.5)


class CatalogFeatureStoreCacheTest(unittest.TestCase):
    def test_cache_statistics_track_hits_and_misses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.jsonl"
            catalog_path.write_text(
                "".join(json.dumps(_product(name, f"{name} shoe")) + "\n" for name in "AB"),
                encoding="utf-8",
            )
            store = CatalogFeatureStore(catalog_path, cache_size=8)
            try:
                store.get("A")
                store.get("A")
                store.get("MISSING")
                statistics = store.cache_statistics()
            finally:
                store.close()

        self.assertEqual(statistics["hits"], 1)
        self.assertEqual(statistics["misses"], 1)
        self.assertEqual(statistics["unknown_lookups"], 1)
        self.assertEqual(statistics["hit_rate"], 0.5)

    def test_bounded_cache_never_exceeds_its_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.jsonl"
            catalog_path.write_text(
                "".join(
                    json.dumps(_product(f"P{index}", f"product {index} shoe")) + "\n"
                    for index in range(10)
                ),
                encoding="utf-8",
            )
            store = CatalogFeatureStore(catalog_path, cache_size=3)
            try:
                for index in range(10):
                    store.get(f"P{index}")
                self.assertEqual(store.cache_statistics()["cached_products"], 3)
            finally:
                store.close()


class FacetTieDeterminismTest(unittest.TestCase):
    """The Top-N cut must not depend on how a NumPy build partitions tied scores."""

    def retriever(self, *, deterministic: bool):
        numpy = __import__("numpy")
        from starter.retrieval.facets import FacetRetriever

        retriever = FacetRetriever.__new__(FacetRetriever)
        retriever._np = numpy
        retriever.product_ids = [f"P{index}" for index in range(8)]
        retriever.token_to_index = {"black": 0}
        retriever.offsets = numpy.asarray([0, 8], dtype=numpy.int64)
        retriever.row_indices = numpy.arange(8, dtype=numpy.int32)
        # Every row scores identically, so only the tie-break rule decides the cut.
        retriever.posting_weights = numpy.ones(8, dtype=numpy.float32)
        retriever.idf = numpy.asarray([1.0], dtype=numpy.float32)
        retriever.deterministic_ties = deterministic
        retriever.query_count = 0
        retriever.query_seconds = 0.0
        return retriever

    def test_tied_scores_resolve_to_ascending_row_order(self) -> None:
        result = self.retriever(deterministic=True).search("black", 3)

        self.assertEqual(result, ["P0", "P1", "P2"])

    def test_repeated_queries_are_stable(self) -> None:
        retriever = self.retriever(deterministic=True)

        self.assertEqual(retriever.search("black", 4), retriever.search("black", 4))

    def test_unmatched_query_returns_nothing(self) -> None:
        self.assertEqual(self.retriever(deterministic=True).search("zzzzz", 3), [])


class TopKAllocatorTest(unittest.TestCase):
    class _Store:
        def __init__(self, groups: dict[str, str]) -> None:
            self.groups = groups

        def get(self, identifier: str):
            value = self.groups.get(identifier)
            if value is None:
                return None
            return type(
                "Features",
                (),
                {
                    "product_type_values": (value,) if value else (),
                    "brand_values": (),
                    "category_values": (value,) if value else (),
                },
            )()

    def allocator(self, groups: dict[str, str], **overrides) -> TopKAllocator:
        config = AllocationConfig(
            pool_size=overrides.pop("pool_size", 20),
            protected_slots=overrides.pop("protected_slots", 2),
            group_limit=overrides.pop("group_limit", 2),
            group_key=overrides.pop("group_key", "product_type"),
        )
        return TopKAllocator(self._Store(groups), config)

    def test_tail_slots_are_capped_per_group(self) -> None:
        groups = {f"P{index}": "sneaker" for index in range(6)}
        groups["P6"] = "boot"
        groups["P7"] = "sandal"
        allocator = self.allocator(groups)

        result = allocator.allocate([_scored(f"P{index}", index + 1) for index in range(8)], 4)

        self.assertEqual(result.recommendations, ("P0", "P1", "P6", "P7"))
        self.assertEqual(result.deferred_count, 4)

    def test_protected_head_is_never_reordered(self) -> None:
        groups = {f"P{index}": "sneaker" for index in range(5)}
        allocator = self.allocator(groups, protected_slots=3, group_limit=1)

        result = allocator.allocate([_scored(f"P{index}", index + 1) for index in range(5)], 3)

        self.assertEqual(result.recommendations, ("P0", "P1", "P2"))

    def test_missing_group_metadata_is_never_capped(self) -> None:
        groups = {f"P{index}": "" for index in range(5)}
        allocator = self.allocator(groups, protected_slots=0, group_limit=1)

        result = allocator.allocate([_scored(f"P{index}", index + 1) for index in range(5)], 5)

        self.assertEqual(result.recommendations, ("P0", "P1", "P2", "P3", "P4"))
        self.assertEqual(result.deferred_count, 0)

    def test_deferred_candidates_still_fill_remaining_slots(self) -> None:
        groups = {f"P{index}": "sneaker" for index in range(5)}
        allocator = self.allocator(groups, protected_slots=0, group_limit=1)

        result = allocator.allocate([_scored(f"P{index}", index + 1) for index in range(5)], 5)

        self.assertEqual(result.recommendations, ("P0", "P1", "P2", "P3", "P4"))
        self.assertEqual(len(set(result.recommendations)), 5)

    def test_allocation_is_deterministic(self) -> None:
        groups = {"A": "sneaker", "B": "sneaker", "C": "boot", "D": "sneaker", "E": "boot"}
        allocator = self.allocator(groups, protected_slots=1, group_limit=2)
        candidates = [_scored(name, rank) for rank, name in enumerate("ABCDE", start=1)]

        first = allocator.allocate(candidates, 4)
        second = allocator.allocate(candidates, 4)

        self.assertEqual(first.recommendations, second.recommendations)

    def test_empty_candidate_list_is_safe(self) -> None:
        result = self.allocator({}).allocate([], 10)

        self.assertEqual(result.recommendations, ())


class RuntimeTracerTest(unittest.TestCase):
    def trace(self, tier: str = "full") -> TurnTrace:
        return TurnTrace(
            session_id="session",
            turn=1,
            fallback_tier=tier,
            route_health={},
            route_candidates={},
            rewritten_query="shoes",
            state_patches=(),
            active_slots=(),
            scenario=None,
            ask_attribute=None,
            question_reason="reason",
            recommendation_count=10,
            stage_milliseconds={"total": 1.0},
            degraded_stages=(),
        )

    def test_history_is_bounded(self) -> None:
        tracer = RuntimeTracer(history_limit=2)
        for _ in range(5):
            tracer.record(self.trace())

        self.assertEqual(len(tracer), 2)
        self.assertEqual(tracer.summary()["traced_turns"], 5)

    def test_disabled_tracer_still_counts_health(self) -> None:
        tracer = RuntimeTracer(history_limit=4, enabled=False)
        tracer.record(self.trace("retrieval_lexical"))

        self.assertEqual(len(tracer), 0)
        self.assertEqual(tracer.summary()["fallback_tiers"], {"retrieval_lexical": 1})


class AgentRuntimeIntegrationTest(unittest.TestCase):
    def make_agent(self, directory: str, phase5_mode: str = "trace") -> Agent:
        catalog_path = Path(directory) / "catalog.jsonl"
        products = [
            _product("A", "Black leather heels for a wedding"),
            _product("B", "Black running sneakers lightweight"),
            _product("C", "White travel walking shoes breathable"),
            _product("D", "Blue cotton casual sneakers"),
        ]
        catalog_path.write_text(
            "".join(json.dumps(product) + "\n" for product in products),
            encoding="utf-8",
        )
        return Agent(
            catalog_path,
            RetrievalConfig(
                mode="hybrid_facet",
                artifact_dir=Path(directory) / "missing-artifacts",
                lexical_top_n=10,
                dense_top_n=10,
                facet_top_n=10,
                dense_weight=0.0,
                validate_artifact_checksums=False,
            ),
            PhaseThreeConfig(mode="rerank", fresh_candidate_limit=10),
            PhaseFourConfig(mode="ask"),
            PhaseFiveConfig(mode=phase5_mode, trace_history_limit=8),
        )

    def assert_valid_response(self, response: dict) -> None:
        self.assertIsInstance(response["message"], str)
        self.assertIsInstance(response["recommendations"], list)
        identifiers = [item["parent_asin"] for item in response["recommendations"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(all(isinstance(item, str) and item for item in identifiers))

    def test_healthy_turn_records_a_full_tier_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            try:
                agent.reset("session", {})
                response = agent.respond("session", "black shoes for a wedding", 1, 10)
                trace = agent.last_trace()
            finally:
                agent.close()

        self.assert_valid_response(response)
        self.assertEqual(trace["fallback_tier"], "full")
        self.assertEqual(trace["degraded_stages"], ())
        self.assertEqual(trace["turn"], 1)
        self.assertIn("total", trace["stage_milliseconds"])
        self.assertIn("lexical", trace["route_health"])
        self.assertTrue(trace["rewritten_query"])

    def test_trace_never_exposes_target_or_label_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            try:
                agent.reset("session", {})
                agent.respond("session", "black shoes", 1, 10)
                trace = agent.last_trace()
            finally:
                agent.close()

        forbidden = {"target", "ground_truth", "parent_asin", "label", "hit", "sample_id"}
        self.assertEqual(forbidden & set(trace), set())
        self.assertNotIn("recommendations", trace)

    def test_missing_facet_artifacts_still_return_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            try:
                agent.reset("session", {})
                response = agent.respond("session", "black shoes", 1, 10)
            finally:
                agent.close()

        self.assert_valid_response(response)
        self.assertTrue(response["recommendations"])
        self.assertTrue(agent.facet_status.startswith("disabled"))

    def test_ranking_failure_falls_back_to_fused_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            try:
                def explode(*args, **kwargs):
                    raise RuntimeError("reranker unavailable")

                agent._feature_scorer.rank = explode
                agent.reset("session", {})
                response = agent.respond("session", "black shoes", 1, 10)
                trace = agent.last_trace()
            finally:
                agent.close()

        self.assert_valid_response(response)
        self.assertTrue(response["recommendations"])
        self.assertEqual(trace["fallback_tier"], "retrieval_fused")
        self.assertIn("ranking", trace["degraded_stages"])

    def test_clarification_failure_keeps_a_valid_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            try:
                def explode(*args, **kwargs):
                    raise RuntimeError("analysis unavailable")

                agent._question_analyzer.analyze = explode
                agent.reset("session", {})
                response = agent.respond("session", "black shoes", 1, 10)
                trace = agent.last_trace()
            finally:
                agent.close()

        self.assert_valid_response(response)
        self.assertTrue(response["recommendations"])
        self.assertIsNone(response["ask_attribute"])
        self.assertEqual(trace["fallback_tier"], "clarification_fallback")

    def test_total_retrieval_failure_reuses_previous_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            try:
                agent.reset("session", {})
                first = agent.respond("session", "black shoes", 1, 10)

                def explode(*args, **kwargs):
                    raise RuntimeError("index unavailable")

                agent._fresh_retrieval = explode
                agent._lexical_search = explode
                second = agent.respond("session", "still exploring", 2, 10)
                trace = agent.last_trace()
            finally:
                agent.close()

        self.assert_valid_response(second)
        self.assertEqual(
            [item["parent_asin"] for item in second["recommendations"]],
            [item["parent_asin"] for item in first["recommendations"]],
        )
        self.assertEqual(trace["fallback_tier"], "previous_recommendations")

    def test_respond_without_reset_still_returns_a_valid_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            try:
                response = agent.respond("never-reset", "black shoes", 1, 10)
            finally:
                agent.close()

        self.assert_valid_response(response)
        self.assertTrue(response["recommendations"])

    def test_allocation_mode_returns_distinct_valid_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory, phase5_mode="allocate")
            try:
                agent.reset("session", {})
                response = agent.respond("session", "black shoes", 1, 4)
            finally:
                agent.close()

        self.assert_valid_response(response)
        self.assertEqual(agent.allocation_status, "ready")

    def test_runtime_stats_report_health_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            try:
                agent.reset("session", {})
                agent.respond("session", "black shoes", 1, 10)
                statistics = agent.runtime_stats()
            finally:
                agent.close()

        self.assertEqual(statistics["phase5_mode"], "trace")
        self.assertIsNotNone(statistics["feature_cache"])
        self.assertEqual(statistics["runtime_health"]["traced_turns"], 1)
        self.assertEqual(statistics["runtime_health"]["fallback_tiers"], {"full": 1})


class RecommendationContractTest(unittest.TestCase):
    def test_duplicates_and_blanks_are_removed_and_order_is_kept(self) -> None:
        items = [
            {"parent_asin": "A"},
            {"parent_asin": "A"},
            {"parent_asin": ""},
            {"parent_asin": "B"},
            {},
            {"parent_asin": "C"},
        ]

        self.assertEqual(
            _unique_recommendations(items, 10),
            [{"parent_asin": "A"}, {"parent_asin": "B"}, {"parent_asin": "C"}],
        )

    def test_top_k_is_never_exceeded(self) -> None:
        items = [{"parent_asin": f"P{index}"} for index in range(30)]

        self.assertEqual(len(_unique_recommendations(items, 10)), 10)

    def test_fallback_tiers_are_ordered_from_healthy_to_empty(self) -> None:
        self.assertEqual(FALLBACK_TIERS[0], "full")
        self.assertEqual(FALLBACK_TIERS[-1], "empty")


if __name__ == "__main__":
    unittest.main()


class PrecisionTurnAllocationTests(unittest.TestCase):
    """P19: short opening lists trade MTTC for MRR under the first-hit rule."""

    def test_opening_turns_emit_a_single_recommendation(self) -> None:
        config = AllocationConfig(precision_turns=2, precision_top_k=1)
        self.assertEqual(config.effective_top_k(1, 10), 1)
        self.assertEqual(config.effective_top_k(2, 10), 1)

    def test_later_turns_emit_the_full_top_k(self) -> None:
        # P20 keys the width to the constraint count instead; clearing the width
        # table isolates the turn-indexed rule this test is about.
        config = AllocationConfig(emit_widths=(), precision_turns=2, precision_top_k=1)
        self.assertEqual(config.effective_top_k(3, 10), 10)
        self.assertEqual(config.effective_top_k(10, 10), 10)

    def test_both_narrowing_rules_off_emits_the_full_top_k(self) -> None:
        config = AllocationConfig(emit_widths=(), precision_turns=0)
        self.assertEqual(config.effective_top_k(1, 10), 10)

    def test_never_emits_more_than_the_requested_top_k(self) -> None:
        # The contract allows up to top_k; a larger precision_top_k must not
        # widen the response beyond what the caller asked for.
        config = AllocationConfig(precision_turns=2, precision_top_k=5)
        self.assertEqual(config.effective_top_k(1, 3), 3)

    def test_agent_respects_the_policy_end_to_end(self) -> None:
        agent = Agent("data/catalog.jsonl")
        try:
            agent.reset("precision", {})
            first = agent.respond("precision", "I want navy cotton running shoes", 1, 10)
            later = agent.respond("precision", "size 10 please", 3, 10)
        finally:
            agent.close()
        self.assertEqual(len(first["recommendations"]), 1)
        self.assertGreater(len(later["recommendations"]), 1)

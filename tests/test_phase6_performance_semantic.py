from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starter.agent import Agent
from starter.clarification_config import PhaseFourConfig
from starter.phase5_config import PhaseFiveConfig
from starter.phase6_config import PhaseSixConfig
from starter.ranking.config import PhaseThreeConfig
from starter.ranking.features import ProductFeatures, ScoredCandidate
from starter.retrieval.config import RetrievalConfig
from starter.semantic import (
    CatalogEncoderSemanticProvider,
    SemanticShortlistReranker,
    _validated_order,
)
from starter.state import SessionState


class FakeStore:
    def __init__(self, identifiers: list[str]) -> None:
        self.products = {identifier: _features(identifier) for identifier in identifiers}

    def get(self, identifier: str) -> ProductFeatures | None:
        return self.products.get(identifier)


class RecordingProvider:
    version = "test-v1"

    def __init__(self, output: list[str] | None = None, fail: bool = False) -> None:
        self.output = output
        self.fail = fail
        self.requests = []

    def rank(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("provider failure")
        return self.output or [item.parent_asin for item in request.candidates]


def _features(identifier: str) -> ProductFeatures:
    return ProductFeatures(
        parent_asin=identifier,
        title=f"Product {identifier}",
        all_terms=frozenset({"shoe"}),
        category_terms=frozenset({"shoe"}),
        brand_terms=frozenset(),
        color_terms=frozenset(),
        material_terms=frozenset(),
        style_terms=frozenset(),
        price=50.0,
        category_values=("shoes",),
        product_type_values=("shoes",),
        brand_values=(),
        use_case_values=(),
        size_fit_values=(),
        style_values=(),
        occasion_values=(),
        feature_values=(),
    )


def _candidate(identifier: str, rank: int, *, conflict: float = 0.0, rejection: float = 0.0) -> ScoredCandidate:
    return ScoredCandidate(
        identifier,
        1.0 / rank,
        rank,
        (("conflict", conflict), ("rejection", rejection), ("route_support", 1.0)),
    )


class SemanticRerankerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.identifiers = [f"P{index:02d}" for index in range(35)]
        self.ranked = [_candidate(identifier, index + 1) for index, identifier in enumerate(self.identifiers)]
        self.store = FakeStore(self.identifiers)
        self.config = PhaseSixConfig(
            semantic_rerank_mode="optional",
            semantic_provider="catalog_encoder",
            semantic_rerank_k=30,
            semantic_protected_top_n=3,
        )

    def test_only_shortlist_ids_accepted_duplicates_removed_and_missing_appended(self) -> None:
        supplied = ["A", "B", "C"]

        result = _validated_order(["B", "HALLUCINATED", "B"], supplied)

        self.assertEqual(result, ["B", "A", "C"])

    def test_provider_receives_only_configured_shortlist(self) -> None:
        provider = RecordingProvider()
        reranker = SemanticShortlistReranker(provider, self.store, self.config)

        reranker.rerank(self.ranked, SessionState("s"), "shoes")

        self.assertEqual(len(provider.requests[0].candidates), 30)

    def test_top_three_are_protected(self) -> None:
        provider = RecordingProvider(list(reversed(self.identifiers[:30])))
        reranker = SemanticShortlistReranker(provider, self.store, self.config)

        result = reranker.rerank(self.ranked, SessionState("s"), "shoes")

        self.assertEqual(result[:3], self.ranked[:3])

    def test_semantic_failure_returns_deterministic_order(self) -> None:
        reranker = SemanticShortlistReranker(RecordingProvider(fail=True), self.store, self.config)

        result = reranker.rerank(self.ranked, SessionState("s"), "shoes")

        self.assertEqual(result, self.ranked)
        self.assertEqual(reranker.failures, 1)
        self.assertEqual(reranker.fallbacks, 1)

    def test_timeout_returns_deterministic_order(self) -> None:
        provider = RecordingProvider()
        provider.rank = lambda request: (_ for _ in ()).throw(TimeoutError("timeout"))
        reranker = SemanticShortlistReranker(provider, self.store, self.config)

        self.assertEqual(reranker.rerank(self.ranked, SessionState("s"), "shoes"), self.ranked)

    def test_no_provider_returns_deterministic_order(self) -> None:
        reranker = SemanticShortlistReranker(None, self.store, self.config)

        self.assertEqual(reranker.rerank(self.ranked, SessionState("s"), "shoes"), self.ranked)

    def test_hard_conflicting_and_rejected_items_are_not_restored_upward(self) -> None:
        ranked = list(self.ranked)
        ranked[9] = _candidate("P09", 10, conflict=-1.0)
        ranked[10] = _candidate("P10", 11, rejection=-1.0)
        provider = RecordingProvider(["P10", "P09", *self.identifiers[:9], *self.identifiers[11:30]])
        reranker = SemanticShortlistReranker(provider, self.store, self.config)

        result = reranker.rerank(ranked, SessionState("s"), "shoes")

        self.assertEqual(result[9].parent_asin, "P09")
        self.assertEqual(result[10].parent_asin, "P10")

    def test_override_changes_cache_key_and_same_input_hits_cache(self) -> None:
        provider = RecordingProvider()
        reranker = SemanticShortlistReranker(provider, self.store, self.config)
        state = SessionState("s")
        reranker.rerank(self.ranked, state, "black heels")
        reranker.rerank(self.ranked, state, "black heels")
        reranker.rerank(self.ranked, state, "white sneakers")

        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(reranker.cache_hits, 1)

    def test_final_order_is_unique_and_contains_only_candidates(self) -> None:
        provider = RecordingProvider(["P04", "P04", "INVALID", "P03"])
        reranker = SemanticShortlistReranker(provider, self.store, self.config)

        result = reranker.rerank(self.ranked, SessionState("s"), "shoes")
        identifiers = [item.parent_asin for item in result[:10]]

        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(set(identifiers).issubset(set(self.identifiers)))


class PhaseSixAgentEquivalenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog = Path(self.temporary.name) / "catalog.jsonl"
        products = [
            {
                "parent_asin": f"P{index}",
                "title": f"comfortable black running shoes {index}",
                "features": ["comfortable"],
                "description": [],
                "price": 50.0,
                "categories": ["Shoes", "Sneakers"],
                "details": {"Color": "black"},
                "average_rating": 4.0,
                "rating_number": 1,
                "store": f"Brand{index}",
            }
            for index in range(30)
        ]
        self.catalog.write_text("".join(json.dumps(item) + "\n" for item in products), encoding="utf-8")
        self.agents: list[Agent] = []

    def tearDown(self) -> None:
        for agent in self.agents:
            agent.close()
        self.temporary.cleanup()

    def agent(self, phase6: PhaseSixConfig | None = None, artifact_dir: Path | None = None) -> Agent:
        agent = Agent(
            self.catalog,
            RetrievalConfig(
                mode="hybrid_facet",
                artifact_dir=artifact_dir or Path(self.temporary.name) / "missing",
                lexical_top_n=30,
                facet_top_n=30,
                dense_weight=0.0,
                validate_artifact_checksums=True,
            ),
            PhaseThreeConfig(mode="rerank", fresh_candidate_limit=30),
            PhaseFourConfig(mode="ask", question_candidate_k=20),
            PhaseFiveConfig(topk_mode="rank_only"),
            phase6 or PhaseSixConfig(),
        )
        self.agents.append(agent)
        return agent

    def test_bm25_cache_and_reranker_preserve_order(self) -> None:
        agent = self.agent()
        first = agent._lexical_search("comfortable running shoes", 10)
        second = agent._lexical_search("comfortable running shoes", 10)

        self.assertEqual(first, second)
        self.assertEqual(agent.lexical_cache_hits, 1)

    def test_state_clarification_and_rank_only_behavior_unchanged(self) -> None:
        first = self.agent()
        second = self.agent()
        for agent in (first, second):
            agent.reset("s", {})

        self.assertEqual(
            first.respond("s", "black running shoes", 1, 10),
            second.respond("s", "black running shoes", 1, 10),
        )

    def test_missing_or_corrupt_optional_semantic_artifact_falls_back(self) -> None:
        config = PhaseSixConfig(semantic_rerank_mode="optional", semantic_provider="catalog_encoder")
        agent = self.agent(config, Path(self.temporary.name) / "corrupt")
        agent.reset("s", {})

        response = agent.respond("s", "running shoes", 1, 10)

        self.assertTrue(response["recommendations"])
        self.assertIn("fallback", agent.semantic_status)

    def test_default_initializes_neither_dense_semantic_nor_persistence(self) -> None:
        agent = self.agent()

        self.assertIsNone(agent._dense_retriever)
        self.assertIsNone(agent._semantic_reranker)
        self.assertFalse(agent.phase3_config.uses_persistence)

    def test_packaged_offline_semantic_artifacts_validate(self) -> None:
        catalog = Path("data/catalog.jsonl")
        artifact_dir = Path("artifacts/retrieval")
        if not catalog.is_file() or not (artifact_dir / "manifest.json").is_file():
            self.skipTest("optional packaged artifacts unavailable")

        provider = CatalogEncoderSemanticProvider(artifact_dir, catalog, validate_checksums=True)

        self.assertEqual(provider.version, "catalog_random_indexing_v1_shortlist_rerank_v1")


if __name__ == "__main__":
    unittest.main()

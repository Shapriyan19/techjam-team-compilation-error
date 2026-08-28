from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starter.agent import Agent
from starter.ranking.config import FeatureWeights, PersistenceWeights, PhaseThreeConfig
from starter.ranking.evidence import (
    CandidateEvidence,
    CandidateEvidencePool,
    FreshCandidate,
    explicit_rejected_ids,
)
from starter.ranking.features import CatalogFeatureStore, DeterministicFeatureScorer
from starter.retrieval.config import RetrievalConfig
from starter.state import SessionState
from starter.understanding import update_state_from_message


WEIGHTS = PersistenceWeights()


def _fresh(identifier: str, rank: int, *routes: str) -> FreshCandidate:
    route_names = routes or ("lexical",)
    return FreshCandidate(
        parent_asin=identifier,
        fused_rank=rank,
        fused_score=sum(
            (1.0 if route == "lexical" else 0.55) / (60.0 + rank)
            for route in route_names
        ),
        route_ranks=tuple((route, rank) for route in route_names),
    )


def _product(identifier: str, title: str, **overrides) -> dict:
    product = {
        "parent_asin": identifier,
        "title": title,
        "features": [],
        "description": [],
        "price": None,
        "categories": ["Clothing", "Shoes"],
        "details": {},
        "average_rating": 4.0,
        "rating_number": 1,
        "store": "Example",
    }
    product.update(overrides)
    return product


class CandidateEvidencePoolTest(unittest.TestCase):
    def test_candidate_survives_when_absent_from_next_fresh_top_n(self) -> None:
        pool = CandidateEvidencePool(1000)
        pool.merge([_fresh("A", 2), _fresh("B", 1)], turn=1, weights=WEIGHTS, rrf_k=60)

        ranked = pool.merge([_fresh("B", 1), _fresh("C", 2)], turn=2, weights=WEIGHTS, rrf_k=60)

        self.assertIn("A", pool.records)
        self.assertIsNone(pool.records["A"].current_turn_rank)
        self.assertIn("A", [record.parent_asin for record in ranked])

    def test_unseen_fresh_candidate_enters_on_later_turn(self) -> None:
        pool = CandidateEvidencePool(1000)
        pool.merge([_fresh("A", 1)], turn=1, weights=WEIGHTS, rrf_k=60)
        pool.merge([_fresh("NEW", 1)], turn=2, weights=WEIGHTS, rrf_k=60)

        self.assertIn("NEW", pool.records)
        self.assertEqual(pool.records["NEW"].first_seen_turn, 2)

    def test_pool_never_exceeds_configured_cap(self) -> None:
        pool = CandidateEvidencePool(2)
        pool.merge(
            [_fresh("A", 1), _fresh("B", 2), _fresh("C", 3)],
            turn=1,
            weights=WEIGHTS,
            rrf_k=60,
        )

        self.assertEqual(len(pool), 2)
        self.assertEqual(set(pool.records), {"A", "B"})

    def test_repeated_retrieval_increases_support_counts(self) -> None:
        pool = CandidateEvidencePool(1000)
        pool.merge([_fresh("A", 1, "lexical", "facet")], turn=1, weights=WEIGHTS, rrf_k=60)
        pool.merge([_fresh("A", 2, "lexical", "facet")], turn=2, weights=WEIGHTS, rrf_k=60)

        record = pool.records["A"]
        self.assertEqual(record.support_count, 2)
        self.assertEqual(record.lexical_support_count, 2)
        self.assertEqual(record.facet_support_count, 2)

    def test_recent_evidence_beats_equally_strong_stale_evidence(self) -> None:
        pool = CandidateEvidencePool(1000)
        pool.records = {
            "stale": CandidateEvidence("stale", 1, 1, None, 5, 0.0, support_count=1),
            "recent": CandidateEvidence("recent", 1, 3, None, 5, 0.0, support_count=1),
        }
        recency_only = PersistenceWeights(
            current_rrf=0.0,
            previous_rrf=0.0,
            best_rank=0.0,
            repeated_support=0.0,
            recency=1.0,
        )

        ranked = pool.rank(turn=3, weights=recency_only, rrf_k=60)

        self.assertEqual([item.parent_asin for item in ranked], ["recent", "stale"])

    def test_override_epoch_invalidates_old_candidate_evidence(self) -> None:
        pool = CandidateEvidencePool(1000)
        pool.merge([_fresh("HEEL", 1)], turn=1, weights=WEIGHTS, rrf_k=60)

        pool.start_override_epoch()
        pool.merge([_fresh("SNEAKER", 1)], turn=2, weights=WEIGHTS, rrf_k=60)

        self.assertNotIn("HEEL", pool.records)
        self.assertEqual(pool.records["SNEAKER"].override_epoch, 1)

    def test_rejected_record_receives_strong_penalty(self) -> None:
        pool = CandidateEvidencePool(1000)
        pool.merge([_fresh("A", 1), _fresh("B", 2)], turn=1, weights=WEIGHTS, rrf_k=60)
        pool.reject(["A"])

        ranked = pool.rank(turn=1, weights=WEIGHTS, rrf_k=60)

        self.assertTrue(pool.records["A"].rejected)
        self.assertEqual(ranked[-1].parent_asin, "A")

    def test_too_formal_is_recorded_as_negative_style_evidence(self) -> None:
        state = SessionState("session")
        update_state_from_message(state, "These are too formal", 1)

        self.assertNotIn("style", state.slots)
        self.assertIn("formal", state.negative_preferences["style"])

    def test_only_unambiguous_rejection_references_are_resolved(self) -> None:
        previous = ["B012345678", "B087654321"]

        self.assertEqual(explicit_rejected_ids("not the first two", previous), set(previous))
        self.assertEqual(explicit_rejected_ids("not those", previous), set())
        self.assertEqual(
            explicit_rejected_ids("reject B012345678", previous),
            {"B012345678"},
        )


class PersistentAgentIntegrationTest(unittest.TestCase):
    def make_agent(self, directory: str, pool_size: int = 1000) -> Agent:
        catalog_path = Path(directory) / "catalog.jsonl"
        products = [
            _product("A", "Black heels under 100"),
            _product("B", "Black sneakers under 100"),
            _product("C", "White travel shoes"),
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
            PhaseThreeConfig(
                mode="persistence",
                active_pool_size=pool_size,
                fresh_candidate_limit=10,
            ),
        )

    def test_fresh_retrieval_runs_every_turn_and_remains_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            batches = [[_fresh("A", 1)], [_fresh("C", 1)]]
            calls: list[str] = []

            def fresh(query: str, limit: int, state) -> list[FreshCandidate]:
                calls.append(query)
                return batches.pop(0)

            agent._fresh_retrieval = fresh
            agent.reset("session", {})
            agent.respond("session", "black shoes", 1, 10)
            agent.respond("session", "comfortable if possible", 2, 10)

            state = agent.session_state("session")
            self.assertEqual(len(calls), 2)
            self.assertIn("A", state.candidate_pool.records)
            self.assertIn("C", state.candidate_pool.records)

    def test_override_resets_pool_but_preserves_independent_slots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            batches = [[_fresh("A", 1)], [_fresh("B", 1)]]
            agent._fresh_retrieval = lambda query, limit, state: batches.pop(0)
            agent.reset("session", {})
            agent.respond("session", "black heels under $100", 1, 10)
            agent.respond("session", "actually sneakers instead", 2, 10)

            state = agent.session_state("session")
            self.assertEqual(state.override_epoch, 1)
            self.assertNotIn("A", state.candidate_pool.records)
            self.assertIn("B", state.candidate_pool.records)
            self.assertEqual(state.slots["color"].value, "black")
            self.assertEqual(state.slots["budget_max"].value, 100.0)

    def test_explicit_rank_rejection_removes_product_from_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            batches = [
                [_fresh("A", 1), _fresh("B", 2)],
                [_fresh("A", 1), _fresh("B", 2)],
            ]
            agent._fresh_retrieval = lambda query, limit, state: batches.pop(0)
            agent.reset("session", {})
            first = agent.respond("session", "black shoes", 1, 10)
            second = agent.respond("session", "not the first one", 2, 10)

            self.assertEqual(first["recommendations"][0]["parent_asin"], "A")
            self.assertNotIn("A", [item["parent_asin"] for item in second["recommendations"]])
            self.assertTrue(agent.session_state("session").candidate_pool.records["A"].rejected)

    def test_candidate_pools_are_isolated_by_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            agent.reset("one", {})
            agent.reset("two", {})
            agent.respond("one", "black shoes", 1, 10)

            self.assertGreater(len(agent.session_state("one").candidate_pool), 0)
            self.assertEqual(len(agent.session_state("two").candidate_pool), 0)

    def test_missing_facet_artifact_falls_back_to_fresh_lexical_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            agent.reset("session", {})

            response = agent.respond("session", "black sneakers", 1, 10)

            self.assertTrue(response["recommendations"])
            self.assertIn("disabled", agent.facet_status)
            self.assertGreater(len(agent.session_state("session").candidate_pool), 0)


class DeterministicFeatureScorerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog_path = Path(self.temporary.name) / "catalog.jsonl"
        self.products = [
            _product(
                "A",
                "Nike black leather sneakers",
                categories=["Clothing", "Women", "Shoes", "Sneakers"],
                features=["comfortable running shoe"],
                details={"Color": "Black", "Material": "Leather", "Brand": "Nike"},
                store="Nike",
                price=75.0,
            ),
            _product(
                "B",
                "Generic white polyester heels",
                categories=["Clothing", "Women", "Shoes", "Heels"],
                features=["formal party style"],
                details={"Color": "White", "Material": "Polyester"},
                store="Generic",
                price=120.0,
            ),
            _product(
                "MISSING",
                "Unspecified footwear",
                categories=[],
                features=[],
                details={},
                store="",
                price=None,
            ),
        ]
        self.catalog_path.write_text(
            "".join(json.dumps(product) + "\n" for product in self.products),
            encoding="utf-8",
        )
        self.store = CatalogFeatureStore(self.catalog_path, cache_size=10)
        self.scorer = DeterministicFeatureScorer(self.store, FeatureWeights())

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def state_after(self, *messages: str) -> SessionState:
        state = SessionState("session")
        for turn, message in enumerate(messages, start=1):
            update_state_from_message(state, message, turn)
        return state

    def ranked(self, state: SessionState, *identifiers: str, evidence=None):
        fresh = [_fresh(identifier, 5, "lexical") for identifier in identifiers]
        return self.scorer.rank(fresh, state, evidence)

    def test_category_agreement_boosts_candidate(self) -> None:
        state = self.state_after("I want sneakers")

        ranked = self.ranked(state, "B", "A")

        self.assertEqual(ranked[0].parent_asin, "A")

    def test_soft_brand_agreement_boosts_without_eliminating_alternative(self) -> None:
        state = self.state_after("Nike if possible")

        ranked = self.ranked(state, "B", "A")

        self.assertEqual(ranked[0].parent_asin, "A")
        self.assertEqual({item.parent_asin for item in ranked}, {"A", "B"})

    def test_known_hard_contradiction_is_penalized(self) -> None:
        state = self.state_after("must be black")

        ranked = self.ranked(state, "B", "A")
        features = {item.parent_asin: dict(item.features) for item in ranked}

        self.assertEqual(ranked[0].parent_asin, "A")
        self.assertEqual(features["B"]["conflict"], -1.0)

    def test_negative_material_evidence_penalizes_matching_product(self) -> None:
        state = self.state_after("no leather")

        ranked = self.ranked(state, "A", "B")
        features = {item.parent_asin: dict(item.features) for item in ranked}

        self.assertEqual(features["A"]["conflict"], -1.0)
        self.assertEqual(ranked[0].parent_asin, "B")

    def test_missing_metadata_is_neutral_not_a_contradiction(self) -> None:
        state = self.state_after("must be black")

        ranked = self.ranked(state, "B", "MISSING")
        features = {item.parent_asin: dict(item.features) for item in ranked}

        self.assertEqual(features["MISSING"]["conflict"], 0.0)
        self.assertEqual(ranked[0].parent_asin, "MISSING")

    def test_known_price_violation_is_penalized(self) -> None:
        state = self.state_after("under $100")

        ranked = self.ranked(state, "B", "A")
        features = {item.parent_asin: dict(item.features) for item in ranked}

        self.assertEqual(features["A"]["price"], 1.0)
        self.assertEqual(features["B"]["conflict"], -1.0)
        self.assertEqual(ranked[0].parent_asin, "A")

    def test_missing_price_remains_eligible(self) -> None:
        state = self.state_after("under $100")

        ranked = self.ranked(state, "B", "MISSING")
        features = {item.parent_asin: dict(item.features) for item in ranked}

        self.assertEqual(features["MISSING"]["price"], 0.0)
        self.assertIn("MISSING", [item.parent_asin for item in ranked])
        self.assertEqual(ranked[0].parent_asin, "MISSING")

    def test_explicit_rejection_strongly_penalizes_candidate(self) -> None:
        state = self.state_after("shoes")
        state.rejected_product_ids.add("A")

        ranked = self.ranked(state, "A", "B")

        self.assertEqual(ranked[-1].parent_asin, "A")
        self.assertEqual(dict(ranked[-1].features)["rejection"], -1.0)

    def test_repeated_compatible_evidence_can_help(self) -> None:
        state = self.state_after("shoes")
        evidence = {
            "A": CandidateEvidence("A", 1, 2, 5, 5, 0.01, support_count=3),
            "B": CandidateEvidence("B", 1, 2, 5, 5, 0.01, support_count=1),
        }

        ranked = self.ranked(state, "B", "A", evidence=evidence)

        self.assertEqual(ranked[0].parent_asin, "A")

    def test_override_removes_stale_category_advantage(self) -> None:
        state = self.state_after("I want heels")
        before = self.ranked(state, "A", "B")

        update_state_from_message(state, "actually sneakers instead", 2)
        after = self.ranked(state, "A", "B")

        self.assertEqual(before[0].parent_asin, "B")
        self.assertEqual(after[0].parent_asin, "A")

    def test_ordering_is_deterministic(self) -> None:
        state = self.state_after("black sneakers under $100")

        first = self.ranked(state, "B", "MISSING", "A")
        second = self.ranked(state, "B", "MISSING", "A")

        self.assertEqual(first, second)

    def test_agent_returns_valid_unique_catalog_ids(self) -> None:
        agent = Agent(
            self.catalog_path,
            RetrievalConfig(
                mode="hybrid_facet",
                artifact_dir=Path(self.temporary.name) / "missing-artifacts",
                lexical_top_n=10,
                dense_top_n=10,
                facet_top_n=10,
                dense_weight=0.0,
                validate_artifact_checksums=False,
            ),
            PhaseThreeConfig(mode="rerank", fresh_candidate_limit=10),
        )
        try:
            agent.reset("session", {})
            response = agent.respond("session", "black sneakers", 1, 10)
            identifiers = [item["parent_asin"] for item in response["recommendations"]]

            self.assertTrue(identifiers)
            self.assertEqual(len(identifiers), len(set(identifiers)))
            self.assertTrue(set(identifiers).issubset({"A", "B", "MISSING"}))
        finally:
            assert agent._feature_store is not None
            agent._feature_store.close()


if __name__ == "__main__":
    unittest.main()

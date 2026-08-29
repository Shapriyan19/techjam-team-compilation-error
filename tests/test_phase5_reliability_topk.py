from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starter.agent import Agent
from starter.clarification_config import PhaseFourConfig
from starter.phase5_config import PhaseFiveConfig
from starter.ranking.config import PhaseThreeConfig
from starter.ranking.features import CatalogFeatureStore, ScoredCandidate
from starter.retrieval.config import RetrievalConfig
from starter.state import ConstraintStrength, SessionState, SlotValue
from starter.topk import ConservativeTopKAllocator


def _product(identifier: str, color: str, brand: str) -> dict:
    return {
        "parent_asin": identifier,
        "title": f"{color} sneakers",
        "features": ["comfortable"],
        "description": [],
        "price": 50.0,
        "categories": ["Shoes", "Sneakers"],
        "details": {"Color": color, "Brand": brand},
        "average_rating": 4.0,
        "rating_number": 10,
        "store": brand,
    }


def _candidate(identifier: str, rank: int, *, score: float | None = None) -> ScoredCandidate:
    return ScoredCandidate(
        identifier,
        1.0 - rank * 0.001 if score is None else score,
        rank,
        (("route_support", 1.0), ("conflict", 0.0), ("rejection", 0.0)),
    )


class PhaseFiveFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog = Path(self.temporary.name) / "catalog.jsonl"
        products = [
            _product(f"P{index:02d}", "black" if index < 10 else "white", f"Brand{index}")
            for index in range(30)
        ]
        self.catalog.write_text(
            "".join(json.dumps(product) + "\n" for product in products),
            encoding="utf-8",
        )
        self.agents: list[Agent] = []

    def tearDown(self) -> None:
        for agent in self.agents:
            agent.close()
        self.temporary.cleanup()

    def agent(self, *, tracing: bool = False, retain_all: bool = True) -> Agent:
        agent = Agent(
            self.catalog,
            RetrievalConfig(mode="lexical", lexical_top_n=30),
            PhaseThreeConfig(mode="rerank", fresh_candidate_limit=30),
            PhaseFourConfig(
                mode="ask",
                question_candidate_k=20,
                minimum_uncertainty=0.0,
                maximum_top_confidence=1.0,
                early_threshold=0.0,
                max_known_attributes=10,
            ),
            PhaseFiveConfig(
                tracing_enabled=tracing,
                retain_all_feature_records=retain_all,
                topk_mode="rank_only",
            ),
        )
        self.agents.append(agent)
        return agent


class PhaseFiveReliabilityTest(PhaseFiveFixture):
    def test_optimized_and_control_select_same_attribute_and_recommendations(self) -> None:
        control = self.agent(retain_all=False)
        optimized = self.agent(retain_all=True)
        for agent in (control, optimized):
            agent.reset("s", {})

        control_response = control.respond("s", "sneakers and still exploring", 1, 10)
        optimized_response = optimized.respond("s", "sneakers and still exploring", 1, 10)

        self.assertEqual(control_response, optimized_response)

    def test_same_no_question_decision_and_ask_turn(self) -> None:
        control = self.agent(retain_all=False)
        optimized = self.agent(retain_all=True)
        for agent in (control, optimized):
            agent.reset("s", {})
        messages = ("must be black sneakers", "I don't have an additional preference for feature.")

        control_outputs = [control.respond("s", message, turn, 10) for turn, message in enumerate(messages, 1)]
        optimized_outputs = [optimized.respond("s", message, turn, 10) for turn, message in enumerate(messages, 1)]

        self.assertEqual(control_outputs, optimized_outputs)

    def test_clarification_exception_returns_ranked_recommendations(self) -> None:
        agent = self.agent(tracing=True)
        agent.reset("s", {})
        assert agent._question_analyzer is not None
        with patch.object(agent._question_analyzer, "analyze", side_effect=RuntimeError("broken")):
            response = agent.respond("s", "sneakers", 1, 10)

        self.assertEqual(len(response["recommendations"]), 10)
        self.assertIsNone(response["ask_attribute"])
        self.assertTrue(any(item["fallback_used"] for item in agent.trace_recorder.records()))

    def test_missing_clarification_cache_degrades_safely(self) -> None:
        agent = self.agent()
        agent._question_analyzer = None
        agent.reset("s", {})

        response = agent.respond("s", "sneakers", 1, 10)

        self.assertTrue(response["recommendations"])
        self.assertIsNone(response["ask_attribute"])

    def test_corrupt_optional_clarification_data_degrades_safely(self) -> None:
        agent = self.agent()
        agent.reset("s", {})
        assert agent._question_analyzer is not None
        with patch.object(agent._question_analyzer, "analyze", side_effect=ValueError("corrupt cache")):
            response = agent.respond("s", "sneakers", 1, 10)

        self.assertTrue(response["recommendations"])
        self.assertIsNone(response["ask_attribute"])

    def test_tracing_does_not_change_schema_and_can_be_disabled(self) -> None:
        off = self.agent(tracing=False)
        on = self.agent(tracing=True)
        for agent in (off, on):
            agent.reset("s", {})
        off_response = off.respond("s", "sneakers", 1, 10)
        on_response = on.respond("s", "sneakers", 1, 10)

        self.assertEqual(off_response, on_response)
        self.assertEqual(off.trace_recorder.records(), [])
        self.assertTrue(on.trace_recorder.records())

    def test_dense_and_persistence_are_not_initialized(self) -> None:
        agent = self.agent()

        self.assertIsNone(agent._dense_retriever)
        self.assertFalse(agent.phase3_config.uses_persistence)


class ConservativeTopKAllocatorTest(PhaseFiveFixture):
    def setUp(self) -> None:
        super().setUp()
        self.store = CatalogFeatureStore(self.catalog, cache_size=30)
        self.ranked = [_candidate(f"P{index:02d}", index + 1) for index in range(30)]

    def tearDown(self) -> None:
        self.store.close()
        super().tearDown()

    def allocator(self, mode: str = "hedge", **overrides) -> ConservativeTopKAllocator:
        values = {"topk_mode": mode, "hedge_max_score_gap": 0.03}
        values.update(overrides)
        return ConservativeTopKAllocator(self.store, PhaseFiveConfig(**values))

    def test_top_eight_unchanged_and_hedge_is_unique_valid_near_top(self) -> None:
        decision = self.allocator().allocate(self.ranked, SessionState("s"), 10)

        self.assertTrue(decision.activated)
        self.assertEqual(decision.recommendations[:8], tuple(item.parent_asin for item in self.ranked[:8]))
        self.assertEqual(len(decision.recommendations), 10)
        self.assertEqual(len(set(decision.recommendations)), 10)
        self.assertGreaterEqual(decision.replacement_source_rank or 0, 11)
        self.assertLessEqual(decision.replacement_source_rank or 99, 30)

    def test_hard_constraint_is_not_violated_for_diversity(self) -> None:
        state = SessionState("s")
        state.slots["color"] = SlotValue("black", ConstraintStrength.HARD, 1)

        decision = self.allocator().allocate(self.ranked, state, 10)

        self.assertFalse(decision.activated)

    def test_rejected_candidate_never_reenters_as_hedge(self) -> None:
        state = SessionState("s", rejected_product_ids={f"P{index:02d}" for index in range(10, 30)})

        decision = self.allocator().allocate(self.ranked, state, 10)

        self.assertFalse(decision.activated)

    def test_no_hedge_when_window_excludes_alternative(self) -> None:
        decision = self.allocator(hedge_window_end=10).allocate(self.ranked, SessionState("s"), 10)

        self.assertFalse(decision.activated)

    def test_rank_only_exactly_reproduces_control(self) -> None:
        decision = self.allocator("rank_only").allocate(self.ranked, SessionState("s"), 10)

        self.assertEqual(decision.recommendations, tuple(item.parent_asin for item in self.ranked[:10]))
        self.assertFalse(decision.activated)


if __name__ == "__main__":
    unittest.main()

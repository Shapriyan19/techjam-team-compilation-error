from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starter.agent import Agent
from starter.clarification import (
    AttributeQuestionTrace,
    ConservativeQuestionPolicy,
    InformationGainAnalyzer,
    QuestionAnalysis,
)
from starter.clarification_config import PhaseFourConfig
from starter.ranking.config import PhaseThreeConfig
from starter.ranking.features import CatalogFeatureStore, ScoredCandidate
from starter.retrieval.config import RetrievalConfig
from starter.state import ConstraintStrength, SessionState, SlotValue
from starter.understanding import update_state_from_message


def _product(
    identifier: str,
    *,
    color: str | None = None,
    material: str | None = None,
    brand: str | None = None,
    feature: str | None = None,
    price: float | None = None,
    category: str = "Sneakers",
) -> dict:
    descriptors = [value for value in (color, material, feature) if value]
    return {
        "parent_asin": identifier,
        "title": " ".join([*descriptors, category, "shoes"]),
        "features": [feature] if feature else [],
        "description": [],
        "price": price,
        "categories": ["Clothing, Shoes & Jewelry", "Shoes", category],
        "details": {
            **({"Color": color} if color else {}),
            **({"Material": material} if material else {}),
            **({"Brand": brand} if brand else {}),
        },
        "average_rating": 4.0,
        "rating_number": 1,
        "store": brand or "",
    }


def _scored(identifier: str, rank: int) -> ScoredCandidate:
    return ScoredCandidate(identifier, 1.0, rank, ())


def _analysis(score: float = 0.8, attribute: str = "color") -> QuestionAnalysis:
    trace = AttributeQuestionTrace(
        attribute=attribute,
        api_attribute="size" if attribute == "size_fit" else attribute,
        coverage=0.9,
        entropy_before=2.0,
        expected_entropy_after=0.5,
        eig=1.5,
        normalized_eig=0.75,
        intent_relevance=0.8,
        category_relevance=0.8,
        already_known=False,
        already_asked=False,
        no_preference=False,
        raw_question_score=score,
        final_question_score=score,
    )
    return QuestionAnalysis(10, 0.9, 0.15, attribute, (trace,))


class InformationGainAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog_path = Path(self.temporary.name) / "catalog.jsonl"
        products = []
        for index in range(10):
            products.append(_product(
                f"P{index}",
                color="black" if index < 4 else ("white" if index < 8 else None),
                material="cotton" if index == 0 else ("leather" if index == 1 else None),
                brand=f"Brand {index}",
                feature="comfortable" if index % 2 == 0 else "waterproof",
                price=float(20 + index * 10) if index < 2 else None,
            ))
        self.catalog_path.write_text(
            "".join(json.dumps(product) + "\n" for product in products),
            encoding="utf-8",
        )
        self.store = CatalogFeatureStore(self.catalog_path, cache_size=20)
        self.config = PhaseFourConfig(mode="analyze", question_candidate_k=10)
        self.analyzer = InformationGainAnalyzer(self.store, self.config)
        self.candidates = [_scored(f"P{index}", index + 1) for index in range(10)]

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def analyze(self, state: SessionState | None = None) -> QuestionAnalysis:
        return self.analyzer.analyze(self.candidates, state or SessionState("session"))

    def test_high_information_attribute_beats_uniform_attribute(self) -> None:
        analysis = self.analyze()

        self.assertGreater(
            analysis.trace_for("color").final_question_score,
            analysis.trace_for("material").final_question_score,
        )

    def test_low_coverage_reduces_otherwise_strong_split(self) -> None:
        analysis = self.analyze()
        color = analysis.trace_for("color")
        material = analysis.trace_for("material")

        self.assertGreater(material.normalized_eig, 0.0)
        self.assertAlmostEqual(color.coverage, 0.8)
        self.assertAlmostEqual(material.coverage, 0.2)
        self.assertLess(material.coverage, color.coverage)
        self.assertLess(material.final_question_score, color.final_question_score)

    def test_already_known_attribute_is_not_selected(self) -> None:
        state = SessionState("session")
        update_state_from_message(state, "must be black", 1)

        trace = self.analyze(state).trace_for("color")

        self.assertTrue(trace.already_known)
        self.assertEqual(trace.final_question_score, 0.0)

    def test_already_asked_attribute_is_not_selected(self) -> None:
        state = SessionState("session")
        state.record_asked_attribute("color", "color")

        trace = self.analyze(state).trace_for("color")

        self.assertTrue(trace.already_asked)
        self.assertEqual(trace.final_question_score, 0.0)

    def test_no_preference_attribute_is_not_selected_again(self) -> None:
        state = SessionState("session")
        state.record_asked_attribute("color", "color")
        state.record_no_preference_for_last_question()

        trace = self.analyze(state).trace_for("color")

        self.assertTrue(trace.no_preference)
        self.assertEqual(trace.final_question_score, 0.0)

    def test_uniform_attribute_has_zero_useful_gain(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            path = Path(temporary.name) / "uniform.jsonl"
            products = [_product(f"U{index}", material="cotton") for index in range(4)]
            path.write_text("".join(json.dumps(item) + "\n" for item in products), encoding="utf-8")
            store = CatalogFeatureStore(path)
            try:
                analyzer = InformationGainAnalyzer(store, PhaseFourConfig(mode="analyze", question_candidate_k=4))
                analysis = analyzer.analyze([_scored(f"U{index}", index + 1) for index in range(4)], SessionState("s"))
                material = analysis.trace_for("material")
                self.assertAlmostEqual(material.eig, 0.0)
                self.assertAlmostEqual(material.final_question_score, 0.0)
            finally:
                store.close()
        finally:
            temporary.cleanup()

    def test_strongly_splitting_attribute_has_positive_eig(self) -> None:
        color = self.analyze().trace_for("color")

        self.assertGreater(color.eig, 0.0)
        self.assertGreater(color.normalized_eig, 0.0)

    def test_missing_metadata_is_safe(self) -> None:
        candidates = [*self.candidates[:9], _scored("NOT_IN_CATALOG", 10)]

        analysis = self.analyzer.analyze(candidates, SessionState("session"))

        self.assertEqual(analysis.candidate_count, 10)
        self.assertGreaterEqual(analysis.trace_for("color").coverage, 0.0)

    def test_sparse_price_coverage_suppresses_budget_utility(self) -> None:
        budget = self.analyze().trace_for("budget")

        self.assertEqual(budget.coverage, 0.0)
        self.assertEqual(budget.final_question_score, 0.0)

    def test_analysis_is_deterministic(self) -> None:
        first = self.analyze()
        second = self.analyze()

        self.assertEqual(first, second)


class ConservativeQuestionPolicyTest(unittest.TestCase):
    def config(self, **overrides) -> PhaseFourConfig:
        values = {
            "mode": "ask",
            "early_threshold": 0.4,
            "middle_threshold": 0.5,
            "late_threshold": 0.7,
        }
        values.update(overrides)
        return PhaseFourConfig(**values)

    def state(self, turn: int, scenario: str | None = None) -> SessionState:
        state = SessionState("session", turn=turn, active_scenario=scenario)
        return state

    def test_high_usefulness_early_question_is_asked(self) -> None:
        decision = ConservativeQuestionPolicy(self.config()).decide(
            _analysis(0.8), self.state(2)
        )

        self.assertEqual(decision.api_attribute, "color")
        self.assertIsNotNone(decision.message)

    def test_low_usefulness_question_is_not_asked(self) -> None:
        decision = ConservativeQuestionPolicy(self.config()).decide(
            _analysis(0.1), self.state(2)
        )

        self.assertIsNone(decision.api_attribute)

    def test_turn_nine_still_asks(self) -> None:
        # A turn-9 answer still reaches the turn-10 query, so it is worth asking.
        decision = ConservativeQuestionPolicy(self.config()).decide(
            _analysis(), self.state(9)
        )

        self.assertIsNotNone(decision.api_attribute)

    def test_turn_ten_does_not_ask(self) -> None:
        decision = ConservativeQuestionPolicy(self.config()).decide(
            _analysis(), self.state(10)
        )

        self.assertIsNone(decision.api_attribute)

    def test_well_specified_buying_intent_does_not_ask(self) -> None:
        state = self.state(1, "buying")
        for name in ("category", "brand", "color", "material"):
            state.slots[name] = SlotValue("known", ConstraintStrength.HARD, 1)

        decision = ConservativeQuestionPolicy(self.config()).decide(_analysis(), state)

        self.assertIsNone(decision.api_attribute)
        self.assertEqual(decision.reason, "intent already well specified")

    def test_vague_browsing_intent_can_ask_high_value_question(self) -> None:
        decision = ConservativeQuestionPolicy(self.config()).decide(
            _analysis(0.8, "use_case"), self.state(1, "browsing")
        )

        self.assertEqual(decision.api_attribute, "use_case")

    def test_material_answer_prefers_leading_value_for_asked_attribute(self) -> None:
        state = self.state(1)
        state.record_asked_attribute("material", "material")

        update_state_from_message(
            state,
            "For that, what matters is: wool; 44% Acrylic, 28% Cotton, 20% Merino Wool, 8% Polyester.",
            2,
        )

        self.assertEqual(state.slots["material"].value, "wool")


class PhaseFourAgentIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.agents: list[Agent] = []
        self.catalog_path = Path(self.temporary.name) / "catalog.jsonl"
        products = [
            _product(
                f"P{index}",
                color="black" if index % 2 == 0 else "white",
                material="cotton" if index < 5 else "leather",
                brand=f"Brand {index}",
                feature="comfortable" if index % 2 == 0 else "waterproof",
                price=float(40 + index * 5),
                category="Sneakers" if index < 5 else "Heels",
            )
            for index in range(10)
        ]
        self.catalog_path.write_text(
            "".join(json.dumps(product) + "\n" for product in products),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        for agent in self.agents:
            agent.close()
        self.temporary.cleanup()

    def agent(self, mode: str, **config_overrides) -> Agent:
        config = {
            "mode": mode,
            "question_candidate_k": 10,
            "minimum_uncertainty": 0.0,
            "maximum_top_confidence": 1.0,
            "early_threshold": 0.0,
            "middle_threshold": 0.0,
            "late_threshold": 0.0,
            "max_known_attributes": 10,
        }
        config.update(config_overrides)
        agent = Agent(
            self.catalog_path,
            RetrievalConfig(
                mode="hybrid_facet",
                artifact_dir=Path(self.temporary.name) / "missing",
                lexical_top_n=10,
                dense_top_n=10,
                facet_top_n=10,
                dense_weight=0.0,
                validate_artifact_checksums=False,
            ),
            PhaseThreeConfig(mode="rerank", fresh_candidate_limit=10),
            PhaseFourConfig(**config),
        )
        self.agents.append(agent)
        return agent

    def test_analysis_mode_leaves_visible_response_identical_to_off(self) -> None:
        off = self.agent("off")
        analyze = self.agent("analyze")
        off.reset("session", {})
        analyze.reset("session", {})

        off_response = off.respond("session", "shoes and still exploring", 1, 10)
        analyze_response = analyze.respond("session", "shoes and still exploring", 1, 10)

        self.assertEqual(off_response, analyze_response)
        self.assertEqual(len(analyze.session_state("session").question_analysis_history), 1)

    def test_question_and_recommendations_are_returned_together(self) -> None:
        agent = self.agent("ask")
        agent.reset("session", {})

        response = agent.respond("session", "shoes and still exploring", 1, 10)

        self.assertIsNotNone(response["ask_attribute"])
        self.assertTrue(response["recommendations"])
        # Turn 1 falls inside the P19 precision window, which deliberately emits a
        # short list. Assert the full Top-K on a later turn instead, so this still
        # covers "a question and a complete ranking arrive together".
        later = agent.respond("session", "something for running", 3, 10)
        self.assertEqual(len(later["recommendations"]), 10)

    def test_no_preference_suppresses_repeated_internal_attribute(self) -> None:
        agent = self.agent("ask")
        agent.reset("session", {})
        first = agent.respond("session", "shoes and still exploring", 1, 10)
        first_internal = agent.session_state("session").last_asked_attribute

        agent.respond(
            "session",
            f"I don't have an additional preference for {first['ask_attribute']}.",
            2,
            10,
        )

        state = agent.session_state("session")
        self.assertIn(first_internal, state.no_preference_attributes)
        self.assertNotEqual(state.last_asked_attribute, first_internal)

    def test_override_uses_new_state_and_recomputes_analysis(self) -> None:
        agent = self.agent("ask")
        agent.reset("session", {})
        agent.respond("session", "black heels", 1, 10)
        first_recommendations = agent.session_state("session").last_recommendations

        agent.respond("session", "actually sneakers instead", 2, 10)

        state = agent.session_state("session")
        self.assertEqual(state.slots["category"].value, "sneakers")
        self.assertEqual(len(state.question_analysis_history), 2)
        self.assertNotEqual(state.last_recommendations, first_recommendations)

    def test_response_schema_remains_valid(self) -> None:
        agent = self.agent("ask")
        agent.reset("session", {})

        response = agent.respond("session", "shoes and still exploring", 1, 10)

        self.assertIsInstance(response["message"], str)
        self.assertIn(
            response["ask_attribute"],
            {"category", "material", "color", "size", "style", "brand", "budget", "feature", "use_case", "other", None},
        )
        identifiers = [item["parent_asin"] for item in response["recommendations"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starter.agent import Agent
from starter.clarification_config import PhaseFourConfig
from starter.phase5_config import PhaseFiveConfig
from starter.phase6_config import PhaseSixConfig
from starter.ranking.config import PhaseThreeConfig
from starter.ranking.evidence import explicit_rejected_ids
from starter.retrieval.config import RetrievalConfig
from starter.state import ConstraintStrength, SessionState
from starter.topk import AllocationDecision
from starter.understanding import rewrite_query, update_state_from_message


ALLOWED_ASK_ATTRIBUTES = {
    "category", "material", "color", "size", "style", "brand",
    "budget", "feature", "use_case", "other", None,
}


def _product(index: int) -> dict:
    return {
        "parent_asin": f"P{index:02d}",
        "title": f"comfortable black walking shoes {index}",
        "features": ["comfortable", "lightweight"],
        "description": [],
        "price": 40.0 + index,
        "categories": ["Shoes", "Sneakers"],
        "details": {"Color": "black", "Material": "cotton"},
        "average_rating": 4.0,
        "rating_number": 1,
        "store": f"Brand{index}",
    }


class PhaseSevenAgentFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.catalog = self.root / "catalog.jsonl"
        self.catalog.write_text(
            "".join(json.dumps(_product(index)) + "\n" for index in range(30)),
            encoding="utf-8",
        )
        self.catalog_ids = {f"P{index:02d}" for index in range(30)}
        self.agents: list[Agent] = []

    def tearDown(self) -> None:
        for agent in self.agents:
            agent.close()
        self.temporary.cleanup()

    def agent(self, artifact_dir: Path, *, tracing: bool = False) -> Agent:
        agent = Agent(
            self.catalog,
            RetrievalConfig(
                mode="hybrid_facet",
                artifact_dir=artifact_dir,
                lexical_top_n=30,
                facet_top_n=30,
                dense_weight=0.0,
                validate_artifact_checksums=True,
            ),
            PhaseThreeConfig(mode="rerank", fresh_candidate_limit=30),
            PhaseFourConfig(mode="ask", question_candidate_k=20),
            PhaseFiveConfig(tracing_enabled=tracing, topk_mode="rank_only"),
            PhaseSixConfig(),
        )
        self.agents.append(agent)
        return agent

    def assert_valid_response(self, response: dict, top_k: int = 10) -> None:
        self.assertEqual(set(response), {"message", "ask_attribute", "recommendations", "usage"})
        self.assertIsInstance(response["message"], str)
        self.assertIn(response["ask_attribute"], ALLOWED_ASK_ATTRIBUTES)
        identifiers = [item["parent_asin"] for item in response["recommendations"]]
        self.assertLessEqual(len(identifiers), top_k)
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(set(identifiers).issubset(self.catalog_ids))
        self.assertGreaterEqual(response["usage"]["prompt_tokens"], 0)
        self.assertGreaterEqual(response["usage"]["completion_tokens"], 0)


class FallbackMatrixTest(PhaseSevenAgentFixture):
    def test_missing_facet_artifact_uses_lexical_and_returns_valid_output(self) -> None:
        agent = self.agent(self.root / "missing")
        agent.reset("s", {})

        response = agent.respond("s", "comfortable walking shoes", 1, 10)

        self.assert_valid_response(response)
        self.assertIn("disabled", agent.facet_status)

    def test_corrupt_facet_manifest_uses_lexical_fallback(self) -> None:
        corrupt = self.root / "corrupt"
        corrupt.mkdir()
        (corrupt / "manifest.json").write_text("{not-json", encoding="utf-8")
        agent = self.agent(corrupt)
        agent.reset("s", {})

        response = agent.respond("s", "walking shoes", 1, 10)

        self.assert_valid_response(response)
        self.assertIn("disabled", agent.facet_status)

    def test_checksum_mismatch_uses_lexical_fallback(self) -> None:
        mismatch = self.root / "mismatch"
        mismatch.mkdir()
        (mismatch / "manifest.json").write_text(json.dumps({
            "catalog_sha256": "WRONG",
            "facets": {"schema_version": "techjam_safe_facets_v1"},
            "files": {},
        }), encoding="utf-8")
        agent = self.agent(mismatch)
        agent.reset("s", {})

        response = agent.respond("s", "walking shoes", 1, 10)

        self.assert_valid_response(response)

    def test_clarification_failure_returns_recommendations_without_question(self) -> None:
        agent = self.agent(self.root / "missing")
        agent.reset("s", {})
        assert agent._question_analyzer is not None
        with patch.object(agent._question_analyzer, "analyze", side_effect=RuntimeError("failure")):
            response = agent.respond("s", "walking shoes", 1, 10)

        self.assert_valid_response(response)
        self.assertIsNone(response["ask_attribute"])

    def test_allocator_failure_returns_raw_reranker_top_ten(self) -> None:
        agent = self.agent(self.root / "missing")
        agent.reset("s", {})
        assert agent._topk_allocator is not None
        with patch.object(agent._topk_allocator, "allocate", side_effect=RuntimeError("failure")):
            response = agent.respond("s", "walking shoes", 1, 10)

        self.assert_valid_response(response)
        self.assertEqual(len(response["recommendations"]), 10)

    def test_dense_and_semantic_artifacts_not_required_when_disabled(self) -> None:
        agent = self.agent(self.root / "nothing")

        self.assertIsNone(agent._dense_retriever)
        self.assertIsNone(agent._semantic_reranker)

    def test_tracing_off_needs_no_directory_and_produces_no_records(self) -> None:
        agent = self.agent(self.root / "missing", tracing=False)
        agent.reset("s", {})

        response = agent.respond("s", "walking shoes", 1, 10)

        self.assert_valid_response(response)
        self.assertEqual(agent.trace_recorder.records(), [])

    def test_invalid_internal_ids_duplicates_and_empty_values_are_filtered(self) -> None:
        agent = self.agent(self.root / "missing")
        agent.reset("s", {})
        assert agent._topk_allocator is not None
        invalid = AllocationDecision(
            recommendations=("P00", "", "INVALID", "P00", "P01"),
            activated=False,
            changed_positions=(),
            hedge_attribute=None,
            replacement_source_rank=None,
            reason="injected invalid test output",
        )
        with patch.object(agent._topk_allocator, "allocate", return_value=invalid):
            response = agent.respond("s", "walking shoes", 1, 10)

        self.assertEqual(
            [item["parent_asin"] for item in response["recommendations"]],
            ["P00", "P01"],
        )


class ContractValidationTest(PhaseSevenAgentFixture):
    def test_official_method_signatures(self) -> None:
        self.assertEqual(
            list(inspect.signature(Agent.reset).parameters),
            ["self", "session_id", "user_profile"],
        )
        self.assertEqual(
            list(inspect.signature(Agent.respond).parameters),
            ["self", "session_id", "user_message", "turn", "top_k"],
        )

    def test_first_middle_turn_ten_empty_message_and_reset(self) -> None:
        agent = self.agent(self.root / "missing")
        agent.reset("s", {"summary": "first"})
        for turn, message in ((1, ""), (5, "still exploring"), (10, "walking shoes")):
            self.assert_valid_response(agent.respond("s", message, turn, 10))

        agent.reset("s", {"summary": "replacement"})
        self.assertEqual(agent.session_state("s").turn, 0)
        self.assertEqual(agent.session_state("s").history, [])

    def test_multiple_sessions_do_not_leak(self) -> None:
        agent = self.agent(self.root / "missing")
        agent.reset("a", {})
        agent.respond("a", "black shoes", 1, 10)
        agent.reset("b", {})

        self.assertTrue(agent.session_state("a").slots)
        self.assertEqual(agent.session_state("b").slots, {})


class ParserStateRegressionCorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture = Path(__file__).parent / "fixtures" / "phase7_state_regression.json"
        cls.cases = {item["name"]: item for item in json.loads(fixture.read_text(encoding="utf-8"))}

    def state(self, name: str) -> SessionState:
        case = self.cases[name]
        state = SessionState(name)
        if case.get("asked_attribute"):
            state.record_asked_attribute(case["asked_attribute"], case["asked_attribute"])
        for turn, message in enumerate(case["messages"], 1):
            update_state_from_message(state, message, turn)
        rewrite_query(state)
        return state

    def test_accumulation_soft_and_hard_strength(self) -> None:
        accumulated = self.state("accumulation")
        self.assertEqual(accumulated.slots["category"].value, "shoes")
        self.assertEqual(accumulated.slots["budget_max"].value, 80.0)
        self.assertEqual(accumulated.slots["color"].strength, ConstraintStrength.SOFT)
        self.assertEqual(self.state("soft_brand").slots["brand"].strength, ConstraintStrength.SOFT)
        self.assertEqual(self.state("hard_feature").slots["features"].strength, ConstraintStrength.HARD)

    def test_negation_override_and_multiple_overrides_keep_active_state_only(self) -> None:
        negated = self.state("negation")
        self.assertNotIn("material", negated.slots)
        self.assertIn("leather", negated.negative_preferences["material"])
        overridden = self.state("override")
        self.assertEqual(overridden.slots["category"].value, "sneakers")
        colors = self.state("multiple_overrides")
        self.assertEqual(colors.slots["color"].value, "blue")
        self.assertIn("black", colors.negative_preferences["color"])
        self.assertIn("white", colors.negative_preferences["color"])
        self.assertNotIn("black", colors.rewritten_query.casefold())

    def test_no_preference_and_rejections(self) -> None:
        no_preference = self.state("no_preference")
        self.assertIn("color", no_preference.no_preference_attributes)
        self.assertEqual(explicit_rejected_ids("not the first one", ("P00", "P01")), {"P00"})
        style = self.state("style_rejection")
        self.assertNotIn("style", style.slots)
        self.assertIn("formal", style.negative_preferences["style"])

    def test_browsing_boundary_and_feature_language_remains_query_visible(self) -> None:
        self.assertIn("beach holiday", self.state("browsing").rewritten_query.casefold())
        self.assertIn("something nice", self.state("boundary").rewritten_query.casefold())
        feature = self.state("feature_walks")
        self.assertIn("comfortable", feature.slots["features"].value)
        self.assertIn("long walks", feature.rewritten_query.casefold())


if __name__ == "__main__":
    unittest.main()

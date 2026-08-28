from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starter.agent import Agent
from starter.ranking.config import PhaseThreeConfig
from starter.state import ConstraintStrength, PatchOperation, SessionState
from starter.understanding import rewrite_query, update_state_from_message


class PhaseOneStateTest(unittest.TestCase):
    def state_after(self, *messages: str) -> SessionState:
        state = SessionState(session_id="test")
        for turn, message in enumerate(messages, start=1):
            update_state_from_message(state, message, turn)
        return state

    def test_accumulates_category_and_budget_across_turns(self) -> None:
        state = self.state_after("women's running shoes", "under $80")

        self.assertEqual(state.slots["category"].value, "running shoes")
        self.assertEqual(state.slots["budget_max"].value, 80.0)
        self.assertEqual(state.slots["category"].source_turn, 1)
        self.assertEqual(state.slots["budget_max"].source_turn, 2)
        self.assertEqual(len(state.history), 2)

    def test_marks_if_possible_brand_as_soft(self) -> None:
        state = self.state_after("Nike if possible")

        self.assertEqual(state.slots["brand"].value, "Nike")
        self.assertEqual(state.slots["brand"].strength, ConstraintStrength.SOFT)

    def test_marks_must_color_as_hard(self) -> None:
        state = self.state_after("must be black")

        self.assertEqual(state.slots["color"].value, "black")
        self.assertEqual(state.slots["color"].strength, ConstraintStrength.HARD)

    def test_category_override_preserves_independent_slots(self) -> None:
        state = self.state_after("black heels under $100")
        parsed = update_state_from_message(state, "actually sneakers instead", 2)

        self.assertEqual(state.slots["category"].value, "sneakers")
        self.assertNotIn("heels", str(state.slots["category"].value))
        self.assertEqual(state.slots["color"].value, "black")
        self.assertEqual(state.slots["budget_max"].value, 100.0)
        self.assertIn("heels", state.negative_preferences["category"])
        self.assertEqual(state.active_scenario, "intent_override")
        self.assertEqual(len(state.override_history), 1)
        self.assertEqual(
            [patch.operation for patch in parsed.patches],
            [PatchOperation.REMOVE, PatchOperation.RESET_DEPENDENTS, PatchOperation.SET],
        )

    def test_color_override_removes_black_and_sets_white(self) -> None:
        state = self.state_after("black shoes", "actually white instead")

        self.assertEqual(state.slots["category"].value, "shoes")
        self.assertEqual(state.slots["color"].value, "white")
        self.assertIn("black", state.negative_preferences["color"])

    def test_query_rewrite_uses_full_active_state(self) -> None:
        state = self.state_after("shoes for travel", "water-resistant under $80")
        query = rewrite_query(state).casefold()

        for expected in ("shoes", "travel", "water-resistant", "80"):
            self.assertIn(expected, query)

    def test_dependency_reset_removes_size_but_keeps_color(self) -> None:
        state = self.state_after("black heels size 8", "actually sneakers instead")

        self.assertNotIn("size_fit", state.slots)
        self.assertEqual(state.slots["color"].value, "black")
        self.assertEqual(state.slots["category"].value, "sneakers")

    def test_negation_records_negative_preferences_without_active_values(self) -> None:
        state = self.state_after("shoes, not black and no leather")
        query = rewrite_query(state).casefold()

        self.assertNotIn("color", state.slots)
        self.assertNotIn("material", state.slots)
        self.assertIn("black", state.negative_preferences["color"])
        self.assertIn("leather", state.negative_preferences["material"])
        self.assertNotIn("black", query)
        self.assertNotIn("leather", query)

    def test_features_accumulate(self) -> None:
        state = self.state_after("comfortable shoes", "waterproof and lightweight")

        self.assertEqual(
            state.slots["features"].value,
            ("comfortable", "waterproof", "lightweight"),
        )

    def test_rejection_is_tracked_but_not_used_as_a_positive_query(self) -> None:
        state = self.state_after("black shoes", "Those options are not quite right yet.")
        query = rewrite_query(state).casefold()

        self.assertEqual(len(state.rejected_information), 1)
        self.assertIn("shoes", query)
        self.assertIn("black", query)
        self.assertNotIn("options", query)


class AgentPhaseOneIntegrationTest(unittest.TestCase):
    def test_reset_replaces_state_and_sessions_do_not_leak(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.jsonl"
            products = [
                {"parent_asin": "A", "title": "Black running shoes"},
                {"parent_asin": "B", "title": "White sneakers"},
            ]
            catalog_path.write_text(
                "".join(json.dumps(product) + "\n" for product in products),
                encoding="utf-8",
            )
            agent = Agent(catalog_path, phase3_config=PhaseThreeConfig(mode="off"))

            agent.reset("one", {"summary": "first"})
            agent.respond("one", "black shoes", 1, 10)
            self.assertIn("category", agent.session_state("one").slots)

            agent.reset("two", {"summary": "second"})
            self.assertEqual(agent.session_state("two").slots, {})
            self.assertEqual(agent.session_state("two").history, [])

            agent.reset("one", {"summary": "replacement"})
            self.assertEqual(agent.session_state("one").slots, {})
            self.assertEqual(agent.session_state("one").history, [])
            self.assertEqual(agent.session_state("one").user_profile["summary"], "replacement")

    def test_agent_retrieval_query_contains_prior_turn_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.jsonl"
            catalog_path.write_text(
                json.dumps({"parent_asin": "A", "title": "Waterproof travel shoes under 80"}) + "\n",
                encoding="utf-8",
            )
            agent = Agent(catalog_path, phase3_config=PhaseThreeConfig(mode="off"))
            agent.reset("session", {})

            agent.respond("session", "shoes for travel", 1, 10)
            response = agent.respond("session", "water-resistant under $80", 2, 10)
            query = agent.session_state("session").rewritten_query.casefold()

            self.assertTrue(response["recommendations"])
            for expected in ("shoes", "travel", "water-resistant", "80"):
                self.assertIn(expected, query)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starter.agent import Agent
from starter.clarification_config import PhaseFourConfig
from starter.llm.client import RerankReply, RerankRequest
from starter.llm.config import PhaseSixConfig
from starter.llm.rerank import SemanticReranker, _resolve_order
from starter.ranking.config import PhaseThreeConfig
from starter.ranking.features import CatalogFeatureStore, ScoredCandidate
from starter.retrieval.config import RetrievalConfig
from starter.runtime_config import PhaseFiveConfig
from starter.state import SessionState
from starter.understanding import update_state_from_message


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


def _scored(identifier: str, rank: int) -> ScoredCandidate:
    return ScoredCandidate(identifier, 1.0 / rank, rank, ())


class _RecordingClient:
    """Deterministic stand-in for the Anthropic SDK; never touches the network."""

    def __init__(self, order: tuple[int, ...] = (), error: Exception | None = None) -> None:
        self.order = order
        self.error = error
        self.requests: list[RerankRequest] = []

    def rerank(self, request: RerankRequest) -> RerankReply:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return RerankReply(order=self.order, prompt_tokens=120, completion_tokens=30)


class OrderResolutionTest(unittest.TestCase):
    def test_valid_permutation_is_preserved(self) -> None:
        self.assertEqual(_resolve_order([3, 1, 2], 3), [2, 0, 1])

    def test_missing_entries_are_appended_in_original_order(self) -> None:
        self.assertEqual(_resolve_order([2], 4), [1, 0, 2, 3])

    def test_duplicates_and_out_of_range_values_are_ignored(self) -> None:
        self.assertEqual(_resolve_order([1, 1, 99, 0, -4, 2], 3), [0, 1, 2])

    def test_empty_model_output_keeps_the_deterministic_order(self) -> None:
        self.assertEqual(_resolve_order([], 3), [0, 1, 2])


class SemanticRerankerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        catalog_path = Path(self.directory.name) / "catalog.jsonl"
        catalog_path.write_text(
            "".join(
                json.dumps(_product(name, f"{name} walking shoe", price=index * 10.0)) + "\n"
                for index, name in enumerate("ABCD", start=1)
            ),
            encoding="utf-8",
        )
        self.store = CatalogFeatureStore(catalog_path)
        self.addCleanup(self.store.close)
        self.addCleanup(self.directory.cleanup)

    def make_state(self, message: str = "black walking shoes") -> SessionState:
        state = SessionState(session_id="session")
        update_state_from_message(state, message, 1)
        return state

    def reranker(self, client, **overrides) -> SemanticReranker:
        config = PhaseSixConfig(mode=overrides.pop("mode", "rerank"), **overrides)
        return SemanticReranker(self.store, config, client)

    def test_model_ordering_is_applied_in_rerank_mode(self) -> None:
        client = _RecordingClient(order=(3, 1, 2, 4))
        result = self.reranker(client).rerank(
            [_scored(name, rank) for rank, name in enumerate("ABCD", start=1)],
            self.make_state(),
        )

        self.assertTrue(result.applied)
        self.assertEqual(result.ordered, ("C", "A", "B", "D"))
        self.assertEqual(result.usage, {"prompt_tokens": 120, "completion_tokens": 30})

    def test_shadow_mode_records_usage_without_changing_order(self) -> None:
        client = _RecordingClient(order=(4, 3, 2, 1))
        result = self.reranker(client, mode="shadow").rerank(
            [_scored(name, rank) for rank, name in enumerate("ABCD", start=1)],
            self.make_state(),
        )

        self.assertFalse(result.applied)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.prompt_tokens, 120)

    def test_client_failure_falls_back_to_the_deterministic_order(self) -> None:
        client = _RecordingClient(error=TimeoutError("upstream timeout"))
        candidates = [_scored(name, rank) for rank, name in enumerate("ABCD", start=1)]

        result = self.reranker(client).rerank(candidates, self.make_state())

        self.assertFalse(result.applied)
        self.assertIn("TimeoutError", result.status)
        self.assertEqual(result.ordered, ("A", "B", "C", "D"))
        self.assertEqual(result.usage, {"prompt_tokens": 0, "completion_tokens": 0})

    def test_session_call_budget_is_enforced(self) -> None:
        client = _RecordingClient(order=(1, 2, 3, 4))
        reranker = self.reranker(client, max_calls_per_session=2)
        state = self.make_state()
        candidates = [_scored(name, rank) for rank, name in enumerate("ABCD", start=1)]

        statuses = [reranker.rerank(candidates, state).status for _ in range(4)]

        self.assertEqual(statuses.count("ok"), 2)
        self.assertEqual(statuses[-1], "session call budget spent")
        self.assertEqual(len(client.requests), 2)

    def test_turn_window_is_respected(self) -> None:
        client = _RecordingClient(order=(1, 2, 3, 4))
        reranker = self.reranker(client, first_turn=2, last_turn=3)
        state = self.make_state()
        state.turn = 9

        result = reranker.rerank(
            [_scored(name, rank) for rank, name in enumerate("ABCD", start=1)],
            state,
        )

        self.assertEqual(result.status, "outside turn window")
        self.assertEqual(client.requests, [])

    def test_prompt_contains_state_and_candidates_but_no_labels(self) -> None:
        client = _RecordingClient(order=(1, 2))
        self.reranker(client).rerank(
            [_scored("A", 1), _scored("B", 2)],
            self.make_state("black walking shoes under $50"),
        )

        prompt = client.requests[0].user_message
        self.assertIn("A walking shoe", prompt)
        self.assertIn("black", prompt)
        self.assertIn("Return JSON", prompt)
        for forbidden in ("ground_truth", "target", "sample_id", "hit_rate"):
            self.assertNotIn(forbidden, prompt)

    def test_shortlist_is_capped(self) -> None:
        client = _RecordingClient(order=(1, 2))
        self.reranker(client, shortlist_size=2).rerank(
            [_scored(name, rank) for rank, name in enumerate("ABCD", start=1)],
            self.make_state(),
        )

        self.assertEqual(client.requests[0].user_message.count("\n1. "), 1)
        self.assertNotIn("3. ", client.requests[0].user_message)


class PhaseSixConfigTest(unittest.TestCase):
    def test_default_mode_is_off_and_makes_no_calls(self) -> None:
        config = PhaseSixConfig()

        self.assertEqual(config.mode, "off")
        self.assertFalse(config.calls_model)
        self.assertFalse(config.applies_ordering)

    def test_unsupported_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PhaseSixConfig(mode="magic")

    def test_default_model_is_the_current_claude_opus(self) -> None:
        self.assertEqual(PhaseSixConfig().model, "claude-opus-5")


class AgentSemanticRerankIntegrationTest(unittest.TestCase):
    def make_agent(self, directory: str, mode: str, client) -> Agent:
        catalog_path = Path(directory) / "catalog.jsonl"
        products = [
            _product("A", "Black leather walking shoe"),
            _product("B", "Blue running sneaker"),
            _product("C", "White canvas shoe"),
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
            PhaseFiveConfig(mode="trace"),
            PhaseSixConfig(mode=mode),
            rerank_client=client,
        )

    def test_disabled_by_default_and_reports_zero_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = _RecordingClient(order=(3, 2, 1))
            agent = self.make_agent(directory, "off", client)
            try:
                response = agent.respond("session", "black walking shoes", 1, 10)
            finally:
                agent.close()

        self.assertEqual(agent.semantic_rerank_status, "disabled")
        self.assertEqual(client.requests, [])
        self.assertEqual(response["usage"], {"prompt_tokens": 0, "completion_tokens": 0})

    def test_rerank_mode_reorders_and_reports_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = _RecordingClient(order=(3, 2, 1))
            agent = self.make_agent(directory, "rerank", client)
            try:
                agent.reset("session", {})
                response = agent.respond("session", "black walking shoes", 1, 10)
                trace = agent.last_trace()
            finally:
                agent.close()

        self.assertEqual(agent.semantic_rerank_status, "ready")
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(response["usage"]["prompt_tokens"], 120)
        self.assertEqual(response["usage"]["completion_tokens"], 30)
        self.assertEqual(trace["fallback_tier"], "full")
        identifiers = [item["parent_asin"] for item in response["recommendations"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_llm_failure_keeps_the_response_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = _RecordingClient(error=RuntimeError("no credentials"))
            agent = self.make_agent(directory, "rerank", client)
            try:
                agent.reset("session", {})
                response = agent.respond("session", "black walking shoes", 1, 10)
                trace = agent.last_trace()
            finally:
                agent.close()

        self.assertTrue(response["recommendations"])
        self.assertEqual(trace["fallback_tier"], "semantic_rerank_fallback")
        self.assertIn("semantic_rerank", trace["degraded_stages"])
        self.assertEqual(response["usage"], {"prompt_tokens": 0, "completion_tokens": 0})

    def test_missing_credentials_disable_the_route_at_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.jsonl"
            catalog_path.write_text(json.dumps(_product("A", "Shoe")) + "\n", encoding="utf-8")
            agent = Agent(
                catalog_path,
                RetrievalConfig(
                    artifact_dir=Path(directory) / "missing-artifacts",
                    validate_artifact_checksums=False,
                ),
                PhaseThreeConfig(mode="rerank", fresh_candidate_limit=10),
                PhaseFourConfig(mode="off"),
                PhaseFiveConfig(mode="off"),
                PhaseSixConfig(mode="rerank", api_key_variable="TECHJAM_ABSENT_KEY"),
            )
            try:
                agent.reset("session", {})
                response = agent.respond("session", "shoe", 1, 10)
            finally:
                agent.close()

        self.assertTrue(agent.semantic_rerank_status.startswith("disabled:"))
        self.assertTrue(response["recommendations"])


if __name__ == "__main__":
    unittest.main()

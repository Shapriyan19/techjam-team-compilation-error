from __future__ import annotations

import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from starter.agent import Agent
from starter.clarification_config import PhaseFourConfig
from starter.llm.client import RerankReply, RerankRequest
from starter.llm.client import LLMUnavailable
from starter.llm.config import PhaseSixConfig
from starter.llm.nvidia_client import NvidiaRerankClient
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

    def test_nvidia_provider_supplies_its_own_defaults(self) -> None:
        environment = {"TECHJAM_LLM_PROVIDER": "nvidia"}
        with unittest.mock.patch.dict(os.environ, environment, clear=False):
            for name in ("TECHJAM_LLM_MODEL", "TECHJAM_LLM_KEY_VARIABLE", "TECHJAM_LLM_BASE_URL"):
                os.environ.pop(name, None)
            config = PhaseSixConfig.from_environment()

        self.assertEqual(config.provider, "nvidia")
        self.assertEqual(config.model, "openai/gpt-oss-120b")
        self.assertEqual(config.api_key_variable, "NVIDIA_API_KEY")
        self.assertEqual(config.base_url, "https://integrate.api.nvidia.com/v1")

    def test_unsupported_provider_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PhaseSixConfig(provider="gemini")

    def test_nvidia_provider_requires_a_base_url(self) -> None:
        with self.assertRaises(ValueError):
            PhaseSixConfig(provider="nvidia", base_url="")


class _StubTransport:
    """Stands in for the HTTP POST, so no test opens a socket."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []

    def __call__(self, payload: dict) -> dict:
        self.payloads.append(payload)
        if not self.responses:
            raise AssertionError("unexpected extra request")
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _completion(content: str, prompt_tokens: int = 100, completion_tokens: int = 20) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


class NvidiaRerankClientTest(unittest.TestCase):
    config = PhaseSixConfig(
        mode="rerank",
        provider="nvidia",
        model="openai/gpt-oss-120b",
        api_key_variable="TECHJAM_TEST_NVIDIA_KEY",
        base_url="https://integrate.api.nvidia.com/v1",
    )
    request = RerankRequest(system="rank these", user_message="1. Shoe\n2. Boot")

    def make_client(self, transport, config: PhaseSixConfig | None = None) -> NvidiaRerankClient:
        with unittest.mock.patch.dict(os.environ, {"TECHJAM_TEST_NVIDIA_KEY": "nvapi-test"}):
            return NvidiaRerankClient(config or self.config, transport=transport)

    def test_missing_key_is_unavailable_before_any_request(self) -> None:
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TECHJAM_TEST_NVIDIA_KEY", None)
            with self.assertRaises(LLMUnavailable):
                NvidiaRerankClient(self.config, transport=_StubTransport())

    def test_schema_request_returns_order_and_usage(self) -> None:
        transport = _StubTransport(_completion('{"order": [2, 1]}'))
        reply = self.make_client(transport).rerank(self.request)

        self.assertEqual(reply.order, (2, 1))
        self.assertEqual(reply.prompt_tokens, 100)
        self.assertEqual(reply.completion_tokens, 20)
        payload = transport.payloads[0]
        self.assertEqual(payload["model"], "openai/gpt-oss-120b")
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["messages"][0]["content"], "rank these")

    def test_schema_rejection_falls_back_to_json_mode(self) -> None:
        from starter.llm.nvidia_client import _ParameterRejected

        transport = _StubTransport(
            _ParameterRejected("response_format", "400"), _completion('{"order": [1, 2]}')
        )
        client = self.make_client(transport)

        self.assertEqual(client.rerank(self.request).order, (1, 2))
        self.assertEqual(transport.payloads[1]["response_format"], {"type": "json_object"})
        self.assertIn("JSON object", transport.payloads[1]["messages"][0]["content"])
        # The downgrade sticks, so the next call does not pay for another rejection.
        transport.responses.append(_completion('{"order": [2, 1]}'))
        client.rerank(self.request)
        self.assertEqual(transport.payloads[2]["response_format"], {"type": "json_object"})

    def test_rejected_reasoning_effort_is_dropped_and_the_schema_kept(self) -> None:
        from starter.llm.nvidia_client import _ParameterRejected

        transport = _StubTransport(
            _ParameterRejected("reasoning_effort", "400: unknown field"),
            _completion('{"order": [2, 1]}'),
        )
        client = self.make_client(transport)

        self.assertEqual(client.rerank(self.request).order, (2, 1))
        self.assertNotIn("reasoning_effort", transport.payloads[1])
        self.assertEqual(transport.payloads[1]["response_format"]["type"], "json_schema")

    def test_thinking_that_eats_the_token_budget_reports_the_cause(self) -> None:
        truncated = {
            "choices": [
                {
                    "message": {"content": "", "reasoning_content": "thinking " * 100},
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 3238, "completion_tokens": 2048},
        }
        with self.assertRaises(LLMUnavailable) as caught:
            self.make_client(_StubTransport(truncated)).rerank(self.request)

        self.assertIn("truncated", str(caught.exception))

    def test_wrapped_json_is_parsed(self) -> None:
        transport = _StubTransport(_completion('Here you go:\n```json\n{"order": [2, 1]}\n```'))

        self.assertEqual(self.make_client(transport).rerank(self.request).order, (2, 1))

    def test_transient_failure_is_retried_then_reported_as_unavailable(self) -> None:
        from starter.llm.nvidia_client import _RetryableError

        transport = _StubTransport(_RetryableError("HTTP 503"), _RetryableError("HTTP 503"))
        client = self.make_client(transport)
        with unittest.mock.patch("starter.llm.nvidia_client.time.sleep"):
            with self.assertRaises(LLMUnavailable):
                client.rerank(self.request)

        self.assertEqual(len(transport.payloads), self.config.max_retries + 1)

    def test_empty_content_is_unavailable(self) -> None:
        transport = _StubTransport(_completion("   "))
        with self.assertRaises(LLMUnavailable):
            self.make_client(transport).rerank(self.request)

    def test_refusal_is_unavailable(self) -> None:
        transport = _StubTransport({"choices": [{"message": {"refusal": "no"}}], "usage": {}})
        with self.assertRaises(LLMUnavailable):
            self.make_client(transport).rerank(self.request)


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
            # Point the OAuth profile lookup at an empty directory and drop the
            # bearer token, so no credential source can resolve on any host.
            environment = {"ANTHROPIC_CONFIG_DIR": str(Path(directory) / "empty-config")}
            with unittest.mock.patch.dict(os.environ, environment, clear=False):
                os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
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

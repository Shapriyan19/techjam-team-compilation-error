"""Measure Phase 6 call volume and prompt size without calling a model.

Runs the official evaluator with a recording client that returns the identity
permutation, so ranking is unchanged and the resulting metrics must match the
deterministic control exactly. Every request that *would* have been sent is
captured and summarized, which gives a cost estimate before any spend.

    python -m scripts.phase6_dryrun
    python -m scripts.phase6_dryrun --exact-tokens   # uses free count_tokens API

`--exact-tokens` needs ANTHROPIC_API_KEY but calls only the token-counting
endpoint, which is not billed.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path

from evaluator import local_evaluator as LE
from starter.agent import Agent
from starter.llm.client import RerankReply, RerankRequest
from starter.llm.config import PhaseSixConfig


# Published Claude Opus 5 rates, USD per million tokens.
MODEL_RATES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


class RecordingClient:
    """Captures requests and returns the identity order, so ranking is unchanged."""

    def __init__(self) -> None:
        self.requests: list[RerankRequest] = []

    def rerank(self, request: RerankRequest) -> RerankReply:
        self.requests.append(request)
        # Identity permutation: the reranker leaves the deterministic order alone.
        return RerankReply(order=(), prompt_tokens=0, completion_tokens=0)


def _estimate_output_tokens(shortlist_size: int) -> int:
    """A JSON array of N integers, plus adaptive thinking overhead at low effort."""
    json_tokens = shortlist_size * 3 + 16
    return json_tokens + 200


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 6 dry-run cost estimate")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--limit", type=int, default=None, help="only first N sessions")
    parser.add_argument("--exact-tokens", action="store_true", help="use count_tokens API")
    parser.add_argument("--output", default=None, help="write JSON summary here")
    args = parser.parse_args()

    config = PhaseSixConfig.from_environment()
    if not config.calls_model:
        config = PhaseSixConfig(**{**config.__dict__, "mode": "rerank"})

    samples = LE.load_jsonl(args.dataset)
    if args.limit:
        samples = samples[: args.limit]
    catalog_ids, categories, products = LE.catalog_index(args.catalog)

    recorder = RecordingClient()
    agent = Agent(args.catalog, phase6_config=config, rerank_client=recorder)
    result = LE.evaluate(agent, samples, catalog_ids, categories, products)

    requests = recorder.requests
    if not requests:
        print("No Phase 6 requests were produced. Check the mode and turn window.")
        return

    prompt_chars = [len(item.system) + len(item.user_message) for item in requests]
    # ~3.6 chars/token is a reasonable prior for dense English + product metadata.
    prompt_tokens = [round(value / 3.6) for value in prompt_chars]
    source = "estimated from characters"

    if args.exact_tokens:
        try:
            import anthropic

            client = anthropic.Anthropic()
            sampled = requests[:: max(1, len(requests) // 25)][:25]
            counted = [
                client.messages.count_tokens(
                    model=config.model,
                    system=item.system,
                    messages=[{"role": "user", "content": item.user_message}],
                ).input_tokens
                for item in sampled
            ]
            ratio = statistics.fmean(counted) / statistics.fmean(
                len(item.system) + len(item.user_message) for item in sampled
            )
            prompt_tokens = [round(value * ratio) for value in prompt_chars]
            source = f"calibrated on {len(counted)} count_tokens samples"
        except Exception as exc:
            print(f"count_tokens unavailable ({type(exc).__name__}: {exc}); using estimate")

    output_tokens = [_estimate_output_tokens(config.shortlist_size)] * len(requests)
    total_in = sum(prompt_tokens)
    total_out = sum(output_tokens)
    in_rate, out_rate = MODEL_RATES.get(config.model, MODEL_RATES["claude-opus-5"])
    cost = total_in / 1e6 * in_rate + total_out / 1e6 * out_rate

    scale = len(LE.load_jsonl(args.dataset)) / len(samples)

    summary = {
        "model": config.model,
        "mode": config.mode,
        "sessions": len(samples),
        "requests": len(requests),
        "requests_per_session": round(len(requests) / len(samples), 3),
        "shortlist_size": config.shortlist_size,
        "max_calls_per_session": config.max_calls_per_session,
        "turn_window": [config.first_turn, config.last_turn],
        "prompt_tokens": {
            "source": source,
            "total": total_in,
            "mean": round(statistics.fmean(prompt_tokens)),
            "median": round(statistics.median(prompt_tokens)),
            "max": max(prompt_tokens),
        },
        "output_tokens": {"assumed_per_call": output_tokens[0], "total": total_out},
        "estimated_cost_usd": {
            "this_run": round(cost, 2),
            "full_200_sessions": round(cost * scale, 2),
        },
        "identity_check": {
            "hit_rate_at_10": result["hit_rate_at_10"],
            "technical_score": result["recommended_technical_score"],
            "note": "must equal the deterministic control; the recorder reorders nothing",
        },
    }

    print(json.dumps(summary, indent=2))
    if args.output:
        Path(args.output).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

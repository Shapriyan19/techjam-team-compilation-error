"""Paired control-vs-LLM measurement on a small session pilot.

Session-count metrics (HR@10, MRR) are far too noisy at n=30 to settle whether
Phase 6 earns its latency. The decisive instrument here is the paired per-call
record: for every LLM call, where did the true target sit in the deterministic
order, and where did the model put it? That is one observation per call rather
than per session, and both arms see the identical candidate list, so it isolates
the model's contribution from retrieval variance.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from evaluator import local_evaluator as ev
from starter.agent import Agent
from starter.llm.config import PhaseSixConfig
from starter.llm.rerank import SemanticReranker


CALL_LOG: list[dict] = []
_CURRENT: dict[str, str] = {}


def _install_probes() -> None:
    """Record the target's rank before and after each LLM call."""
    original_materialize = ev.materialize_hidden_fields
    original_rerank = SemanticReranker.rerank

    def materialize(sample, products):
        _CURRENT["target"] = str(sample["ground_truth"]["parent_asin"])
        _CURRENT["scenario"] = str(sample["scenario_type"])
        return original_materialize(sample, products)

    def rerank(self, candidates, state):
        baseline = [c.parent_asin for c in candidates]
        result = original_rerank(self, candidates, state)
        if result.status != "ok":
            return result
        target = _CURRENT.get("target", "")
        before = baseline.index(target) + 1 if target in baseline else None
        after = result.ordered.index(target) + 1 if target in result.ordered else None
        CALL_LOG.append({
            "scenario": _CURRENT.get("scenario"),
            "turn": state.turn,
            "shortlist": min(len(baseline), self.config.shortlist_size),
            "rank_before": before,
            "rank_after": after,
            "moved_positions": result.moved_positions,
        })
        return result

    ev.materialize_hidden_fields = materialize
    SemanticReranker.rerank = rerank


def run(dataset: str, catalog: str, phase6: PhaseSixConfig) -> dict:
    samples = ev.load_jsonl(dataset)
    catalog_ids, categories, products = ev.catalog_index(catalog)
    agent = Agent(catalog, phase6_config=phase6)
    print(f"  semantic_rerank_status: {agent.semantic_rerank_status}")
    return ev.evaluate(agent, samples, catalog_ids, categories, products)


def summarize(name: str, result: dict) -> dict:
    return {
        "arm": name,
        "hit_rate_at_10": result["hit_rate_at_10"],
        "mrr": result["mrr"],
        "mttc": result["mttc"],
        "technical_score": result["recommended_technical_score"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/pilot_set.jsonl")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--provider", default="nvidia")
    parser.add_argument("--shortlist", type=int, default=40)
    parser.add_argument("--max-calls", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--output", default="results_phase6_pilot.json")
    args = parser.parse_args()

    _install_probes()

    print("Control arm (Phase 6 off)...")
    control = run(args.dataset, args.catalog, PhaseSixConfig(mode="off"))
    print(f"  {summarize('control', control)}")

    import os
    os.environ["TECHJAM_LLM_PROVIDER"] = args.provider
    base = PhaseSixConfig.from_environment()
    treatment_config = PhaseSixConfig(**{
        **base.__dict__,
        "mode": "rerank",
        "shortlist_size": args.shortlist,
        "max_calls_per_session": args.max_calls,
        "timeout_seconds": args.timeout,
    })
    print(f"\nTreatment arm (Phase 6 rerank, {args.provider} {treatment_config.model})...")
    treatment = run(args.dataset, args.catalog, treatment_config)
    print(f"  {summarize('treatment', treatment)}")

    scored = [c for c in CALL_LOG if c["rank_before"] and c["rank_after"]]
    deltas = [c["rank_before"] - c["rank_after"] for c in scored]
    report = {
        "dataset": args.dataset,
        "provider": args.provider,
        "model": treatment_config.model,
        "arms": [summarize("control", control), summarize("treatment", treatment)],
        "calls_total": len(CALL_LOG),
        "calls_with_target_in_shortlist": len(scored),
        "target_moved_up": sum(1 for d in deltas if d > 0),
        "target_moved_down": sum(1 for d in deltas if d < 0),
        "target_unchanged": sum(1 for d in deltas if d == 0),
        "mean_rank_delta": round(statistics.fmean(deltas), 3) if deltas else None,
        "median_rank_delta": statistics.median(deltas) if deltas else None,
        "promoted_into_top10": sum(
            1 for c in scored if c["rank_before"] > 10 >= c["rank_after"]
        ),
        "demoted_out_of_top10": sum(
            1 for c in scored if c["rank_after"] > 10 >= c["rank_before"]
        ),
        "calls": CALL_LOG,
        "control_sessions": control["sessions"],
        "treatment_sessions": treatment["sessions"],
    }
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\n" + json.dumps(
        {k: v for k, v in report.items()
         if k not in {"calls", "control_sessions", "treatment_sessions"}},
        indent=2,
    ))


if __name__ == "__main__":
    main()

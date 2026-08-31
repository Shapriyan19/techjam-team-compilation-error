"""Run the untouched official evaluator with label-free runtime observation.

Labels are joined AFTER evaluation, here only, never passed to the agent. Detail
timings overlap their parent timings and must not be summed across levels.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import time
import re
from collections import Counter

import numpy as np

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl, materialize_hidden_fields
from starter.agent import Agent, _terms
from starter.improvements import ImprovementConfig
from starter.neural import NeuralConfig
from starter.retrieval.config import RetrievalConfig


EXPERIMENTS = {
    "control": ImprovementConfig(optimize=False),
    "optimized": ImprovementConfig(),
    "category": ImprovementConfig(category_evidence=True),
    "category_guard": ImprovementConfig(category_evidence=True, category_guard_only=True),
    "evidence": ImprovementConfig(active_evidence=True),
    "recent": ImprovementConfig(recent_query=True),
    "confidence": ImprovementConfig(evidence_confidence=True),
    "combined": ImprovementConfig(category_evidence=True, active_evidence=True,
                                  recent_query=True, evidence_confidence=True),
    "guarded": ImprovementConfig(category_evidence=True, category_guard_only=True, active_evidence=True,
                                 recent_query=True, evidence_confidence=True),
}


class ObservedAgent(Agent):
    def __init__(self, *args, **kwargs):
        self.observations = []
        self._fresh_snapshot = []
        self.paraphrase = False
        super().__init__(*args, **kwargs)

    def reset(self, session_id, user_profile):
        super().reset(session_id, user_profile)
        self.observations.append([])

    def _fresh_retrieval(self, query, limit, state):
        result = super()._fresh_retrieval(query, limit, state)
        self._fresh_snapshot = [item.parent_asin for item in result]
        return result

    def respond(self, session_id, user_message, turn, top_k):
        self._fresh_snapshot = []
        if self.paraphrase:
            # Discourse-only stress: leave product attributes and constraints
            # intact. This is not an independent/private test set.
            for source, target in [
                (r"I'm looking for", "I'd like to find"),
                (r"what matters is", "my preference is"),
                (r"A key requirement is", "I need"),
                (r"but I'm still exploring", "and I'm considering options"),
            ]:
                user_message = re.sub(source, target, user_message, flags=re.I)
        response = super().respond(session_id, user_message, turn, top_k)
        state = self.session_state(session_id)
        self.observations[-1].append({
            "turn": turn, "message": user_message, "query": state.rewritten_query,
            "unique_query_terms": len(set(_terms(state.rewritten_query))),
            "fresh": self._fresh_snapshot,
            "ranked": [item[0] for item in state.last_candidate_scores],
            "returned": [item["parent_asin"] for item in response["recommendations"]],
            "trace": self.last_trace(),
        })
        return response


def _rank(items, target):
    return items.index(target) + 1 if target in items else None


def diagnostics(agent, samples, products):
    records = []
    stages = {}
    by_turn = {}
    misses = Counter()
    truncated = 0
    for sample, turns in zip(samples, agent.observations):
        target = str(sample["ground_truth"]["parent_asin"])
        _, behavior = materialize_hidden_fields(sample, products)
        first_eligible = int((behavior.get("override") or {}).get("turn", 3)) if sample["scenario_type"] == "intent_override" else 1
        analyzed = []
        for row in turns:
            for name, value in row["trace"]["stage_milliseconds"].items():
                stages.setdefault(name, []).append(value)
            eligible = row["turn"] >= first_eligible
            fresh = _rank(row["fresh"], target)
            ranked = _rank(row["ranked"], target)
            returned = _rank(row["returned"], target)
            truncated += row["unique_query_terms"] > 40
            counts = by_turn.setdefault(row["turn"], Counter())
            if eligible:
                counts["eligible_observed_sessions"] += 1
                counts["candidate_hits"] += fresh is not None
                counts["shortlist40_hits"] += ranked is not None and ranked <= 40
                counts["top10_hits"] += returned is not None
            analyzed.append({
                "turn": row["turn"], "eligible": eligible, "query": row["query"],
                "message": row["message"], "fresh_rank": fresh, "reranked_rank": ranked,
                "returned_rank": returned, "unique_query_terms": row["unique_query_terms"],
                "question_reason": row["trace"]["question_reason"],
            })
        eligible_turns = [row for row in analyzed if row["eligible"]]
        if not any(row["returned_rank"] is not None for row in eligible_turns):
            misses["retrieved_but_not_top10" if any(row["fresh_rank"] is not None for row in eligible_turns)
                   else "never_retrieved_when_eligible"] += 1
        records.append({"sample_id": sample["sample_id"], "scenario": sample["scenario_type"], "turns": analyzed})
    return {
        "startup_seconds": agent.startup_seconds,
        "timing_note": "Per-turn times exclude startup; detail stages overlap retrieval_ranking. Diagnostic instrumentation adds overhead.",
        "stage_ms": {name: {"mean": float(np.mean(values)), "p50": float(np.percentile(values, 50)),
                             "p95": float(np.percentile(values, 95))} for name, values in stages.items()},
        "lexical_cache": {"hits": agent.lexical_cache_hits, "misses": agent.lexical_cache_misses},
        "truncated_query_turns": truncated,
        "miss_decomposition": dict(misses),
        "by_turn": by_turn,
        "denominator_note": "Conditional on sessions still running; later turns do not have a fixed 200-session denominator.",
        "sessions": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", choices=EXPERIMENTS, required=True)
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output", required=True)
    parser.add_argument("--neural", choices=["off", "dense", "cross_encoder", "shadow"], default="off")
    parser.add_argument("--neural-backend", choices=["torch", "onnx"], default="torch")
    parser.add_argument("--retrieval-artifacts", default="artifacts/retrieval")
    parser.add_argument("--paraphrase", action="store_true")
    args = parser.parse_args()
    samples = load_jsonl(args.dataset)
    ids, categories, products = catalog_index(args.catalog)
    started = time.perf_counter()
    agent = ObservedAgent(args.catalog, improvements=EXPERIMENTS[args.experiment],
                          retrieval_config=replace(RetrievalConfig.from_environment(), artifact_dir=Path(args.retrieval_artifacts)),
                          neural_config=NeuralConfig(mode=args.neural, backend=args.neural_backend))
    agent.paraphrase = args.paraphrase
    try:
        if args.neural != "off" and agent.local_neural_status != "ready":
            raise RuntimeError(agent.local_neural_status)
        result = evaluate(agent, samples, ids, categories, products)
        if args.neural != "off" and agent.local_neural_status != "ready":
            raise RuntimeError("Neural experiment degraded: " + agent.local_neural_status)
        report = diagnostics(agent, samples, products)
        report.update(experiment=args.experiment, configuration=asdict(agent.improvements),
                      neural=args.neural,
                      neural_backend=args.neural_backend,
                      paraphrase_stress=args.paraphrase,
                      catalog=args.catalog,
                      elapsed_seconds=time.perf_counter()-started)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        output.with_suffix(".diagnostics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in result.items() if key != "sessions"}, indent=2))
        print(json.dumps({key: value for key, value in report.items() if key != "sessions"}, indent=2))
    finally:
        agent.close()


if __name__ == "__main__":
    main()

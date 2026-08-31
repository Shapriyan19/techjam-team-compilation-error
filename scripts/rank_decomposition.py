"""Why does the target lose? Feature-level decomposition of near-misses.

Hooks the deterministic scorer and, on the turn the evaluator scores, records the
target's feature vector alongside the vector of whatever outranked it. Averaged
over many sessions this says which weight is actually buying the wrong answer.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
from pathlib import Path

from evaluator import local_evaluator as ev
from starter.agent import Agent
from starter.llm.config import PhaseSixConfig
from starter.ranking.features import DeterministicFeatureScorer


_CURRENT: dict = {}
ROWS: list[dict] = []


def _install() -> None:
    original_materialize = ev.materialize_hidden_fields
    original_rank = DeterministicFeatureScorer.rank

    def materialize(sample, products):
        _CURRENT.clear()
        _CURRENT.update(
            target=str(sample["ground_truth"]["parent_asin"]),
            scenario=str(sample["scenario_type"]),
            sample_id=sample["sample_id"],
            turn=0,
        )
        return original_materialize(sample, products)

    def rank(self, fresh_candidates, state, evidence=None):
        scored = original_rank(self, fresh_candidates, state, evidence)
        target = _CURRENT.get("target")
        if not target:
            return scored
        _CURRENT["turn"] = state.turn
        order = [c.parent_asin for c in scored]
        _CURRENT["last"] = {
            "rank": order.index(target) + 1 if target in order else None,
            "pool": len(order),
            "target_features": dict(scored[order.index(target)].features) if target in order else None,
            "top_features": [dict(c.features) for c in scored[:3]],
            "top_ids": order[:3],
            "target_score": scored[order.index(target)].score if target in order else None,
            "top_score": scored[0].score if scored else None,
        }
        return scored

    def record_session():
        if "last" in _CURRENT:
            ROWS.append({**{k: _CURRENT[k] for k in ("sample_id", "scenario", "turn")}, **_CURRENT["last"]})

    ev.materialize_hidden_fields = materialize
    DeterministicFeatureScorer.rank = rank
    return record_session


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/synthetic_set3.jsonl")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--output", default="decomposition.json")
    args = parser.parse_args()

    record_session = _install()
    samples = ev.load_jsonl(args.dataset)
    catalog_ids, categories, products = ev.catalog_index(args.catalog)
    agent = Agent(args.catalog, phase6_config=PhaseSixConfig(mode="off"))

    # Evaluate one sample at a time so each session's final state is captured.
    for sample in samples:
        ev.evaluate(agent, [sample], catalog_ids, categories, products)
        record_session()

    def bucket(row):
        r = row["rank"]
        if r is None:
            return "not_in_pool"
        if r == 1:
            return "rank1"
        if r <= 10:
            return "rank2_10"
        return "rank_gt10"

    groups = collections.defaultdict(list)
    for row in ROWS:
        groups[bucket(row)].append(row)

    print(f"sessions analysed: {len(ROWS)}")
    for name in ("rank1", "rank2_10", "rank_gt10", "not_in_pool"):
        print(f"  {name:12s} {len(groups[name]):4d}")

    summary = {"counts": {k: len(v) for k, v in groups.items()}}
    for name in ("rank2_10", "rank_gt10"):
        rows = [r for r in groups[name] if r["target_features"]]
        if not rows:
            continue
        keys = sorted(rows[0]["target_features"])
        print(f"\n=== {name}: n={len(rows)}  mean score gap "
              f"{statistics.fmean(r['top_score'] - r['target_score'] for r in rows):.4f}")
        print(f"{'feature':22s} {'winner':>9s} {'target':>9s} {'winner-target':>14s}")
        deltas = {}
        for key in keys:
            w = statistics.fmean(r["top_features"][0][key] for r in rows)
            t = statistics.fmean(r["target_features"][key] for r in rows)
            deltas[key] = w - t
            print(f"{key:22s} {w:9.4f} {t:9.4f} {w - t:14.4f}")
        summary[name] = {"n": len(rows), "deltas": deltas}
        print("  largest winner advantages:",
              ", ".join(f"{k} {v:+.3f}" for k, v in sorted(deltas.items(), key=lambda kv: -kv[1])[:4]))

    Path(args.output).write_text(json.dumps({"summary": summary, "rows": ROWS}, indent=2) + "\n")
    print(f"\nwrote {args.output}")


if __name__ == "__main__":
    main()

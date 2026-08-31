"""Which signals separate the target from the items ranked above it?

P18-E003 failed because its motivating probe compared the target only against the
rank-1 item - both already survivors of retrieval. A signal can look strong there
and still be useless, because the population it must actually rank is the whole
candidate pool, where sparse listings win precision trivially.

This probe therefore scores every candidate signal on two axes at once:

  discrimination - over sessions where the target lands at rank 2-10, how often
                   does the signal put the target above the items beating it?
  pool safety    - does the signal favour the target over a random sample of the
                   pool, or does it favour arbitrary pool items? A signal that
                   discriminates but is high across the pool will promote junk.

A signal is only worth building into a feature if it wins on both.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

from evaluator import local_evaluator as ev
from starter.agent import Agent
from starter.llm.config import PhaseSixConfig
from starter.ranking.features import DeterministicFeatureScorer, _tokens


_CURRENT: dict = {}
PAIRS: list[dict] = []
POOL: list[dict] = []


def signals(product, query_terms, rarity, store) -> dict[str, float]:
    """Candidate discriminators, all computable from frozen catalog data."""
    record = store.describe(product.parent_asin) or {}
    title_terms = _tokens(record.get("title", ""))
    matched = query_terms & product.all_terms
    doc_mass = rarity.mass(product.all_terms) or 1.0
    query_mass = rarity.mass(query_terms) or 1.0
    matched_title = query_terms & title_terms
    return {
        # how much of the query the listing accounts for
        "coverage_mass": rarity.mass(matched) / query_mass,
        # the single rarest thing the shopper said that this product has
        "rarest_matched": max((rarity.weight(t) for t in matched), default=0.0),
        # same, restricted to the title - titles are curated, descriptions are noise
        "title_coverage": rarity.mass(matched_title) / query_mass,
        "title_rarest": max((rarity.weight(t) for t in matched_title), default=0.0),
        # precision, the P18-E003 signal, kept for comparison
        "evidence_density": rarity.mass(matched) / doc_mass,
        # listing shape
        "doc_terms": float(len(product.all_terms)),
        "title_terms": float(len(title_terms)),
        "has_price": float(product.price is not None),
        "n_features": float(len(product.feature_values)),
        "n_categories": float(len(product.category_values)),
        "category_depth": float(len(product.category_values)),
    }


def _install(rarity_holder: dict) -> None:
    original_materialize = ev.materialize_hidden_fields
    original_rank = DeterministicFeatureScorer.rank

    def materialize(sample, products):
        _CURRENT.clear()
        _CURRENT.update(target=str(sample["ground_truth"]["parent_asin"]))
        return original_materialize(sample, products)

    def rank(self, fresh_candidates, state, evidence=None):
        scored = original_rank(self, fresh_candidates, state, evidence)
        rarity_holder["r"] = self.rarity
        target = _CURRENT.get("target")
        if not target or not scored or self.rarity is None:
            return scored
        query_terms: set[str] = set()
        for fragment in state.verbatim_fragments:
            query_terms |= _tokens(fragment)
        if not query_terms:
            return scored
        order = [c.parent_asin for c in scored]
        if target not in order:
            return scored
        position = order.index(target)
        _CURRENT["snapshot"] = (position, order, query_terms, self)
        return scored

    ev.materialize_hidden_fields = materialize
    DeterministicFeatureScorer.rank = rank


def flush(rng: random.Random) -> None:
    snapshot = _CURRENT.get("snapshot")
    if not snapshot:
        return
    position, order, query_terms, scorer = snapshot
    if not 1 <= position <= 9:  # target at rank 2-10 only
        return
    target = _CURRENT["target"]
    rarity = scorer.rarity
    target_product = scorer.store.get(target)
    if target_product is None:
        return
    target_signals = signals(target_product, query_terms, rarity, scorer.store)
    for beat_id in order[:position]:
        product = scorer.store.get(beat_id)
        if product is None:
            continue
        PAIRS.append({
            "target": target_signals,
            "above": signals(product, query_terms, rarity, scorer.store),
        })
    for pool_id in rng.sample(order, min(20, len(order))):
        if pool_id == target:
            continue
        product = scorer.store.get(pool_id)
        if product is None:
            continue
        POOL.append({
            "target": target_signals,
            "pool": signals(product, query_terms, rarity, scorer.store),
        })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/synthetic_set3.jsonl")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--output", default="pool_probe.json")
    args = parser.parse_args()

    rng = random.Random(2026)
    holder: dict = {}
    _install(holder)
    samples = ev.load_jsonl(args.dataset)
    catalog_ids, categories, products = ev.catalog_index(args.catalog)
    agent = Agent(args.catalog, phase6_config=PhaseSixConfig(mode="off"))
    for sample in samples:
        _CURRENT.pop("snapshot", None)
        ev.evaluate(agent, [sample], catalog_ids, categories, products)
        flush(rng)

    if not PAIRS:
        print("no rank-2-10 sessions captured")
        return
    keys = sorted(PAIRS[0]["target"])
    print(f"comparison pairs (target vs items above it): {len(PAIRS)}")
    print(f"pool pairs (target vs random pool member):   {len(POOL)}\n")
    print(f"{'signal':20s} {'beats above':>12s} {'beats pool':>11s} {'verdict':>10s}")
    rows = {}
    for key in keys:
        above_win = statistics.fmean(
            1.0 if p["target"][key] > p["above"][key] else 0.0 if p["target"][key] < p["above"][key] else 0.5
            for p in PAIRS
        )
        pool_win = statistics.fmean(
            1.0 if p["target"][key] > p["pool"][key] else 0.0 if p["target"][key] < p["pool"][key] else 0.5
            for p in POOL
        ) if POOL else float("nan")
        # useful only if it beats the items above AND stays above the wider pool
        verdict = "USE" if above_win > 0.55 and pool_win > 0.55 else (
            "unsafe" if above_win > 0.55 else "flat")
        rows[key] = {"above_win": above_win, "pool_win": pool_win, "verdict": verdict}
        print(f"{key:20s} {above_win:12.3f} {pool_win:11.3f} {verdict:>10s}")
    Path(args.output).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {args.output}")
    print("beats above > 0.5 means the signal ranks the target over the items currently beating it.")
    print("beats pool  > 0.5 means it does not simply promote arbitrary pool members.")


if __name__ == "__main__":
    main()

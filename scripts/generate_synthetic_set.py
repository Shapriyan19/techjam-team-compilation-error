from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from evaluator.local_evaluator import load_jsonl


# Matches the observed public_set.jsonl distribution exactly (see
# docs/EXPERIMENT_LOG.md / data snapshot): scenario_type determines
# difficulty_bucket 1:1, and category_bucket is constant because the catalog
# is entirely "Clothing, Shoes & Jewelry".
SCENARIO_MIX = (
    ("buying", 0.40, "easy"),
    ("browsing", 0.40, "medium"),
    ("intent_override", 0.15, "hard"),
    ("boundary", 0.05, "medium"),
)
CATEGORY_BUCKET = "clothing"
PURCHASE_FREQUENCY = "3-4 prior purchases"

# average_prior_rating -> rating_style is a deterministic mapping in the
# observed data: 5.0 always "usually positive", 4.0 always "mixed", and
# 1.0/2.0/3.0 always "critical".
RATING_WEIGHTS = {5.0: 134, 3.0: 22, 4.0: 21, 1.0: 14, 2.0: 9}


def _rating_style(rating: float) -> str:
    if rating == 5.0:
        return "usually positive"
    if rating == 4.0:
        return "mixed"
    return "critical"


TAG_WEIGHTS = {
    "fit": 163,
    "material": 154,
    "comfort": 144,
    "style": 101,
    "durability": 47,
    "performance": 26,
    "warmth": 18,
    "weather": 12,
}
TAG_COUNT_WEIGHTS = {1: 6, 2: 43, 3: 30, 4: 121}
GENERAL_SHOPPING_PROBABILITY = 1 / 200


def _weighted_choice(rng: random.Random, weights: dict) -> object:
    keys = list(weights.keys())
    values = list(weights.values())
    return rng.choices(keys, weights=values, k=1)[0]


def _weighted_sample_without_replacement(rng: random.Random, weights: dict, k: int) -> list[str]:
    pool = dict(weights)
    chosen: list[str] = []
    for _ in range(min(k, len(pool))):
        keys = list(pool.keys())
        vals = list(pool.values())
        pick = rng.choices(keys, weights=vals, k=1)[0]
        chosen.append(pick)
        del pool[pick]
    return chosen


def _build_user_profile(rng: random.Random) -> dict:
    if rng.random() < GENERAL_SHOPPING_PROBABILITY:
        tags = ["general shopping"]
    else:
        count = _weighted_choice(rng, TAG_COUNT_WEIGHTS)
        tags = _weighted_sample_without_replacement(rng, TAG_WEIGHTS, count)
    rating = _weighted_choice(rng, RATING_WEIGHTS)
    style = _rating_style(rating)
    return {
        "average_prior_rating": rating,
        "preference_tags": tags,
        "purchase_frequency": PURCHASE_FREQUENCY,
        "rating_style": style,
        "summary": f"Prior purchases emphasize {', '.join(tags)}; ratings are {style}.",
    }


def _scenario_assignment(count: int) -> list[str]:
    """Largest-remainder rounding so the mix sums to exactly `count`."""
    raw = [(name, count * fraction) for name, fraction, _ in SCENARIO_MIX]
    base = [(name, int(value)) for name, value in raw]
    remainder = count - sum(n for _, n in base)
    fractional = sorted(
        ((value - int(value), name) for name, value in raw), reverse=True
    )
    bump = {name for _, name in fractional[:remainder]}
    counts = {name: n + (1 if name in bump else 0) for name, n in base}
    assignment: list[str] = []
    for name, _, _ in SCENARIO_MIX:
        assignment.extend([name] * counts[name])
    return assignment


def generate(
    catalog_path: str | Path,
    count: int,
    seed: int,
    prefix: str,
    exclude: set[str],
) -> list[dict]:
    rng = random.Random(seed)
    difficulty_by_scenario = {name: difficulty for name, _, difficulty in SCENARIO_MIX}

    candidates: list[str] = []
    with Path(catalog_path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            product = json.loads(line)
            parent_asin = str(product.get("parent_asin", "")).strip()
            if not parent_asin or parent_asin in exclude:
                continue
            if not str(product.get("title") or "").strip():
                continue
            candidates.append(parent_asin)

    if count > len(candidates):
        raise ValueError(f"requested {count} samples but only {len(candidates)} eligible products exist")

    targets = rng.sample(candidates, count)
    scenarios = _scenario_assignment(count)
    rng.shuffle(scenarios)

    samples: list[dict] = []
    for index, (target, scenario) in enumerate(zip(targets, scenarios), start=1):
        samples.append(
            {
                "category_bucket": CATEGORY_BUCKET,
                "difficulty_bucket": difficulty_by_scenario[scenario],
                "ground_truth": {"parent_asin": target},
                "sample_id": f"{prefix}_{index:04d}",
                "scenario_type": scenario,
                "user_profile": _build_user_profile(rng),
            }
        )
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a synthetic dataset in the public_set.jsonl format from the catalog."
    )
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--prefix", default="synthetic")
    parser.add_argument("--output", default="data/synthetic_set.jsonl")
    parser.add_argument(
        "--exclude-existing",
        action="append",
        default=None,
        help=(
            "Path to a public_set-format file whose targets should never be reused; "
            "repeatable. Defaults to data/public_set.jsonl. Pass an empty string to disable."
        ),
    )
    args = parser.parse_args()

    exclude_paths = args.exclude_existing if args.exclude_existing is not None else ["data/public_set.jsonl"]
    exclude: set[str] = set()
    for raw_path in exclude_paths:
        if not raw_path:
            continue
        existing_path = Path(raw_path)
        if existing_path.exists():
            exclude.update(
                str(sample["ground_truth"]["parent_asin"])
                for sample in load_jsonl(existing_path)
            )

    samples = generate(args.catalog, args.count, args.seed, args.prefix, exclude)

    output_path = Path(args.output)
    with output_path.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample) + "\n")

    print(f"Wrote {len(samples)} samples to {output_path}")
    print(f"Excluded {len(exclude)} existing targets from selection" if exclude else "No exclusion set applied")


if __name__ == "__main__":
    main()

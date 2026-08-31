"""Bake sessions to JSON so the demo works with nothing running.

    python -m demo.recorder                 # the curated demo set
    python -m demo.recorder --all           # all 200, and check parity
    python -m demo.recorder --sessions public_0023,public_0104

With `--all`, every recorded hit / first_hit_turn / best_rank is compared
against `results.json`. That comparison is the correctness gate for
`demo.session_runner`: it proves the runner reproduces the scored evaluator
loop rather than approximating it. A mismatch is a bug in the runner.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from demo.session_runner import run_session
from evaluator.local_evaluator import catalog_index, load_jsonl
from starter.agent import Agent


RECORDINGS = Path(__file__).parent / "recordings"

# One per scenario, chosen from results.json for what each makes visible.
CURATED = {
    "public_0001": "Hard constraint up front, then three turns of narrowing.",
    "public_0021": "Vague opener - watch the slot map fill from nothing.",
    "public_0004": "Intent override on turn 3 rewrites the query and converts.",
    "public_0104": "Customer has no preference, and the agent stops asking.",
}
FEATURED = "public_0004"


def _resolve(samples: list[dict], requested: list[str] | None, record_all: bool) -> list[dict]:
    if record_all:
        return samples
    wanted = set(requested or CURATED)
    selected = [sample for sample in samples if str(sample["sample_id"]) in wanted]
    missing = wanted - {str(sample["sample_id"]) for sample in selected}
    if missing:
        raise SystemExit(f"unknown sample ids: {', '.join(sorted(missing))}")
    return selected


def _parity(payloads: list[dict], results_path: Path) -> list[str]:
    """Compare recorded outcomes against the scored ones. Empty means clean."""
    if not results_path.exists():
        return [f"{results_path} not found - run the evaluator first to enable the parity check"]
    scored = {
        str(item["sample_id"]): item
        for item in json.loads(results_path.read_text(encoding="utf-8")).get("sessions", [])
    }
    problems: list[str] = []
    for payload in payloads:
        expected = scored.get(payload["sample_id"])
        if expected is None:
            continue
        for field in ("hit", "first_hit_turn", "best_rank"):
            if payload[field] != expected[field]:
                problems.append(
                    f"{payload['sample_id']}: {field} recorded={payload[field]!r} "
                    f"scored={expected[field]!r}"
                )
    return problems


def _featured_override(payloads: list[dict]) -> str | None:
    """Pick the override session where the override visibly does something.

    The override turn is `rng.choice([3, 4])` seeded from the sample id, so which
    session demos best is only knowable after a run. In most of them the target
    already sits at rank 1 and the override merely unlocks scoring - true to the
    rules, but flat to watch. The ones worth showing are those where the new
    intent actually lands a slot and rewrites the query. Prefer, in order:
    a hit, a slot added or changed on the override turn, a turn-3 override
    (leaving room to recover), a short session, and a strong final rank.
    """
    def score(payload: dict) -> tuple:
        if payload["scenario_type"] != "intent_override" or not payload["hit"]:
            return (0,)
        override_turn = payload.get("override_turn")
        turn = next(
            (item for item in payload["turns"] if item["turn"] == override_turn), None
        )
        moved = bool(turn) and bool(turn["slots_added"] or turn["slots_changed"])
        return (
            1,
            int(moved),
            int(override_turn == 3),
            -payload["turn_count"],
            -(payload["best_rank"] or 99),
        )

    ranked = sorted(payloads, key=score, reverse=True)
    return ranked[0]["sample_id"] if ranked and score(ranked[0])[0] else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Record sessions for the demo viewer")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--results", default="results.json")
    parser.add_argument("--sessions", help="comma-separated sample ids")
    parser.add_argument("--all", action="store_true", help="record all sessions and check parity")
    args = parser.parse_args()

    samples = load_jsonl(args.dataset)
    catalog_ids, categories, products = catalog_index(args.catalog)
    selected = _resolve(samples, args.sessions.split(",") if args.sessions else None, args.all)

    agent = Agent(args.catalog)
    print(f"index ready in {agent.runtime_stats()['startup_seconds']:.1f}s")

    RECORDINGS.mkdir(parents=True, exist_ok=True)
    payloads: list[dict] = []
    for position, sample in enumerate(selected, start=1):
        payload = run_session(agent, sample, catalog_ids, categories, products)
        payloads.append(payload)
        (RECORDINGS / f"{payload['sample_id']}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        outcome = f"hit turn {payload['first_hit_turn']} rank {payload['best_rank']}" \
            if payload["hit"] else "miss"
        print(f"[{position}/{len(selected)}] {payload['sample_id']:<14} "
              f"{payload['scenario_type']:<16} {outcome}")

    featured = _featured_override(payloads) or FEATURED
    manifest = {
        "featured": featured if featured in {p["sample_id"] for p in payloads} else payloads[0]["sample_id"],
        "sessions": [
            {
                "sample_id": payload["sample_id"],
                "scenario_type": payload["scenario_type"],
                "hit": payload["hit"],
                "first_hit_turn": payload["first_hit_turn"],
                "best_rank": payload["best_rank"],
                "turn_count": payload["turn_count"],
                "override_turn": payload["override_turn"],
                "note": CURATED.get(payload["sample_id"], ""),
            }
            for payload in payloads
        ],
    }
    (RECORDINGS / "index.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {len(payloads)} recording(s) to {RECORDINGS}")
    print(f"featured session: {manifest['featured']}")

    problems = _parity(payloads, Path(args.results))
    if problems:
        print("\nPARITY FAILURES against the scored evaluator:")
        for problem in problems:
            print(f"  {problem}")
        raise SystemExit(1)
    print("parity with results.json: clean")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _sessions(path: Path) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(item["sample_id"]): item for item in payload.get("sessions", [])}


def _headline(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        key: payload.get(key)
        for key in (
            "hit_rate_at_10",
            "mrr",
            "mttc",
            "efficiency",
            "recommended_technical_score",
        )
    }


def compare(control_path: Path, candidate_path: Path) -> dict:
    control = _sessions(control_path)
    candidate = _sessions(candidate_path)
    shared = sorted(set(control) & set(candidate))

    new_hits: list[dict] = []
    lost_hits: list[dict] = []
    better_rank: list[dict] = []
    worse_rank: list[dict] = []
    earlier_turn: list[dict] = []
    later_turn: list[dict] = []

    for sample_id in shared:
        before, after = control[sample_id], candidate[sample_id]
        record = {
            "sample_id": sample_id,
            "scenario_type": after.get("scenario_type"),
            "control": {"rank": before.get("best_rank"), "turn": before.get("first_hit_turn")},
            "candidate": {"rank": after.get("best_rank"), "turn": after.get("first_hit_turn")},
        }
        if after["hit"] and not before["hit"]:
            new_hits.append(record)
            continue
        if before["hit"] and not after["hit"]:
            lost_hits.append(record)
            continue
        if not (before["hit"] and after["hit"]):
            continue
        if after["best_rank"] < before["best_rank"]:
            better_rank.append(record)
        elif after["best_rank"] > before["best_rank"]:
            worse_rank.append(record)
        if after["first_hit_turn"] < before["first_hit_turn"]:
            earlier_turn.append(record)
        elif after["first_hit_turn"] > before["first_hit_turn"]:
            later_turn.append(record)

    control_headline = _headline(control_path)
    candidate_headline = _headline(candidate_path)
    return {
        "control": {"path": str(control_path), **control_headline},
        "candidate": {"path": str(candidate_path), **candidate_headline},
        "delta": {
            key: round(candidate_headline[key] - control_headline[key], 6)
            for key in control_headline
            if isinstance(control_headline[key], (int, float))
            and isinstance(candidate_headline[key], (int, float))
        },
        "summary": {
            "compared_sessions": len(shared),
            "new_hits": len(new_hits),
            "lost_hits": len(lost_hits),
            "better_ranks": len(better_rank),
            "worse_ranks": len(worse_rank),
            "earlier_turns": len(earlier_turn),
            "later_turns": len(later_turn),
        },
        "new_hits": new_hits,
        "lost_hits": lost_hits,
        "better_ranks": better_rank,
        "worse_ranks": worse_rank,
        "earlier_turns": earlier_turn,
        "later_turns": later_turn,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two evaluator result files session by session.")
    parser.add_argument("control")
    parser.add_argument("candidate")
    parser.add_argument("--output")
    parser.add_argument("--brief", action="store_true")
    args = parser.parse_args()

    report = compare(Path(args.control), Path(args.candidate))
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.brief:
        print(json.dumps({key: report[key] for key in ("control", "candidate", "delta", "summary")}, indent=2))
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from evaluator.local_evaluator import (
    catalog_index,
    coarse_category,
    evaluate,
    initial_message,
    load_jsonl,
    materialize_hidden_fields,
)
from starter.agent import Agent


class ObservedAgent(Agent):
    """Agent wrapper that preserves evaluator reset order for trace/sample correlation."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.reset_order: list[str] = []

    def reset(self, session_id: str, user_profile: dict) -> None:
        super().reset(session_id, user_profile)
        self.reset_order.append(session_id)


def _first_turn(
    agent: ObservedAgent,
    samples: list[dict],
    categories: dict[str, list[str]],
    products: dict[str, dict],
) -> None:
    for index, sample in enumerate(samples):
        session_id = f"diagnostic_{index:04d}"
        agent.reset(session_id, sample["user_profile"])
        card, behavior = materialize_hidden_fields(sample, products)
        effective = {**sample, "intent_card": card, "behavior": behavior}
        target = str(sample["ground_truth"]["parent_asin"])
        disclosed: set[str] = set()
        message = initial_message(effective, coarse_category(categories.get(target, [])), disclosed)
        agent.respond(session_id, message, 1, 10)


def _rank(identifier: str, recommendations: list[str]) -> int | None:
    try:
        return recommendations.index(identifier) + 1
    except ValueError:
        return None


def _score_distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "minimum": None, "p25": None, "median": None, "p75": None, "maximum": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "minimum": round(ordered[0], 8),
        "p25": round(ordered[(len(ordered) - 1) // 4], 8),
        "median": round(statistics.median(ordered), 8),
        "p75": round(ordered[(3 * (len(ordered) - 1)) // 4], 8),
        "maximum": round(ordered[-1], 8),
    }


def _diagnostics(
    agent: ObservedAgent,
    samples: list[dict],
    result: dict | None,
) -> dict:
    outcome_by_id = {
        item["sample_id"]: item
        for item in (result or {}).get("sessions", [])
    }
    question_attributes: Counter[str] = Counter()
    question_api_attributes: Counter[str] = Counter()
    questions_by_scenario: Counter[str] = Counter()
    decision_reasons: Counter[str] = Counter()
    chosen_attributes: Counter[str] = Counter()
    question_turns: list[int] = []
    first_turn_scores: list[float] = []
    first_turn_scores_by_scenario: dict[str, list[float]] = defaultdict(list)
    repeated_prevented = 0
    no_preference_respected = 0
    no_preference_answers = 0
    session_records: list[dict] = []

    for sample, session_id in zip(samples, agent.reset_order):
        state = agent.session_state(session_id)
        scenario = str(sample["scenario_type"])
        target = str(sample["ground_truth"]["parent_asin"])
        turns = state.phase4_turn_history
        analyses = state.question_analysis_history
        repeated_prevented += state.repeated_questions_prevented
        no_preference_respected += state.no_preference_responses_respected
        for analysis in analyses:
            chosen = analysis.get("chosen_best_attribute")
            if chosen:
                chosen_attributes[str(chosen)] += 1
            if analysis.get("turn") == 1 and chosen:
                trace = next(
                    (item for item in analysis.get("attributes", []) if item.get("attribute") == chosen),
                    None,
                )
                if trace:
                    value = float(trace["final_question_score"])
                    first_turn_scores.append(value)
                    first_turn_scores_by_scenario[scenario].append(value)
        causality: list[dict] = []
        for index, turn in enumerate(turns):
            decision_reasons[str(turn.get("decision_reason"))] += 1
            attribute = turn.get("ask_attribute")
            if not attribute:
                continue
            api_attribute = str(turn.get("api_attribute"))
            question_attributes[str(attribute)] += 1
            question_api_attributes[api_attribute] += 1
            questions_by_scenario[scenario] += 1
            question_turns.append(int(turn["turn"]))
            next_turn = turns[index + 1] if index + 1 < len(turns) else None
            answer = "" if next_turn is None else str(next_turn.get("user_message", ""))
            no_preference = "preference" in answer.casefold() and (
                "don't have" in answer.casefold() or "use your judgment" in answer.casefold()
            )
            no_preference_answers += int(no_preference)
            causality.append({
                "question_turn": turn["turn"],
                "attribute": attribute,
                "api_attribute": api_attribute,
                "score": turn.get("question_score"),
                "threshold": turn.get("question_threshold"),
                "target_rank_before_answer": _rank(target, list(turn.get("recommendations", []))),
                "answer": answer or None,
                "answer_was_no_preference": no_preference,
                "next_query": None if next_turn is None else next_turn.get("rewritten_query"),
                "slots_after_answer": None if next_turn is None else next_turn.get("slots"),
                "target_rank_after_answer": (
                    None
                    if next_turn is None
                    else _rank(target, list(next_turn.get("recommendations", [])))
                ),
            })
        session_records.append({
            "sample_id": sample["sample_id"],
            "scenario_type": scenario,
            "target": target,
            "outcome": outcome_by_id.get(sample["sample_id"]),
            "asked_attributes": sorted(state.asked_attributes),
            "no_preference_attributes": sorted(state.no_preference_attributes),
            "repeated_questions_prevented": state.repeated_questions_prevented,
            "no_preference_responses_respected": state.no_preference_responses_respected,
            "turns": turns,
            "question_analyses": analyses,
            "question_causality": causality,
        })

    total_questions = sum(question_attributes.values())
    session_count = len(samples)
    summary = {
        "session_count": session_count,
        "total_questions": total_questions,
        "average_questions_per_session": round(total_questions / session_count, 6) if session_count else 0.0,
        "average_question_turn": round(statistics.fmean(question_turns), 6) if question_turns else None,
        "questions_by_scenario": dict(sorted(questions_by_scenario.items())),
        "questions_by_attribute": dict(sorted(question_attributes.items())),
        "questions_by_api_attribute": dict(sorted(question_api_attributes.items())),
        "chosen_attributes_all_analyses": dict(sorted(chosen_attributes.items())),
        "decision_reasons": dict(sorted(decision_reasons.items())),
        "repeated_questions_prevented": repeated_prevented,
        "no_preference_answers": no_preference_answers,
        "no_preference_responses_respected": no_preference_respected,
        "first_turn_best_score_distribution": _score_distribution(first_turn_scores),
        "first_turn_best_score_by_scenario": {
            name: _score_distribution(values)
            for name, values in sorted(first_turn_scores_by_scenario.items())
        },
    }
    return {"summary": summary, "sessions": session_records}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the official evaluator with Phase 4 diagnostics.")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output", default="artifacts/evaluation/phase4_diagnostic_result.json")
    parser.add_argument("--diagnostics", default="artifacts/evaluation/phase4_diagnostics.json")
    parser.add_argument("--first-turn-only", action="store_true")
    args = parser.parse_args()

    samples = load_jsonl(args.dataset)
    catalog_ids, categories, products = catalog_index(args.catalog)
    agent = ObservedAgent(args.catalog)
    try:
        result = None
        if args.first_turn_only:
            _first_turn(agent, samples, categories, products)
        else:
            result = evaluate(agent, samples, catalog_ids, categories, products)
            Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        diagnostics = _diagnostics(agent, samples, result)
        Path(args.diagnostics).write_text(json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(diagnostics["summary"], indent=2))
    finally:
        agent.close()


if __name__ == "__main__":
    main()

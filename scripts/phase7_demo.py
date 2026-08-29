from __future__ import annotations

import argparse
import json
from pathlib import Path

from starter.agent import Agent


MESSAGES = (
    "I need shoes for an upcoming trip.",
    "Comfortable and waterproof, under $80.",
)


def _patches(state) -> list[dict]:
    return [
        {
            "operation": patch.operation.value,
            "slot": patch.slot,
            "value": list(patch.value) if isinstance(patch.value, tuple) else patch.value,
            "strength": patch.strength.value,
            "source_turn": patch.source_turn,
            "confidence": patch.confidence,
            "reason": patch.reason,
        }
        for patch in state.last_patches
    ]


def _active_slots(state) -> dict:
    return {
        name: {
            "value": list(slot.value) if isinstance(slot.value, tuple) else slot.value,
            "strength": slot.strength.value,
            "source_turn": slot.source_turn,
            "confidence": slot.confidence,
        }
        for name, slot in state.slots.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Produce the final two-turn demonstration trace")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--output", default="artifacts/evaluation/p7_demo.json")
    args = parser.parse_args()

    agent = Agent(args.catalog)
    session_id = "phase7-demo"
    agent.reset(session_id, {})
    turns = []
    try:
        for turn, message in enumerate(MESSAGES, start=1):
            response = agent.respond(session_id, message, turn, 10)
            state = agent.session_state(session_id)
            analysis = state.question_analysis_history[-1]
            chosen = analysis.get("chosen_best_attribute")
            chosen_trace = next(
                (item for item in analysis.get("attributes", []) if item["attribute"] == chosen),
                None,
            )
            scored = agent._last_scored_candidates.get(session_id, [])
            turns.append({
                "turn": turn,
                "user_message": message,
                "parsed_state_patch": _patches(state),
                "active_state": _active_slots(state),
                "rewritten_query": state.rewritten_query,
                "retrieval": {
                    "routes": {
                        "lexical": {"top_n": agent.retrieval_config.lexical_top_n, "weight": 1.0},
                        "facet": {"top_n": agent.retrieval_config.facet_top_n, "weight": 0.55},
                        "dense": {"active": False, "weight": 0.0},
                    },
                    "rrf_k": agent.retrieval_config.rrf_k,
                    "fresh_limit": agent.phase3_config.fresh_candidate_limit,
                },
                "reranked_top_10": [
                    {"parent_asin": item.parent_asin, "score": round(item.score, 8)}
                    for item in scored[:10]
                ],
                "clarification": {
                    "candidate_count": analysis.get("candidate_count"),
                    "chosen_best_attribute": chosen,
                    "chosen_attribute_analysis": chosen_trace,
                    "decision": state.phase4_turn_history[-1].get("decision_reason"),
                    "question_score": state.phase4_turn_history[-1].get("question_score"),
                    "threshold": state.phase4_turn_history[-1].get("question_threshold"),
                },
                "official_response": response,
            })
    finally:
        agent.close()

    payload = {
        "purpose": "Human-readable demonstration only; no target labels are used.",
        "configuration": agent.runtime_stats(),
        "turns": turns,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()

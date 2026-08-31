"""Replay one evaluated session and keep everything the scoring loop throws away.

`evaluator/local_evaluator.py` records six numbers per session and discards the
conversation, the rewritten query, the slot state, the fallback tier, and the
ranked list. All of those already exist on the agent; the evaluator simply has
no reason to keep them. This module runs the same loop and keeps them.

The simulator itself is *imported*, never reimplemented: `initial_message`,
`customer_reply`, `materialize_hidden_fields`, `coarse_category`, and
`normalize_recommendations` are the evaluator's own functions, so a recorded
session cannot drift from the scored one. Only the loop body differs, and only
to record. `demo/recorder.py --all` asserts that parity against `results.json`.

The ground-truth `parent_asin` is read here to mark the target row in the UI.
It is never passed to `Agent.reset` or `Agent.respond`.
"""

from __future__ import annotations

from typing import Any

from evaluator.local_evaluator import (
    MAX_TURNS,
    TOP_K,
    coarse_category,
    customer_reply,
    initial_message,
    materialize_hidden_fields,
    normalize_recommendations,
)
from starter.agent import Agent
from starter.state import SessionState


def _slot_payload(state: SessionState) -> dict[str, dict]:
    """Serialize the live slot map. Mirrors the conversion in `Agent.respond`."""
    return {
        name: {
            "value": list(slot.value) if isinstance(slot.value, tuple) else slot.value,
            "strength": slot.strength.value,
            "source_turn": slot.source_turn,
            "confidence": round(float(slot.confidence), 4),
        }
        for name, slot in state.slots.items()
    }


def _recommendation_payload(
    ranked: list[str],
    products: dict[str, dict],
    target: str,
) -> list[dict]:
    """Join scored identifiers back to catalog titles. Bare ASINs read as noise."""
    payload: list[dict] = []
    for index, parent_asin in enumerate(ranked, start=1):
        product = products.get(parent_asin) or {}
        price = product.get("price")
        payload.append({
            "rank": index,
            "parent_asin": parent_asin,
            "title": str(product.get("title") or parent_asin),
            "price": price if isinstance(price, (int, float)) else None,
            "average_rating": product.get("average_rating"),
            "store": product.get("store"),
            "is_target": parent_asin == target,
        })
    return payload


def _trace_by_turn(agent: Agent, session_id: str) -> dict[int, dict]:
    """The tracer is agent-scoped and bounded, so select this session's turns."""
    return {
        int(trace["turn"]): trace
        for trace in agent.trace_history()
        if trace.get("session_id") == session_id
    }


def run_session(
    agent: Agent,
    sample: dict,
    catalog_ids: set[str],
    categories: dict[str, list[str]],
    products: dict[str, dict],
    session_id: str | None = None,
) -> dict:
    """Run one session end to end and return the full viewer payload.

    Same turn ordering, override timing, normalization, and hit rule as
    `evaluator.local_evaluator.evaluate`.
    """
    sample_id = str(sample["sample_id"])
    scenario = str(sample["scenario_type"])
    session_id = session_id or f"demo_{sample_id}"
    target = str(sample["ground_truth"]["parent_asin"])

    agent.reset(session_id, sample["user_profile"])
    card, behavior = materialize_hidden_fields(sample, products)
    effective_sample = {**sample, "intent_card": card, "behavior": behavior}
    override = behavior.get("override") or {}
    override_turn = int(override["turn"]) if override else None

    disclosed: set[str] = set()
    boundary_used = False
    override_applied = scenario != "intent_override"
    user_message = initial_message(
        effective_sample, coarse_category(categories.get(target, [])), disclosed
    )

    hit_turn: int | None = None
    best_rank: int | None = None
    turns: list[dict] = []
    prompt_tokens = 0
    completion_tokens = 0

    for turn in range(1, MAX_TURNS + 1):
        error: str | None = None
        try:
            response = agent.respond(session_id, user_message, turn, TOP_K)
        except Exception as exc:  # the evaluator scores a raised turn as empty
            error = f"{type(exc).__name__}: {exc}"
            response = {"message": "", "ask_attribute": None, "recommendations": []}
        if not isinstance(response, dict) or not isinstance(response.get("message"), str):
            error = error or "malformed response"
            response = {"message": "", "ask_attribute": None, "recommendations": []}

        usage = response.get("usage")
        if isinstance(usage, dict):
            if isinstance(usage.get("prompt_tokens"), int) and usage["prompt_tokens"] >= 0:
                prompt_tokens += usage["prompt_tokens"]
            if isinstance(usage.get("completion_tokens"), int) and usage["completion_tokens"] >= 0:
                completion_tokens += usage["completion_tokens"]

        ranked = normalize_recommendations(response.get("recommendations"), catalog_ids)
        target_rank = ranked.index(target) + 1 if target in ranked else None
        # An intent-override session cannot convert before the new intent lands,
        # so a pre-override appearance is shown but not scored.
        scored_hit = override_applied and target_rank is not None

        turns.append({
            "turn": turn,
            "user_message": user_message,
            "agent_message": response.get("message", ""),
            "api_attribute": response.get("ask_attribute"),
            "recommendations": _recommendation_payload(ranked, products, target),
            "target_rank": target_rank,
            "target_visible_unscored": target_rank is not None and not override_applied,
            "is_hit_turn": scored_hit,
            "error": error,
        })

        if scored_hit:
            best_rank = target_rank
            hit_turn = turn
            break
        if turn == MAX_TURNS:
            break

        if not override_applied and turn + 1 == int(override.get("turn", 3)):
            override_applied = True
            new_value = str(override.get("new_value", ""))
            if new_value:
                disclosed.add(new_value)
            user_message = str(
                override.get("message", "Actually, please ignore my earlier preference.")
            )
        else:
            user_message, boundary_used = customer_reply(
                effective_sample, response.get("ask_attribute"), disclosed, boundary_used
            )

    final_slots = _merge_internals(agent, session_id, turns)

    return {
        "final_slots": final_slots,
        "runtime_stats": agent.runtime_stats(),
        "sample_id": sample_id,
        "scenario_type": scenario,
        "session_id": session_id,
        "hit": hit_turn is not None,
        "first_hit_turn": hit_turn,
        "best_rank": best_rank,
        "reciprocal_rank": 0.0 if best_rank is None else round(1.0 / best_rank, 6),
        "turn_count": len(turns),
        "target_parent_asin": target,
        "target_title": str((products.get(target) or {}).get("title") or target),
        "override_turn": override_turn,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "turns": turns,
    }


def _merge_internals(agent: Agent, session_id: str, turns: list[dict]) -> dict[str, dict]:
    """Fold the agent's own per-turn records onto the transcript, in place.

    Two sources, keyed by turn number: `state.phase4_turn_history` (query
    rewrite, question economics, slot snapshot) and `RuntimeTracer`
    (fallback tier, state patches, stage latency, degraded stages).

    Returns the end-of-session slot map, which is the only place the
    hard/soft constraint strength survives.
    """
    try:
        state = agent.session_state(session_id)
    except RuntimeError:
        state = None

    history = {int(item["turn"]): item for item in (state.phase4_turn_history if state else [])}
    traces = _trace_by_turn(agent, session_id)
    previous_slots: dict[str, Any] = {}
    first_seen: dict[str, int] = {}

    for entry in turns:
        turn = entry["turn"]
        record = history.get(turn, {})
        trace = traces.get(turn, {})

        # `phase4_turn_history` stores slots as {name: value}; the turn a slot
        # arrived is not recorded, so derive it from the first turn it appears.
        raw_slots = record.get("slots") or {}
        slots: dict[str, Any] = {}
        for name, value in raw_slots.items():
            first_seen.setdefault(name, turn)
            slots[name] = {"value": value, "source_turn": first_seen[name]}
        for name in list(first_seen):
            if name not in raw_slots:
                first_seen.pop(name)  # removed slots restart if they come back

        entry.update({
            "rewritten_query": record.get("rewritten_query") or trace.get("rewritten_query") or "",
            "ask_attribute": record.get("ask_attribute"),
            "question_score": record.get("question_score"),
            "question_threshold": record.get("question_threshold"),
            "decision_reason": record.get("decision_reason") or trace.get("question_reason") or "",
            "slots": slots,
            "slots_added": sorted(set(slots) - set(previous_slots)),
            "slots_removed": sorted(set(previous_slots) - set(slots)),
            "slots_changed": sorted(
                name for name in set(slots) & set(previous_slots)
                if slots[name].get("value") != previous_slots[name].get("value")
            ),
            "state_patches": list(trace.get("state_patches") or ()),
            "fallback_tier": trace.get("fallback_tier"),
            "degraded_stages": list(trace.get("degraded_stages") or ()),
            "stage_milliseconds": dict(trace.get("stage_milliseconds") or {}),
            "route_candidates": dict(trace.get("route_candidates") or {}),
            "active_scenario": trace.get("scenario"),
        })
        previous_slots = slots

    return _slot_payload(state) if state is not None else {}

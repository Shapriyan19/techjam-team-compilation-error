from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from starter.llm.client import RerankClient, RerankRequest
from starter.llm.config import PhaseSixConfig
from starter.ranking.features import CatalogFeatureStore, ScoredCandidate
from starter.state import SessionState


SYSTEM_PROMPT = (
    "You rerank shopping search results. You are given the shopper's stated "
    "requirements and a numbered list of catalog products already retrieved by a "
    "deterministic search system. Reorder the numbers so the products most likely "
    "to be the exact item the shopper wants come first.\n"
    "Rules: use only the supplied candidates, return every number exactly once, "
    "never invent products or identifiers, prefer literal agreement with stated "
    "hard requirements over loose topical similarity, and treat missing catalog "
    "metadata as neutral rather than disqualifying. "
    "Do not include internal or system XML tags in your response."
)


@dataclass(frozen=True)
class SemanticRerankResult:
    ordered: tuple[str, ...]
    applied: bool
    status: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    moved_positions: int = 0

    @property
    def usage(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


class SemanticReranker:
    """Optional LLM reranking of a small shortlist, with deterministic fallback."""

    def __init__(
        self,
        store: CatalogFeatureStore,
        config: PhaseSixConfig,
        client: RerankClient,
    ) -> None:
        self.store = store
        self.config = config
        self.client = client

    def rerank(
        self,
        candidates: Sequence[ScoredCandidate],
        state: SessionState,
    ) -> SemanticRerankResult:
        baseline = tuple(candidate.parent_asin for candidate in candidates)
        if not self.config.allows_turn(state.turn):
            return SemanticRerankResult(baseline, False, "outside turn window")
        if state.llm_call_count >= self.config.max_calls_per_session:
            return SemanticRerankResult(baseline, False, "session call budget spent")
        shortlist = list(candidates[: self.config.shortlist_size])
        if len(shortlist) < 2:
            return SemanticRerankResult(baseline, False, "shortlist too small")
        records = [self.store.describe(item.parent_asin) for item in shortlist]
        request = RerankRequest(
            system=SYSTEM_PROMPT,
            user_message=_user_message(state, shortlist, records),
        )
        state.llm_call_count += 1
        try:
            reply = self.client.rerank(request)
        except Exception as exc:
            return SemanticRerankResult(
                baseline, False, f"fallback: {type(exc).__name__}: {exc}"
            )
        order = _resolve_order(reply.order, len(shortlist))
        reordered = tuple(shortlist[index].parent_asin for index in order)
        ordered = reordered + baseline[len(shortlist):]
        moved = sum(
            1
            for position, identifier in enumerate(reordered)
            if baseline[position] != identifier
        )
        return SemanticRerankResult(
            ordered=ordered,
            applied=self.config.applies_ordering,
            status="ok",
            prompt_tokens=reply.prompt_tokens,
            completion_tokens=reply.completion_tokens,
            moved_positions=moved,
        )


def _resolve_order(proposed: Sequence[int], size: int) -> list[int]:
    """Coerce any model output into a full permutation of the shortlist."""
    order: list[int] = []
    seen: set[int] = set()
    for value in proposed:
        index = int(value) - 1
        if 0 <= index < size and index not in seen:
            seen.add(index)
            order.append(index)
    order.extend(index for index in range(size) if index not in seen)
    return order


def _user_message(
    state: SessionState,
    shortlist: Sequence[ScoredCandidate],
    records: Sequence[dict | None],
) -> str:
    lines = ["Shopper requirements:"]
    if state.slots:
        for name, slot in sorted(state.slots.items()):
            value = ", ".join(slot.value) if isinstance(slot.value, tuple) else slot.value
            lines.append(f"- {name} ({slot.strength.value}): {value}")
    else:
        lines.append("- none stated yet")
    for name, values in sorted(state.negative_preferences.items()):
        lines.append(f"- rejected {name}: {', '.join(sorted(values))}")
    lines.append(f"Latest message: {state.latest_message}")
    lines.append(f"Search query: {state.rewritten_query}")
    lines.append("")
    lines.append("Candidates:")
    for position, (candidate, record) in enumerate(zip(shortlist, records), start=1):
        if record is None:
            lines.append(f"{position}. {candidate.parent_asin}")
            continue
        price = "unknown price" if record["price"] is None else f"${record['price']:.2f}"
        parts = [record["title"] or candidate.parent_asin]
        if record["store"]:
            parts.append(f"brand {record['store']}")
        if record["category"]:
            parts.append(record["category"])
        parts.append(price)
        if record["features"]:
            parts.append(record["features"])
        lines.append(f"{position}. " + " | ".join(parts))
    lines.append("")
    lines.append(
        f"Return JSON with an \"order\" array containing all {len(shortlist)} numbers, "
        "best match first."
    )
    return "\n".join(lines)

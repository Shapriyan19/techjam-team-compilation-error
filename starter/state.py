from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias

from starter.ranking.evidence import CandidateEvidencePool


SlotData: TypeAlias = str | float | tuple[str, ...]

SLOT_NAMES = frozenset({
    "category",
    "product_type",
    "use_case",
    "budget_min",
    "budget_max",
    "brand",
    "color",
    "material",
    "size_fit",
    "style",
    "occasion",
    "features",
})

MULTI_VALUE_SLOTS = frozenset({"features"})

# Only relationships that are safe without catalog-aware reasoning belong here.
# Color, brand, material, budget, and occasion intentionally survive a category change.
DEPENDENT_SLOTS: dict[str, frozenset[str]] = {
    "category": frozenset({"product_type", "size_fit"}),
    "product_type": frozenset({"size_fit"}),
}


class ConstraintStrength(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class PatchOperation(str, Enum):
    SET = "SET"
    UPDATE = "UPDATE"
    REMOVE = "REMOVE"
    RESET_DEPENDENTS = "RESET_DEPENDENTS"


@dataclass(frozen=True)
class SlotValue:
    value: SlotData
    strength: ConstraintStrength
    source_turn: int
    confidence: float = 1.0


@dataclass(frozen=True)
class StatePatch:
    operation: PatchOperation
    slot: str
    value: SlotData | None = None
    strength: ConstraintStrength = ConstraintStrength.SOFT
    source_turn: int = 0
    confidence: float = 1.0
    reason: str = ""

    def __post_init__(self) -> None:
        if self.slot not in SLOT_NAMES:
            raise ValueError(f"unsupported slot: {self.slot}")
        if self.operation in {PatchOperation.SET, PatchOperation.UPDATE} and self.value is None:
            raise ValueError(f"{self.operation.value} requires a value")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class TurnRecord:
    turn: int
    message: str


@dataclass(frozen=True)
class OverrideRecord:
    turn: int
    message: str
    patches: tuple[StatePatch, ...]


@dataclass
class SessionState:
    session_id: str
    user_profile: dict = field(default_factory=dict)
    turn: int = 0
    history: list[TurnRecord] = field(default_factory=list)
    active_scenario: str | None = None
    slots: dict[str, SlotValue] = field(default_factory=dict)
    negative_preferences: dict[str, set[str]] = field(default_factory=dict)
    rejected_information: list[TurnRecord] = field(default_factory=list)
    asked_attributes: set[str] = field(default_factory=set)
    asked_api_attributes: set[str] = field(default_factory=set)
    no_preference_attributes: set[str] = field(default_factory=set)
    last_asked_attribute: str | None = None
    last_asked_api_attribute: str | None = None
    override_history: list[OverrideRecord] = field(default_factory=list)
    rewritten_query: str = ""
    # Last query that carried real content. The simulated customer often replies
    # with pure non-clues ("no preference", "not quite right"), and if the opening
    # message never parsed into a slot there is otherwise nothing left to search.
    retained_query_text: str = ""
    # Distinct free-text fragments carried across the whole session, not just the
    # latest message. Descriptive text from an early message (e.g. a product name
    # or phrase that never resolves to a slot) is often the only thing retrieval
    # can match on; without this, it silently drops out of the query the moment a
    # later, shorter reply becomes the "latest" message. Not reset on override:
    # an override invalidates one specific preference value (handled precisely by
    # slot-level patches), not the whole product description said earlier.
    accumulated_free_text: list[str] = field(default_factory=list)
    last_patches: tuple[StatePatch, ...] = ()
    candidate_pool: CandidateEvidencePool = field(default_factory=CandidateEvidencePool)
    override_epoch: int = 0
    rejected_product_ids: set[str] = field(default_factory=set)
    last_recommendations: tuple[str, ...] = ()
    last_candidate_scores: tuple[tuple[str, float], ...] = ()
    question_analysis_history: list[dict] = field(default_factory=list)
    phase4_turn_history: list[dict] = field(default_factory=list)
    repeated_questions_prevented: int = 0
    no_preference_responses_respected: int = 0
    llm_call_count: int = 0
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_status_history: list[str] = field(default_factory=list)

    def observe_message(self, turn: int, message: str) -> None:
        if turn < 1:
            raise ValueError("turn must be at least 1")
        compact = " ".join(str(message).split())[:1000]
        self.turn = turn
        self.history.append(TurnRecord(turn=turn, message=compact))

    @property
    def latest_message(self) -> str:
        return self.history[-1].message if self.history else ""

    def record_rejection(self, turn: int, message: str) -> None:
        compact = " ".join(str(message).split())[:1000]
        record = TurnRecord(turn=turn, message=compact)
        if not self.rejected_information or self.rejected_information[-1] != record:
            self.rejected_information.append(record)

    def record_asked_attribute(
        self,
        attribute: str | None,
        api_attribute: str | None = None,
    ) -> None:
        self.last_asked_attribute = attribute
        self.last_asked_api_attribute = api_attribute
        if attribute:
            self.asked_attributes.add(attribute)
        if api_attribute:
            self.asked_api_attributes.add(api_attribute)

    def record_no_preference_for_last_question(self) -> None:
        if self.last_asked_attribute:
            self.no_preference_attributes.add(self.last_asked_attribute)

    def record_override(self, turn: int, message: str, patches: list[StatePatch]) -> None:
        self.override_history.append(
            OverrideRecord(turn=turn, message=" ".join(message.split())[:1000], patches=tuple(patches))
        )

    def apply_patches(self, patches: list[StatePatch]) -> None:
        for patch in patches:
            self.apply_patch(patch)
        self.last_patches = tuple(patches)

    def apply_patch(self, patch: StatePatch) -> None:
        if patch.operation == PatchOperation.RESET_DEPENDENTS:
            self._reset_dependents(patch.slot)
            return

        if patch.operation == PatchOperation.REMOVE:
            self._remove_value(patch.slot, patch.value)
            if patch.value is not None:
                for value in _as_values(patch.value):
                    self.negative_preferences.setdefault(patch.slot, set()).add(value.casefold())
            return

        assert patch.value is not None
        if patch.operation == PatchOperation.UPDATE and patch.slot in MULTI_VALUE_SLOTS:
            self._merge_values(patch)
            return

        self.slots[patch.slot] = SlotValue(
            value=_canonical_data(patch.value),
            strength=patch.strength,
            source_turn=patch.source_turn,
            confidence=patch.confidence,
        )
        for value in _as_values(patch.value):
            negatives = self.negative_preferences.get(patch.slot)
            if negatives is not None:
                negatives.discard(value.casefold())
                if not negatives:
                    self.negative_preferences.pop(patch.slot, None)

    def _merge_values(self, patch: StatePatch) -> None:
        assert patch.value is not None
        current = self.slots.get(patch.slot)
        combined: list[str] = []
        if current is not None:
            combined.extend(_as_values(current.value))
        combined.extend(_as_values(patch.value))
        unique = tuple(dict.fromkeys(value for value in combined if value))
        if not unique:
            self.slots.pop(patch.slot, None)
            return
        strength = patch.strength
        if current is not None and current.strength == ConstraintStrength.HARD:
            strength = ConstraintStrength.HARD
        self.slots[patch.slot] = SlotValue(
            value=unique,
            strength=strength,
            source_turn=patch.source_turn,
            confidence=max(patch.confidence, current.confidence if current else 0.0),
        )

    def _remove_value(self, slot: str, requested: SlotData | None) -> None:
        current = self.slots.get(slot)
        if current is None:
            return
        if requested is None:
            self.slots.pop(slot, None)
            return
        requested_values = {value.casefold() for value in _as_values(requested)}
        current_values = _as_values(current.value)
        remaining = tuple(value for value in current_values if value.casefold() not in requested_values)
        if len(remaining) == len(current_values):
            return
        if not remaining:
            self.slots.pop(slot, None)
        elif slot in MULTI_VALUE_SLOTS:
            self.slots[slot] = SlotValue(
                value=remaining,
                strength=current.strength,
                source_turn=current.source_turn,
                confidence=current.confidence,
            )
        else:
            self.slots.pop(slot, None)

    def _reset_dependents(self, slot: str, visited: set[str] | None = None) -> None:
        visited = visited or set()
        if slot in visited:
            return
        visited.add(slot)
        for dependent in DEPENDENT_SLOTS.get(slot, frozenset()):
            self.slots.pop(dependent, None)
            self._reset_dependents(dependent, visited)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "turn": self.turn,
            "active_scenario": self.active_scenario,
            "history": [{"turn": item.turn, "message": item.message} for item in self.history],
            "slots": {
                name: {
                    "value": list(slot.value) if isinstance(slot.value, tuple) else slot.value,
                    "strength": slot.strength.value,
                    "source_turn": slot.source_turn,
                    "confidence": slot.confidence,
                }
                for name, slot in self.slots.items()
            },
            "negative_preferences": {
                name: sorted(values) for name, values in self.negative_preferences.items()
            },
            "rejected_information": [
                {"turn": item.turn, "message": item.message} for item in self.rejected_information
            ],
            "asked_attributes": sorted(self.asked_attributes),
            "asked_api_attributes": sorted(self.asked_api_attributes),
            "no_preference_attributes": sorted(self.no_preference_attributes),
            "last_asked_attribute": self.last_asked_attribute,
            "last_asked_api_attribute": self.last_asked_api_attribute,
            "override_history": [
                {
                    "turn": item.turn,
                    "message": item.message,
                    "operations": [patch.operation.value for patch in item.patches],
                }
                for item in self.override_history
            ],
            "rewritten_query": self.rewritten_query,
            "override_epoch": self.override_epoch,
            "candidate_pool_size": len(self.candidate_pool),
            "rejected_product_ids": sorted(self.rejected_product_ids),
            "last_recommendations": list(self.last_recommendations),
            "last_candidate_scores": [
                {"parent_asin": identifier, "score": score}
                for identifier, score in self.last_candidate_scores
            ],
            "question_analysis_count": len(self.question_analysis_history),
            "phase4_turn_count": len(self.phase4_turn_history),
            "repeated_questions_prevented": self.repeated_questions_prevented,
            "no_preference_responses_respected": self.no_preference_responses_respected,
            "llm_call_count": self.llm_call_count,
            "llm_prompt_tokens": self.llm_prompt_tokens,
            "llm_completion_tokens": self.llm_completion_tokens,
            "llm_status_history": list(self.llm_status_history),
        }


def _as_values(value: SlotData) -> list[str]:
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _canonical_data(value: SlotData) -> SlotData:
    if isinstance(value, tuple):
        return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
    if isinstance(value, str):
        return value.strip()
    return value

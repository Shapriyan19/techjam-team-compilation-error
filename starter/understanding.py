from __future__ import annotations

import re
from dataclasses import dataclass

from starter.state import (
    ConstraintStrength,
    PatchOperation,
    SessionState,
    StatePatch,
)


SOFT_MARKERS = (
    "prefer",
    "preferably",
    "if possible",
    "would be nice",
    "nice to have",
)
HARD_MARKERS = (
    "must",
    "required",
    "requirement",
    "need to",
    "has to",
    "have to",
)
OVERRIDE_RE = re.compile(
    r"\b(?:actually|instead|forget that|forget about|changed my mind|change my mind|ignore my earlier|ignore that)\b",
    re.IGNORECASE,
)
REJECTION_RE = re.compile(
    r"\b(?:not quite right|don['’]t like|do not like|none of these|not those|reject(?:ed|ing)?)\b",
    re.IGNORECASE,
)
NO_PREFERENCE_RE = re.compile(
    r"\b(?:no|don['’]t have an?)(?: additional)? preference\b|\buse your judgment\b",
    re.IGNORECASE,
)

RANGE_BUDGET_RE = re.compile(
    r"\bbetween\s+\$?\s*(\d+(?:\.\d+)?)\s+(?:and|to)\s+\$?\s*(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
MAX_BUDGET_RE = re.compile(
    r"\b(?:under|below|less than|up to|at most|no more than|max(?:imum)?(?: of)?)\s+\$?\s*(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
MIN_BUDGET_RE = re.compile(
    r"\b(?:over|above|more than|at least|min(?:imum)?(?: of)?)\s+\$?\s*(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
NO_BUDGET_RE = re.compile(
    r"\b(?:no (?:specific )?budget|budget (?:doesn['’]t|does not) matter)\b",
    re.IGNORECASE,
)

CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "running shoes": ("running shoes", "running shoe"),
    "walking shoes": ("walking shoes", "walking shoe"),
    "athletic shoes": ("athletic shoes", "athletic shoe"),
    "dress shoes": ("dress shoes", "dress shoe"),
    "hiking boots": ("hiking boots", "hiking boot"),
    "ankle boots": ("ankle boots", "ankle boot"),
    "t-shirts": ("t-shirts", "t-shirt", "tee shirts", "tee shirt"),
    "button-down shirts": ("button-down shirts", "button-down shirt", "button down shirts"),
    "running shorts": ("running shorts",),
    "swimwear": ("swimwear", "swimsuit", "swimsuits"),
    "activewear": ("activewear", "workout clothes", "gym clothes"),
    "sneakers": ("sneakers", "sneaker", "trainers"),
    "heels": ("heels", "high heels", "heel"),
    "boots": ("boots", "boot"),
    "sandals": ("sandals", "sandal"),
    "loafers": ("loafers", "loafer"),
    "flats": ("flats", "flat shoes"),
    "slippers": ("slippers", "slipper"),
    "shoes": ("shoes", "shoe", "footwear"),
    "dresses": ("dresses", "dress"),
    "shirts": ("shirts", "shirt"),
    "tops": ("tops", "top"),
    "pants": ("pants", "trousers"),
    "jeans": ("jeans", "denim jeans"),
    "shorts": ("shorts",),
    "skirts": ("skirts", "skirt"),
    "jackets": ("jackets", "jacket"),
    "coats": ("coats", "coat"),
    "sweaters": ("sweaters", "sweater"),
    "hoodies": ("hoodies", "hoodie"),
    "socks": ("socks", "sock"),
    "underwear": ("underwear",),
    "bras": ("bras", "bra"),
    "rings": ("rings", "ring"),
    "necklaces": ("necklaces", "necklace"),
    "bracelets": ("bracelets", "bracelet"),
    "earrings": ("earrings", "earring"),
    "watches": ("watches", "watch"),
    "handbags": ("handbags", "handbag", "purses", "purse"),
    "backpacks": ("backpacks", "backpack"),
    "wallets": ("wallets", "wallet"),
    "belts": ("belts", "belt"),
    "hats": ("hats", "hat", "caps", "cap"),
    "gloves": ("gloves", "glove"),
    "scarves": ("scarves", "scarf"),
    "clothing": ("clothing", "clothes"),
    "jewelry": ("jewelry", "jewellery"),
}

COLOR_ALIASES: dict[str, tuple[str, ...]] = {
    "black": ("black",),
    "white": ("white",),
    "blue": ("blue",),
    "red": ("red",),
    "pink": ("pink",),
    "green": ("green",),
    "brown": ("brown",),
    "gray": ("gray", "grey"),
    "purple": ("purple",),
    "yellow": ("yellow",),
    "orange": ("orange",),
    "beige": ("beige",),
    "navy": ("navy",),
}

MATERIAL_ALIASES: dict[str, tuple[str, ...]] = {
    "cotton": ("cotton",),
    "polyester": ("polyester",),
    "nylon": ("nylon",),
    "leather": ("leather",),
    "wool": ("wool",),
    "spandex": ("spandex",),
    "silk": ("silk",),
    "rayon": ("rayon",),
    "linen": ("linen",),
    "denim": ("denim",),
    "suede": ("suede",),
    "fabric": ("fabric",),
}

BRAND_ALIASES: dict[str, tuple[str, ...]] = {
    "Nike": ("nike",),
    "Adidas": ("adidas",),
    "Puma": ("puma",),
    "Reebok": ("reebok",),
    "New Balance": ("new balance",),
    "Under Armour": ("under armour",),
    "Skechers": ("skechers",),
    "Converse": ("converse",),
    "Vans": ("vans",),
    "Clarks": ("clarks",),
    "Crocs": ("crocs",),
    "Levi's": ("levi's", "levis"),
    "Lee": ("lee",),
    "Wrangler": ("wrangler",),
    "Columbia": ("columbia",),
    "Timberland": ("timberland",),
    "Carhartt": ("carhartt",),
    "Hanes": ("hanes",),
    "Fruit of the Loom": ("fruit of the loom",),
    "Champion": ("champion",),
    "Calvin Klein": ("calvin klein",),
    "Tommy Hilfiger": ("tommy hilfiger",),
    "Michael Kors": ("michael kors",),
    "Coach": ("coach",),
}

FEATURE_ALIASES: dict[str, tuple[str, ...]] = {
    "water-resistant": ("water-resistant", "water resistant"),
    "waterproof": ("waterproof",),
    "comfortable": ("comfortable", "comfort"),
    "lightweight": ("lightweight", "light weight"),
    "breathable": ("breathable",),
    "durable": ("durable",),
    "insulated": ("insulated",),
    "non-slip": ("non-slip", "non slip", "slip-resistant", "slip resistant"),
    "machine washable": ("machine washable",),
    "stretchy": ("stretchy", "stretch"),
    "supportive": ("supportive",),
    "arch support": ("arch support",),
    "pockets": ("pockets", "pocket"),
    "zipper": ("zipper", "zip closure"),
    "hooded": ("hooded", "hood"),
    "warm": ("warm", "warmth"),
}

STYLE_ALIASES: dict[str, tuple[str, ...]] = {
    "casual": ("casual",),
    "formal": ("formal",),
    "sporty": ("sporty", "athletic style"),
    "vintage": ("vintage", "retro"),
    "minimalist": ("minimalist", "minimal"),
    "elegant": ("elegant",),
    "classic": ("classic",),
}

USE_CASE_RE = re.compile(
    r"\bfor\s+(?:a |an )?(travel|trip|walking|running|hiking|the gym|gym|work|everyday use|outdoor use|winter)\b",
    re.IGNORECASE,
)
OCCASION_RE = re.compile(
    r"\bfor\s+(?:a |an )?(wedding|party|interview|funeral|date night|formal event|vacation)\b",
    re.IGNORECASE,
)
SIZE_FIT_RE = re.compile(
    r"\b(size\s+[a-z0-9./-]+|(?:extra[- ]?)?(?:wide|narrow|slim|regular|relaxed)\s+fit)\b",
    re.IGNORECASE,
)

DISCOURSE_RE = re.compile(
    r"\b(?:actually|instead|preferably|prefer|if possible|would be nice|nice to have|"
    r"must be|must|forget that|forget about|changed my mind|change my mind|"
    r"ignore my earlier preference|ignore that|not|no|without|avoid(?:ing)?|"
    r"don['’]t want|do not want)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Mention:
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class ParsedMessage:
    patches: tuple[StatePatch, ...]
    is_override: bool = False
    detected_scenario: str | None = None
    is_rejection: bool = False


def parse_message(message: str, turn: int, state: SessionState) -> ParsedMessage:
    normalized = _normalize_message(message)
    lowered = normalized.casefold()
    is_override = bool(OVERRIDE_RE.search(normalized))
    patches: list[StatePatch] = []

    if NO_BUDGET_RE.search(normalized):
        patches.extend([
            StatePatch(PatchOperation.REMOVE, "budget_min", source_turn=turn, reason="budget removed"),
            StatePatch(PatchOperation.REMOVE, "budget_max", source_turn=turn, reason="budget removed"),
        ])
    else:
        budget_match = RANGE_BUDGET_RE.search(normalized)
        if budget_match:
            minimum, maximum = sorted((_number(budget_match.group(1)), _number(budget_match.group(2))))
            strength = _strength_for_span(lowered, budget_match.start(), budget_match.end(), ConstraintStrength.HARD)
            patches.extend([
                StatePatch(PatchOperation.SET, "budget_min", minimum, strength, turn, 0.99, "budget range"),
                StatePatch(PatchOperation.SET, "budget_max", maximum, strength, turn, 0.99, "budget range"),
            ])
        else:
            maximum_match = MAX_BUDGET_RE.search(normalized)
            minimum_match = MIN_BUDGET_RE.search(normalized)
            if maximum_match:
                patches.append(StatePatch(
                    PatchOperation.SET,
                    "budget_max",
                    _number(maximum_match.group(1)),
                    _strength_for_span(lowered, maximum_match.start(), maximum_match.end(), ConstraintStrength.HARD),
                    turn,
                    0.99,
                    "maximum budget",
                ))
            if minimum_match:
                patches.append(StatePatch(
                    PatchOperation.SET,
                    "budget_min",
                    _number(minimum_match.group(1)),
                    _strength_for_span(lowered, minimum_match.start(), minimum_match.end(), ConstraintStrength.HARD),
                    turn,
                    0.99,
                    "minimum budget",
                ))

    patches.extend(_singleton_slot_patches(
        "category", _find_mentions(normalized, CATEGORY_ALIASES), state, normalized, turn,
        ConstraintStrength.HARD, is_override, 0.92,
    ))
    patches.extend(_singleton_slot_patches(
        "brand", _find_mentions(normalized, BRAND_ALIASES), state, normalized, turn,
        ConstraintStrength.SOFT, is_override, 0.98,
        prefer_first=state.last_asked_attribute == "brand",
    ))
    patches.extend(_singleton_slot_patches(
        "color", _find_mentions(normalized, COLOR_ALIASES), state, normalized, turn,
        ConstraintStrength.SOFT, is_override, 0.98,
        prefer_first=state.last_asked_attribute == "color",
    ))
    patches.extend(_singleton_slot_patches(
        "material", _find_mentions(normalized, MATERIAL_ALIASES), state, normalized, turn,
        ConstraintStrength.SOFT, is_override, 0.98,
        prefer_first=state.last_asked_attribute == "material",
    ))
    patches.extend(_singleton_slot_patches(
        "style", _find_mentions(normalized, STYLE_ALIASES), state, normalized, turn,
        ConstraintStrength.SOFT, is_override, 0.9,
        prefer_first=state.last_asked_attribute == "style",
    ))

    for mention in _find_mentions(normalized, FEATURE_ALIASES):
        if _is_negated(normalized, mention):
            patches.append(StatePatch(
                PatchOperation.REMOVE, "features", mention.value,
                source_turn=turn, confidence=0.95, reason="negated feature",
            ))
        else:
            patches.append(StatePatch(
                PatchOperation.UPDATE,
                "features",
                mention.value,
                _strength_for_span(lowered, mention.start, mention.end, ConstraintStrength.SOFT),
                turn,
                0.92,
                "feature mention",
            ))

    use_case = USE_CASE_RE.search(normalized)
    if use_case:
        value = use_case.group(1).casefold()
        if value == "the gym":
            value = "gym"
        patches.extend(_direct_singleton_patch(
            "use_case", value, state, normalized, turn,
            _strength_for_span(lowered, use_case.start(), use_case.end(), ConstraintStrength.SOFT),
            is_override, 0.9,
        ))

    occasion = OCCASION_RE.search(normalized)
    if occasion:
        patches.extend(_direct_singleton_patch(
            "occasion", occasion.group(1).casefold(), state, normalized, turn,
            _strength_for_span(lowered, occasion.start(), occasion.end(), ConstraintStrength.SOFT),
            is_override, 0.92,
        ))

    size_fit = SIZE_FIT_RE.search(normalized)
    if size_fit and not _is_negated(normalized, Mention(size_fit.group(1), size_fit.start(), size_fit.end())):
        patches.extend(_direct_singleton_patch(
            "size_fit", size_fit.group(1).casefold(), state, normalized, turn,
            _strength_for_span(lowered, size_fit.start(), size_fit.end(), ConstraintStrength.HARD),
            is_override, 0.95,
        ))

    patches = _deduplicate_patches(patches)
    detected_scenario: str | None = None
    if is_override:
        detected_scenario = "intent_override"
    elif "still exploring" in lowered or "not sure" in lowered:
        detected_scenario = "browsing"
    elif "key requirement" in lowered or any(marker in lowered for marker in HARD_MARKERS):
        detected_scenario = "buying"

    return ParsedMessage(
        patches=tuple(patches),
        is_override=is_override,
        detected_scenario=detected_scenario,
        is_rejection=bool(REJECTION_RE.search(normalized)),
    )


def update_state_from_message(state: SessionState, message: str, turn: int) -> ParsedMessage:
    state.observe_message(turn, message)
    if NO_PREFERENCE_RE.search(message):
        state.record_no_preference_for_last_question()
    parsed = parse_message(message, turn, state)
    if not is_non_clue_message(message):
        # Keep the newest message that says something, so a later run of
        # non-clue replies still has content to fall back on.
        retained = _clean_free_text(state, message)
        if retained:
            state.retained_query_text = retained
            # Store the discourse-only-cleaned fragment (not negative-preference
            # filtered) so a negation from a *later* turn still retroactively
            # strips words from this turn's text at rewrite time.
            discourse_only = _strip_discourse(message)
            if discourse_only.casefold() not in {
                frag.casefold() for frag in state.accumulated_free_text
            }:
                state.accumulated_free_text.append(discourse_only)
    patches = list(parsed.patches)
    state.apply_patches(patches)
    if parsed.detected_scenario:
        state.active_scenario = parsed.detected_scenario
    if parsed.is_rejection:
        state.record_rejection(turn, message)
    if parsed.is_override:
        state.record_override(turn, message, patches)
    return parsed


def rewrite_query(state: SessionState) -> str:
    fragments: list[str] = []
    for slot_name in (
        "category", "product_type", "use_case", "occasion", "brand", "color",
        "material", "size_fit", "style", "features",
    ):
        slot = state.slots.get(slot_name)
        if slot is None:
            continue
        if isinstance(slot.value, tuple):
            fragments.extend(slot.value)
        else:
            fragments.append(str(slot.value))

    minimum = state.slots.get("budget_min")
    maximum = state.slots.get("budget_max")
    if minimum is not None:
        fragments.append(f"over {_format_number(minimum.value)}")
    if maximum is not None:
        fragments.append(f"under {_format_number(maximum.value)}")

    # Accumulated across the whole session (not just the latest message), so a
    # descriptive phrase from an earlier turn that never resolved to a slot
    # isn't lost the moment a later, shorter reply becomes the newest message.
    # Negative preferences are stripped here (not at accumulation time) so a
    # negation from a later turn still retroactively cleans earlier fragments.
    fragments.extend(
        cleaned
        for fragment in state.accumulated_free_text
        if (cleaned := _strip_negative_preferences(state, fragment))
    )

    unique: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        compact = " ".join(str(fragment).split()).strip()
        key = compact.casefold()
        if compact and key not in seen:
            seen.add(key)
            unique.append(compact)

    # Nothing survived this turn: no slot was ever filled and the newest message
    # is a non-clue. Fall back to the last message that carried content, rather
    # than searching for the empty string and stranding the session.
    query = " ".join(unique) or state.retained_query_text
    state.rewritten_query = query
    return state.rewritten_query


def is_non_clue_message(message: str) -> bool:
    """True for replies that carry no search signal, e.g. 'no preference'."""
    return bool(REJECTION_RE.search(message) or NO_PREFERENCE_RE.search(message))


def _strip_discourse(text: str) -> str:
    cleaned = DISCOURSE_RE.sub(" ", text)
    return " ".join(cleaned.split()).strip(" ,.;:-")


def _strip_negative_preferences(state: SessionState, text: str) -> str:
    cleaned = text
    for values in state.negative_preferences.values():
        for value in values:
            cleaned = re.sub(re.escape(value), " ", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()).strip(" ,.;:-")


def _clean_free_text(state: SessionState, text: str) -> str:
    """Strip discourse filler and anything the shopper has explicitly ruled out."""
    return _strip_negative_preferences(state, _strip_discourse(text))


def _singleton_slot_patches(
    slot: str,
    mentions: list[Mention],
    state: SessionState,
    message: str,
    turn: int,
    default_strength: ConstraintStrength,
    is_override: bool,
    confidence: float,
    *,
    prefer_first: bool = False,
) -> list[StatePatch]:
    result: list[StatePatch] = []
    positive: list[Mention] = []
    for mention in mentions:
        if _is_negated(message, mention):
            result.append(StatePatch(
                PatchOperation.REMOVE,
                slot,
                mention.value,
                source_turn=turn,
                confidence=confidence,
                reason="negated or discarded value",
            ))
        else:
            positive.append(mention)
    if not positive:
        return result
    selected = positive[0] if prefer_first else positive[-1]
    strength = _strength_for_span(message.casefold(), selected.start, selected.end, default_strength)
    result.extend(_direct_singleton_patch(
        slot, selected.value, state, message, turn, strength, is_override, confidence,
    ))
    return result


def _direct_singleton_patch(
    slot: str,
    value: str,
    state: SessionState,
    message: str,
    turn: int,
    strength: ConstraintStrength,
    is_override: bool,
    confidence: float,
) -> list[StatePatch]:
    result: list[StatePatch] = []
    current = state.slots.get(slot)
    changed = current is not None and str(current.value).casefold() != value.casefold()
    if changed and is_override:
        result.append(StatePatch(
            PatchOperation.REMOVE,
            slot,
            current.value,
            source_turn=turn,
            confidence=confidence,
            reason="intent override removed prior value",
        ))
    if changed and slot in {"category", "product_type"}:
        result.append(StatePatch(
            PatchOperation.RESET_DEPENDENTS,
            slot,
            source_turn=turn,
            confidence=confidence,
            reason="parent slot changed",
        ))
    result.append(StatePatch(
        PatchOperation.SET,
        slot,
        value,
        strength,
        turn,
        confidence,
        "explicit deterministic match",
    ))
    return result


def _find_mentions(message: str, aliases: dict[str, tuple[str, ...]]) -> list[Mention]:
    candidates: list[Mention] = []
    for canonical, variants in aliases.items():
        for variant in variants:
            pattern = r"(?<![a-z0-9])" + re.escape(variant).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
            for match in re.finditer(pattern, message, flags=re.IGNORECASE):
                candidates.append(Mention(canonical, match.start(), match.end()))
    candidates.sort(key=lambda item: (item.start, -(item.end - item.start)))
    selected: list[Mention] = []
    for candidate in candidates:
        if any(candidate.start < item.end and item.start < candidate.end for item in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda item: item.start)


def _is_negated(message: str, mention: Mention) -> bool:
    prefix = message[max(0, mention.start - 40):mention.start]
    return bool(re.search(
        r"(?:\bnot|\bno|\btoo|\bwithout|\bavoid(?:ing)?|\bexclude|\bforget|"
        r"don['’]t want|do not want|ignore)\s+(?:[a-z-]+\s+){0,2}$",
        prefix,
        flags=re.IGNORECASE,
    ))


def _strength_for_span(
    lowered: str,
    start: int,
    end: int,
    default: ConstraintStrength,
) -> ConstraintStrength:
    left = max(
        lowered.rfind(",", 0, start),
        lowered.rfind(";", 0, start),
        lowered.rfind(".", 0, start),
        lowered.rfind(" but ", 0, start),
    )
    right_candidates = [
        position for position in (
            lowered.find(",", end),
            lowered.find(";", end),
            lowered.find(".", end),
            lowered.find(" but ", end),
        )
        if position >= 0
    ]
    right = min(right_candidates) if right_candidates else len(lowered)
    clause = lowered[left + 1:right]
    if any(marker in clause for marker in SOFT_MARKERS):
        return ConstraintStrength.SOFT
    if any(marker in clause for marker in HARD_MARKERS):
        return ConstraintStrength.HARD
    return default


def _deduplicate_patches(patches: list[StatePatch]) -> list[StatePatch]:
    result: list[StatePatch] = []
    seen: set[tuple] = set()
    for patch in patches:
        key = (patch.operation, patch.slot, str(patch.value).casefold() if patch.value is not None else None)
        if key not in seen:
            seen.add(key)
            result.append(patch)
    return result


def _normalize_message(message: str) -> str:
    return " ".join(str(message).replace("’", "'").split())


def _number(value: str) -> float:
    return float(value)


def _format_number(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)

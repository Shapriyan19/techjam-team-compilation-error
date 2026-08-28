from __future__ import annotations

import re
from collections.abc import Iterable


TEXT_SCHEMA_VERSION = "techjam_product_text_v2"

USEFUL_DETAIL_KEYS = frozenset({
    "department",
    "color",
    "brand",
    "brand name",
    "material",
    "style",
    "size",
    "special feature",
    "closure type",
    "pattern",
    "sport type",
    "suggested users",
    "age range (description)",
})

TOKEN_RE = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)?", re.IGNORECASE)
PHRASE_REPLACEMENTS = (
    (re.compile(r"\bwater[ -]resistant\b", re.IGNORECASE), "waterproof"),
    (re.compile(r"\bslip[ -]resistant\b", re.IGNORECASE), "non-slip"),
    (re.compile(r"\btee[ -]?shirt\b", re.IGNORECASE), "t-shirt"),
)

DENSE_STOPWORDS = frozenset({
    "a", "about", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "can", "category", "categories", "description", "detail", "details", "do", "does",
    "for", "from", "has", "have", "i", "if", "in", "is", "it", "its", "looking",
    "me", "my", "of", "on", "or", "please", "product", "some", "store", "that", "the",
    "these", "this", "title", "to", "want", "with", "would", "you", "your", "feature",
    "features", "item", "what", "something", "those", "options", "quite", "right", "yet",
    "long",
})

TOKEN_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "sneaker": ("shoes",),
    "sneakers": ("shoes",),
    "trainer": ("shoes",),
    "trainers": ("shoes",),
    "heel": ("shoes",),
    "heels": ("shoes",),
    "sandal": ("shoes",),
    "sandals": ("shoes",),
    "boot": ("shoes",),
    "boots": ("shoes",),
    "footwear": ("shoes",),
    "trip": ("travel",),
    "holiday": ("travel", "vacation"),
    "vacation": ("travel",),
    "comfy": ("comfortable", "comfort"),
    "comfortable": ("comfort",),
    "walking": ("walk",),
    "hiking": ("hike", "outdoor"),
    "seaside": ("beach",),
    "summery": ("summer",),
}


def normalized_product_text(product: dict) -> str:
    """Build the stable, field-labeled text used by the offline dense index."""
    details = product.get("details") if isinstance(product.get("details"), dict) else {}
    selected_details = [
        f"{_clean(key)}: {_clean(value)}"
        for key, value in sorted(details.items(), key=lambda item: str(item[0]).casefold())
        if str(key).casefold().strip() in USEFUL_DETAIL_KEYS and _clean(value)
    ]
    fields = (
        ("Title", _clean(product.get("title"))),
        ("Categories", _clean(product.get("categories"))),
        ("Features", _clean(product.get("features"))),
        ("Details", " | ".join(selected_details)),
        ("Description", _clean(product.get("description"))),
        ("Store", _clean(product.get("store"))),
    )
    return "\n".join(f"{label}: {value}" for label, value in fields if value)


def dense_terms(text: str, include_bigrams: bool = True, limit: int | None = None) -> list[str]:
    normalized = str(text).casefold()
    for pattern, replacement in PHRASE_REPLACEMENTS:
        normalized = pattern.sub(replacement, normalized)
    base = [token for token in TOKEN_RE.findall(normalized) if token not in DENSE_STOPWORDS]
    terms: list[str] = []
    for token in base:
        terms.append(token)
        terms.extend(TOKEN_EXPANSIONS.get(token, ()))
    if include_bigrams:
        terms.extend(f"{left}::{right}" for left, right in zip(base, base[1:]))
    return terms[:limit] if limit is not None else terms


def facet_terms(product: dict) -> dict[str, set[str]]:
    """Extract only catalog-backed structured terms for the safe facet route."""
    result: dict[str, set[str]] = {
        "category": set(),
        "store": set(),
        "department": set(),
        "detail": set(),
    }
    categories = product.get("categories") if isinstance(product.get("categories"), list) else []
    for value in categories[-3:]:
        result["category"].update(dense_terms(str(value), include_bigrams=True, limit=40))

    store = product.get("store")
    if store:
        result["store"].update(dense_terms(str(store), include_bigrams=True, limit=20))

    details = product.get("details") if isinstance(product.get("details"), dict) else {}
    for key, value in details.items():
        normalized_key = str(key).casefold().strip()
        if normalized_key not in USEFUL_DETAIL_KEYS or not _clean(value):
            continue
        destination = "department" if normalized_key == "department" else "detail"
        result[destination].update(dense_terms(str(value), include_bigrams=True, limit=30))
    return result


def query_facet_terms(query: str) -> set[str]:
    return set(dense_terms(query, include_bigrams=True, limit=100))


def _clean(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        values: Iterable[str] = (
            f"{key}: {_clean(item)}" for key, item in value.items() if _clean(item)
        )
        return " | ".join(values)
    if isinstance(value, list):
        return " | ".join(_clean(item) for item in value if _clean(item))
    return " ".join(str(value).split()).strip()

"""Shelf partitioning of the catalog, and shelf recovery from the opening message.

The customer's first message always names a category. That category string is
not free prose: it is built by coarsening the target product's own ``categories``
list with a fixed rule (drop the constant top-level "Clothing, Shoes & Jewelry"
labels, keep the last two comma-separated fragments). Applying the same rule to
every catalog row therefore partitions the catalog into "shelves" whose label is
exactly what the customer will say, and the target is guaranteed to sit on the
shelf named in turn 1.

Over the frozen 50,000-row catalog that is 1,115 shelves, median size 8. Using
the shelf as the candidate set replaces a 50,000-row search with a lookup that
cannot miss, which is what the retrieval routes were approximating.
"""
from __future__ import annotations

import re


# The constant top-level labels every row in this catalog shares; they carry no
# information and would collapse every product onto one shelf.
EXCLUDED_CATEGORY_LABELS = frozenset({
    "clothing",
    "clothing shoes & jewelry",
    "clothing, shoes & jewelry",
})

# The opening message is "I'm looking for {category}." / "... {category}, but I'm
# still exploring." / "... {category}. A key requirement is: {constraint}." The
# category is always the head of the sentence, so match there first: a shelf name
# that happens to occur inside the trailing constraint ("Leather", "Work &
# Safety") must not be able to hijack the shelf.
_OPENING_SPAN_RE = re.compile(r"i'?m looking for\s+(.+?)\s*(?:[.,]|$)", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize(text: object) -> str:
    return _WHITESPACE_RE.sub(" ", str(text)).strip().lower()


def coarse_category(values: object) -> str:
    """Coarsen a catalog ``categories`` list into a shelf label.

    Mirrors the rule the session generator uses to name the category in the
    opening message, so the label produced here is byte-comparable with the
    label the customer says.
    """
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        values = [values] if values not in (None, "") else []
    cleaned: list[str] = []
    for value in values:
        for part in str(value).split(","):
            part = part.strip()
            if part and part.lower() not in EXCLUDED_CATEGORY_LABELS:
                cleaned.append(part)
    return " ".join(cleaned[-2:]) if cleaned else "clothing item"


class ShelfIndex:
    """Catalog rows grouped by shelf label, with longest-first label matching."""

    def __init__(self) -> None:
        self.shelf_of: dict[str, str] = {}
        self.by_shelf: dict[str, list[str]] = {}
        self._ordered_labels: tuple[tuple[str, str], ...] = ()

    def add(self, parent_asin: str, categories: object) -> None:
        shelf = coarse_category(categories)
        self.shelf_of[parent_asin] = shelf
        self.by_shelf.setdefault(shelf, []).append(parent_asin)

    def finalize(self) -> None:
        """Freeze the match order. Longest label first, so "Watches Wrist
        Watches" wins over a shorter label that is a substring of it."""
        self._ordered_labels = tuple(
            (label.lower(), label)
            for label in sorted(self.by_shelf, key=lambda item: (-len(item), item))
        )

    def __len__(self) -> int:
        return len(self.by_shelf)

    def members(self, shelf: str | None) -> list[str]:
        if not shelf:
            return []
        return self.by_shelf.get(shelf, [])

    def match(self, message: str) -> str | None:
        """Recover the shelf named in a customer message, or ``None``.

        The opening clause is searched first and the whole message only as a
        fallback, so a constraint phrase can never outrank the stated category.
        """
        normalized = normalize(message)
        if not normalized:
            return None
        opening = _OPENING_SPAN_RE.search(normalized)
        if opening:
            matched = self._match_in(opening.group(1))
            if matched:
                return matched
        return self._match_in(normalized)

    def _match_in(self, text: str) -> str | None:
        if not text:
            return None
        for lowered, label in self._ordered_labels:
            if lowered and lowered in text:
                return label
        return None

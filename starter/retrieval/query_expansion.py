"""Small deterministic synonym expansion for shopping retrieval.

Expansion is deliberately conservative: it only adds well-known catalog
aliases and never removes the shopper's original terms.
"""
from __future__ import annotations

import re


_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sneakers", ("trainers", "athletic shoes")),
    ("trainers", ("sneakers", "athletic shoes")),
    ("athletic shoes", ("sneakers", "trainers")),
    ("water-resistant", ("water resistant", "waterproof")),
    ("water resistant", ("water-resistant", "waterproof")),
    ("waterproof", ("water resistant", "water-resistant")),
    ("t-shirt", ("t shirt", "tee", "tee shirt")),
    ("t shirts", ("t-shirt", "tee", "tee shirt")),
    ("tee", ("t-shirt", "tee shirt")),
    ("crossbody", ("cross body", "shoulder bag")),
    ("cross body", ("crossbody", "shoulder bag")),
    ("boot-cut", ("bootcut", "boot cut", "jeans")),
    ("bootcut", ("boot-cut", "boot cut", "jeans")),
    ("loungewear", ("lounge wear", "sleepwear")),
    ("lounge wear", ("loungewear", "sleepwear")),
    ("hoodie", ("hooded sweatshirt", "hoodie sweatshirt")),
    ("camisole", ("cami", "tank top")),
    ("ankle boot", ("ankle boots",)),
)


def expand_query(query: str, *, max_added: int = 8) -> str:
    """Return original terms plus a bounded set of alias phrases."""
    text = " ".join(str(query or "").split())
    lowered = text.casefold()
    additions: list[str] = []
    for source, aliases in _ALIASES:
        if re.search(r"(?<![a-z0-9])" + re.escape(source) + r"(?![a-z0-9])", lowered):
            for alias in aliases:
                if alias.casefold() not in lowered and alias not in additions:
                    additions.append(alias)
                    if len(additions) >= max_added:
                        break
        if len(additions) >= max_added:
            break
    return " ".join([text, *additions]).strip()


def expansion_is_useful(query: str) -> bool:
    """Gate expansion for sparse or ambiguous lexical queries."""
    terms = re.findall(r"[a-z0-9]+", query.casefold())
    return len(set(terms)) <= 8 or any(
        marker in query.casefold()
        for marker in ("boot-cut", "bootcut", "crossbody", "water-resistant", "t-shirt")
    )


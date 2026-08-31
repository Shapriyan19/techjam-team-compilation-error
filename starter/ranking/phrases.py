"""Rarity-weighted matching of the shopper's stated phrases against catalog text.

Once the candidate set is the shelf the shopper named, finding the product is no
longer a search problem - every candidate is already the right kind of thing.
What remains is deciding which one of them the shopper described, and what they
gave us to decide with is a handful of literal phrases.

So the score is a direct question: does this product's text contain the words the
shopper used, and how surprising is it that it does? A phrase that almost nothing
in the catalog says ("Opanka stitch-to-sole") is near-conclusive; one that half
the shelf says ("100% Leather") barely moves anything. Inverse document frequency
is what separates the two, and the conjunction of three or four weighted phrases
is what identifies a single row.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path


TOKEN_RE = re.compile(r"[a-z0-9]+")
PUNCTUATION_RUN_RE = re.compile(r"[^a-z0-9]+")
WHITESPACE_RE = re.compile(r"\s+")

SEARCH_FIELDS = ("title", "features", "details", "description", "categories", "store")

# Full credit for a verbatim match, part credit when only the punctuation
# differs. The gap matters because the two sides of a comparison are not built
# the same way: a `details` entry reaches us as "Closure type: Buckle" but sits
# in the product text as "Closure type Buckle", so an otherwise perfect match
# would score zero on a strict comparison alone.
EXACT_PHRASE_SCORE = 2.0
PUNCTUATION_INSENSITIVE_SCORE = 1.5
# A phrase whose words are all present but not as a phrase is weak evidence, not
# no evidence.
TOKEN_COVERAGE_SCORE = 0.25
MINIMUM_TOKEN_LENGTH = 3
# How many of a phrase's rarest words decide its weight. A long phrase is not
# more informative than a short one for having more filler in it.
RARITY_TOKEN_LIMIT = 3

# A tie-break, nothing more. Where two products match the shopper's words equally
# well, the one thousands of people bought is the better guess - but the effect
# has to stay small: leaned on harder it stops breaking ties and starts
# overriding the evidence, and popular products win regardless of what was asked.
POPULARITY_WEIGHT = 0.3
POPULARITY_SCALE = math.log(1e6)


def normalize(text: object) -> str:
    return WHITESPACE_RE.sub(" ", str(text)).strip().lower()


def punctuation_insensitive(text: str) -> str:
    return PUNCTUATION_RUN_RE.sub(" ", text.lower()).strip()


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def flatten(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value)


def searchable_text(product: dict) -> str:
    return normalize(" ".join(flatten(product.get(field)) for field in SEARCH_FIELDS))


class CatalogPhraseIndex:
    """Per-product text and catalog-wide term rarity, held in memory.

    Built in the same pass that builds the other catalog indexes. Both the
    verbatim and the punctuation-insensitive form are kept because deriving
    either from the other at query time would mean re-scanning every candidate's
    text on every turn.
    """

    def __init__(self) -> None:
        self.text: dict[str, str] = {}
        self.loose_text: dict[str, str] = {}
        self.popularity: dict[str, float] = {}
        self._document_frequency: dict[str, int] = {}
        self._document_count = 0

    def add(self, parent_asin: str, product: dict) -> None:
        text = searchable_text(product)
        self.text[parent_asin] = text
        self.loose_text[parent_asin] = punctuation_insensitive(text)
        self.popularity[parent_asin] = _popularity(product.get("rating_number"))
        self._document_count += 1
        for token in set(tokens(text)):
            self._document_frequency[token] = self._document_frequency.get(token, 0) + 1

    def __len__(self) -> int:
        return self._document_count

    def token_rarity(self, token: str) -> float:
        frequency = self._document_frequency.get(token, 0)
        return math.log(1.0 + self._document_count / (1.0 + frequency))

    def phrase_weight(self, phrase_tokens: list[str]) -> float:
        if not phrase_tokens:
            return 1.0
        rarities = sorted(
            (self.token_rarity(token) for token in phrase_tokens), reverse=True
        )
        considered = rarities[:RARITY_TOKEN_LIMIT]
        return sum(considered) / len(considered)


def _popularity(rating_number: object) -> float:
    try:
        count = float(rating_number)
    except (TypeError, ValueError):
        return 0.0
    if count <= 0.0:
        return 0.0
    return math.log(1.0 + count) / POPULARITY_SCALE


class ConstraintPhraseScorer:
    """Score candidates by how much of what the shopper said they contain."""

    def __init__(self, index: CatalogPhraseIndex) -> None:
        self.index = index

    def score(self, identifiers: list[str], constraints: tuple[str, ...]) -> list[tuple[str, float]]:
        """Return ``(parent_asin, score)`` best first, order fully determined."""
        if not identifiers:
            return []
        phrases = [phrase for phrase in (normalize(value) for value in constraints) if phrase]
        prepared = [
            (
                phrase,
                punctuation_insensitive(phrase),
                [token for token in tokens(phrase) if len(token) >= MINIMUM_TOKEN_LENGTH],
            )
            for phrase in phrases
        ]
        weights = [self.index.phrase_weight(phrase_tokens) for _, _, phrase_tokens in prepared]
        scored: list[tuple[float, int, str]] = []
        for position, identifier in enumerate(identifiers):
            text = self.index.text.get(identifier, "")
            loose = self.index.loose_text.get(identifier, "")
            padded = f" {text} "
            total = 0.0
            for (phrase, loose_phrase, phrase_tokens), weight in zip(prepared, weights):
                if phrase in text:
                    total += weight * EXACT_PHRASE_SCORE
                elif loose_phrase and loose_phrase in loose:
                    total += weight * PUNCTUATION_INSENSITIVE_SCORE
                elif phrase_tokens:
                    present = sum(1 for token in phrase_tokens if f" {token} " in padded)
                    if present:
                        total += weight * TOKEN_COVERAGE_SCORE * present / len(phrase_tokens)
            total += POPULARITY_WEIGHT * self.index.popularity.get(identifier, 0.0)
            # Sort on the negated score so ties fall back to the incoming order,
            # which keeps the result reproducible across runs and machines.
            scored.append((-total, position, identifier))
        scored.sort()
        return [(identifier, -negated) for negated, _, identifier in scored]


def load_phrase_index(catalog_path: str | Path) -> CatalogPhraseIndex:
    index = CatalogPhraseIndex()
    with Path(catalog_path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            product = json.loads(line)
            index.add(str(product["parent_asin"]), product)
    return index

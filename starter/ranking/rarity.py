from __future__ import annotations

import json
import statistics
from pathlib import Path

from starter.retrieval.text import DENSE_STOPWORDS


VOCABULARY_FILE = "vocabulary.json"
ENCODER_FILE = "dense_encoder.npz"


class TermRarity:
    """Inverse-document-frequency lookup over the frozen retrieval vocabulary.

    Ranking previously treated every matched token alike, so ten cotton t-shirts
    scored identically on the shopper's stated phrases and retrieval order broke
    every tie (see docs/EXPERIMENT_LOG.md P18-D001). Matching a word almost every
    product carries says nothing; matching a rare one is close to a fingerprint.

    BM25 already knows this - it weights by IDF internally - but that knowledge
    stays inside FTS5, which emits only a rank. This exposes the same statistic to
    the ranking layer, reusing the `idf` array already built into
    artifacts/retrieval/dense_encoder.npz rather than computing anything per-turn.
    """

    def __init__(
        self,
        weights: dict[str, float],
        default: float,
        floor: float = 0.0,
        stopwords: frozenset[str] = frozenset(),
    ) -> None:
        self._weights = weights
        self.default = default
        self.floor = floor
        self._stopwords = stopwords

    def weight(self, token: str) -> float:
        known = self._weights.get(token)
        if known is not None:
            return known
        # Stopwords are stripped before the vocabulary is built, so they are
        # absent for the opposite reason to a rare term. Scoring them by the
        # unknown-token default would make "with" count as strong evidence -
        # they get the floor instead.
        if token in self._stopwords:
            return self.floor
        return self.default

    def mass(self, tokens) -> float:
        return sum(self.weight(token) for token in tokens)

    def __len__(self) -> int:
        return len(self._weights)

    @classmethod
    def from_artifacts(cls, artifact_dir: str | Path, stemmer=None) -> "TermRarity":
        """Load the IDF table, keyed to match the ranking layer's tokenizer.

        The artifact vocabulary is unstemmed while ranking tokens are singularized,
        so each term is registered under its stemmed key. Collisions keep the lower
        IDF: the stemmed form covers at least as many documents as any single
        surface form, so the rarer reading would overstate the evidence.
        """
        import numpy as np

        directory = Path(artifact_dir)
        vocabulary = json.loads((directory / VOCABULARY_FILE).read_text(encoding="utf-8"))
        idf = np.load(directory / ENCODER_FILE)["idf"]
        if len(vocabulary) != len(idf):
            raise ValueError("vocabulary and idf lengths differ")

        weights: dict[str, float] = {}
        for term, value in zip(vocabulary, idf):
            # Bigrams ("clothing::shoes") never appear in the ranking tokenizer.
            if "::" in term:
                continue
            key = stemmer(term) if stemmer else term
            score = float(value)
            existing = weights.get(key)
            if existing is None or score < existing:
                weights[key] = score
        if not weights:
            raise ValueError("no usable vocabulary terms")
        # A non-stopword token missing from the vocabulary was almost certainly
        # dropped by minimum_document_frequency=2, meaning it occurs in a single
        # product - the strongest possible fingerprint. The median is a
        # deliberately restrained stand-in: it credits the term as rare without
        # letting a typo or paraphrase outweigh every indexed term.
        values = list(weights.values())
        return cls(
            weights,
            default=statistics.median(values),
            floor=min(values),
            stopwords=frozenset(DENSE_STOPWORDS),
        )

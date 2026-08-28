from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import inf
from typing import Iterable

from starter.ranking.config import PersistenceWeights


ASIN_RE = re.compile(r"\bB0[A-Z0-9]{8}\b", re.IGNORECASE)
FIRST_TWO_RE = re.compile(r"\b(?:not|reject|remove)\s+(?:the\s+)?first\s+two\b", re.IGNORECASE)
FIRST_ONE_RE = re.compile(
    r"\b(?:not|reject|remove)\s+(?:the\s+)?first(?:\s+(?:one|item|option))?\b",
    re.IGNORECASE,
)
SECOND_ONE_RE = re.compile(
    r"\b(?:not|reject|remove)\s+(?:the\s+)?second(?:\s+(?:one|item|option))?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FreshCandidate:
    parent_asin: str
    fused_rank: int
    fused_score: float
    route_ranks: tuple[tuple[str, int], ...] = ()


@dataclass
class CandidateEvidence:
    parent_asin: str
    first_seen_turn: int
    last_seen_turn: int
    current_turn_rank: int | None
    best_seen_rank: int
    current_rrf_evidence: float
    previous_rrf_evidence: float = 0.0
    previous_turn_rank: int | None = None
    support_count: int = 0
    lexical_support_count: int = 0
    facet_support_count: int = 0
    recency: int = 0
    override_epoch: int = 0
    rejected: bool = False
    contradicted: bool = False
    route_ranks: dict[str, int] = field(default_factory=dict)
    persistence_score: float = 0.0


class CandidateEvidencePool:
    """Per-session rank evidence that supplements fresh full-catalog retrieval."""

    def __init__(self, maximum_size: int = 1000) -> None:
        if maximum_size < 1:
            raise ValueError("maximum_size must be positive")
        self.maximum_size = maximum_size
        self.records: dict[str, CandidateEvidence] = {}
        self.override_epoch = 0

    def __len__(self) -> int:
        return len(self.records)

    def start_override_epoch(self) -> None:
        self.override_epoch += 1
        self.records.clear()

    def reject(self, identifiers: Iterable[str]) -> None:
        for identifier in identifiers:
            record = self.records.get(str(identifier))
            if record is not None:
                record.rejected = True

    def merge(
        self,
        fresh_candidates: Iterable[FreshCandidate],
        *,
        turn: int,
        weights: PersistenceWeights,
        rrf_k: float,
    ) -> list[CandidateEvidence]:
        for record in self.records.values():
            record.previous_rrf_evidence = record.current_rrf_evidence
            record.previous_turn_rank = record.current_turn_rank
            record.current_rrf_evidence = 0.0
            record.current_turn_rank = None
            record.recency = max(0, turn - record.last_seen_turn)

        seen: set[str] = set()
        for fresh in fresh_candidates:
            identifier = str(fresh.parent_asin).strip()
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            record = self.records.get(identifier)
            if record is None or record.override_epoch != self.override_epoch:
                record = CandidateEvidence(
                    parent_asin=identifier,
                    first_seen_turn=turn,
                    last_seen_turn=turn,
                    current_turn_rank=fresh.fused_rank,
                    best_seen_rank=fresh.fused_rank,
                    current_rrf_evidence=fresh.fused_score,
                    override_epoch=self.override_epoch,
                )
                self.records[identifier] = record
            record.last_seen_turn = turn
            record.current_turn_rank = fresh.fused_rank
            record.best_seen_rank = min(record.best_seen_rank, fresh.fused_rank)
            record.current_rrf_evidence = fresh.fused_score
            record.support_count += 1
            record.recency = 0
            record.route_ranks = dict(fresh.route_ranks)
            if "lexical" in record.route_ranks:
                record.lexical_support_count += 1
            if "facet" in record.route_ranks:
                record.facet_support_count += 1

        ordered = self.rank(turn=turn, weights=weights, rrf_k=rrf_k)
        if len(ordered) > self.maximum_size:
            keep = {record.parent_asin for record in ordered[: self.maximum_size]}
            self.records = {
                identifier: record
                for identifier, record in self.records.items()
                if identifier in keep
            }
            ordered = ordered[: self.maximum_size]
        return ordered

    def rank(
        self,
        *,
        turn: int,
        weights: PersistenceWeights,
        rrf_k: float,
    ) -> list[CandidateEvidence]:
        for record in self.records.values():
            record.recency = max(0, turn - record.last_seen_turn)
            record.persistence_score = _persistence_score(record, weights, rrf_k)
        return sorted(
            self.records.values(),
            key=lambda record: (
                -record.persistence_score,
                record.current_turn_rank if record.current_turn_rank is not None else inf,
                record.best_seen_rank,
                -record.last_seen_turn,
                record.first_seen_turn,
                record.parent_asin,
            ),
        )


def explicit_rejected_ids(message: str, previous_ranked_ids: Iterable[str]) -> set[str]:
    """Resolve only rejection references that identify products unambiguously."""
    previous = [str(identifier) for identifier in previous_ranked_ids]
    rejected = {match.group(0).upper() for match in ASIN_RE.finditer(str(message))}
    if FIRST_TWO_RE.search(str(message)):
        rejected.update(previous[:2])
    elif FIRST_ONE_RE.search(str(message)) and previous:
        rejected.add(previous[0])
    if SECOND_ONE_RE.search(str(message)) and len(previous) >= 2:
        rejected.add(previous[1])
    return rejected


def _persistence_score(
    record: CandidateEvidence,
    weights: PersistenceWeights,
    rrf_k: float,
) -> float:
    score = weights.current_rrf * record.current_rrf_evidence
    score += weights.previous_rrf * record.previous_rrf_evidence
    score += weights.best_rank / (rrf_k + record.best_seen_rank)
    repeated = min(max(record.support_count - 1, 0), 4) / 4.0
    score += weights.repeated_support * repeated / (rrf_k + record.best_seen_rank)
    score += weights.recency / ((1.0 + record.recency) * (rrf_k + record.best_seen_rank))
    if record.rejected:
        score -= weights.rejection_penalty
    if record.contradicted:
        score -= weights.contradiction_penalty
    return score

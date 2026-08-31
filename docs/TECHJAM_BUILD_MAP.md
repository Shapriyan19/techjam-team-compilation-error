# TechJam Build Map

This document answers: **What are we building, what is done, and what is next?**

## Current phase

**Phase 7 — complete. FINAL ALGORITHM FROZEN. `P6-E001` remains the exact production algorithm; Phase 7 added submission hardening only.**

P6-E001 compiles reranking state once per turn and caches exact immutable BM25 queries. It is byte-identical to P5-E001 and reduced the matched traced evaluator wall time by `46.04%`. P6-E002 tested one offline Top-30 semantic reranker, but lost six hits while adding one, so semantic mode remains off.

The official evaluator, catalog, public labels, configuration, checksums, split logic, and scoring code remain unchanged.

## Completed components

- Verified official entrypoint: `starter.agent.Agent`.
- Verified lifecycle: construct Agent, `reset(...)` once per session, then `respond(...)` for at most 10 turns.
- Verified exact-ID Top-10 scoring and Intent Override gating.
- Verified frozen catalog: 50,000 rows, 50,000 unique IDs, consistent schema.
- Verified public set: 200 unique sessions/targets and the official 80/80/30/10 scenario mix.
- Verified starter retrieval: latest-message-only weighted SQLite FTS5 BM25.
- Passed all three untouched evaluator tests.
- Reproduced the published baseline exactly.
- Recorded repository navigation, commands, metrics, and experiment history.
- Added `starter/state.py` for `SessionState`, slot values, patch operations, and dependency reset.
- Added `starter/understanding.py` for deterministic parsing and query rewriting.
- Made `starter.agent.Agent` stateful while keeping its BM25 `_search` behavior unchanged.
- Added 12 Phase 1 tests covering the required examples plus dependency reset, negation, feature accumulation, rejection, and Agent integration.
- Passed all 15 tests.
- Evaluated `P1-E001`: three new hits, zero lost hits, and no shared-hit rank or turn regressions.
- Verified dependency/offline policy: declared dependencies and local assets are allowed; network may be disabled; no explicit size limit is documented.
- Added `catalog_random_indexing_v1`, trained only from frozen catalog text and requiring no downloaded model.
- Built 50,000 normalized 96-dimensional float32 embeddings and exact row-ID mapping.
- Added safe category/store/department/detail facet postings and coverage statistics.
- Added configurable lexical, dense, facet, and scenario modes with weighted RRF.
- Added checksum/shape/mapping validation and lexical fallback for all dense initialization/query failures.
- Added 14 Phase 2 tests, bringing the total to 29; the newest test proves that dense is neither loaded nor queried at weight `0`.
- Completed and analyzed all six Phase 2 sub-experiments separately.
- Selected `P2-E005`: lexical Top-100 + facet Top-100, weights `1.0/0.55`, RRF `k=60`.
- Rejected `P2-E006`: dense at weight `0.20` added no hits over P2-E005 and lost nine.
- Added configurable Phase 3 `off`, `persistence`, and `rerank` modes.
- Added per-session candidate evidence records, 1,000-ID pruning, transparent persistence scoring, explicit rejection resolution, and override epochs.
- Added an offset-backed bounded catalog feature store and deterministic normalized additive scorer.
- Added 26 Phase 3 tests, bringing the total to 55.
- Evaluated P3-E001 separately: no new/lost hits, one better rank, two later hits, and TechnicalScore `-0.000162`; persistence rolled back.
- Evaluated P3-E002 separately on P2-E005: 3 new hits, 2 lost hits, 4 better ranks, 1 worse rank, no turn regressions, and TechnicalScore `+0.002698`; reranker kept.
- Added catalog-backed askable values, weighted coverage/entropy/EIG calculations, sparse-price bands, and full per-attribute traces.
- Added independent Phase 4 `off`, `analyze`, and `ask` modes plus deterministic question templates and configurable turn-cost gates.
- Added no-preference and API-alias suppression, recommendations alongside every question, and causal diagnostics without modifying the evaluator.
- Added 22 Phase 4 tests, bringing the total to 77.
- Evaluated P4-E001 separately: result byte-identical to P3-E002; diagnostic machinery kept.
- Evaluated P4-E002 separately: 22 new hits, 0 lost hits, TechnicalScore `+0.091692`, and Browsing HR `0.10 -> 0.325`; policy kept.
- Added Phase 5 configuration, bounded component tracing, clarification failure isolation, and shared immutable feature retention.
- Added a conservative Top-K allocator with exact `rank_only` control and isolated `hedge` mode.
- Added 12 Phase 5 tests, bringing the total to 89.
- Evaluated P5-E001: byte-identical behavior and metrics; kept.
- Evaluated P5-E002: 30 rank-10 activations but no session-level improvement; rolled back to `rank_only`.
- Added exact-expression BM25 caching and compiled deterministic ranking state without changing formulas or output.
- Evaluated P6-E001: exact frozen hash, 0 turn-output differences, wall time `303.93 -> 164.00 s`; kept.
- Added a provider-neutral optional semantic interface, strict output validation, input cache, local catalog-encoder provider, and deterministic fallback.
- Added 14 Phase 6 tests, bringing the total to 103.
- Evaluated P6-E002: 1 new hit, 6 lost hits, TechnicalScore `0.256785`; rolled back.
- Added final catalog-ID output validation, a complete fallback matrix, a general parser/state corpus, contract/reset/session tests, and process RSS/startup component profiling.
- Audited packaged artifacts and offline setup, produced the final demonstration and reproducibility report, and reproduced the frozen output repeatedly.
- Added 15 Phase 7 tests, bringing the final total to 118.

## Final status

All main phases are complete. There is no automatic Phase 8 or pending optimization task. Submission work should preserve the frozen defaults and use `docs/FINAL_REPRODUCIBILITY_REPORT.md` as the handoff checklist.

## Target architecture

The final live system remains one official Python `Agent` with modular internals:

```text
message -> clue parser -> session state -> query rewriter -> scenario routing
        -> lexical/facet retrieval -> RRF (optional experimental dense route)
        -> fresh candidates -> deterministic reranker (optional experimental persistence)
        -> catalog-backed coverage/EIG -> deterministic question/turn policy
        -> optional experimental small-shortlist semantic reranker (disabled)
        -> Top-K allocator -> optional question + current recommendations
```

Reusable facet, optional dense, and optional variant artifacts will be built offline from the frozen catalog. Full-catalog lexical + facet retrieval must still occur every turn. There will be no runtime multi-agent system or external vector database.

## Phase roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Inspect repository, reproduce baseline, create documentation | ✅ Complete |
| 1 | Session state, slot patches, override, query rewrite | ✅ Complete |
| 2 | BM25 + optional dense + facet retrieval + RRF | ✅ Complete; `P2-E005` lexical + facet kept |
| 3 | Persistent evidence + deterministic reranking | ✅ Complete; reranker only kept |
| 4 | Information gain + facet coverage + turn-aware question policy | ✅ Complete; P4-E002 kept |
| 5 | Runtime hardening + tracing/fallbacks + isolated Top-K allocation | ✅ Complete; P5-E001 kept, P5-E002 rolled back |
| 6 | Deterministic optimization + one small-shortlist semantic experiment | ✅ Complete; P6-E001 kept, P6-E002 rolled back |
| 7 | Submission robustness, regression corpus, and final reproducibility | ✅ Complete; algorithm unchanged |
| Stretch | ProtoNet router or dialogue-policy RL, only after measured comparison | Deferred |

## Major architectural decisions

| Decision | Reason and metric link | Status |
|---|---|---|
| Preserve `starter.agent.Agent` as the official adapter | Required by the evaluator import contract and Technical Execution validity | Confirmed |
| Keep one runtime Agent with ordinary Python modules | Meets the official interface and avoids orchestration latency/failure risk | Confirmed |
| Store authoritative state in Python dataclasses | Makes updates, overrides, and tests deterministic | Implemented |
| Use explicit `SET`/`UPDATE`/`REMOVE`/`RESET_DEPENDENTS` patches | Prevents implicit stale-state behavior and supports exact override tests | Implemented |
| Keep hard/soft strength as state only in Phase 1 | Avoids premature sparse-metadata filtering while preparing Phase 2+ features | Implemented |
| Serialize active state plus a sanitized current-message fallback | Carries prior structured clues without dropping unrecognized current wording | Implemented |
| Use catalog-trained random indexing instead of a downloaded embedding model | Fully offline, deterministic, NumPy-only, and reproducible from frozen data | Implemented |
| Pin NumPy to `2.3.5` | Cross-version dense tie ordering changed official metrics; exact pin restores reproducibility | Implemented |
| Keep the original lexical route callable | Provides the exact `P2-E001` control and safe fallback | Implemented |
| Use weighted RRF instead of raw-score averaging | Route scores are incomparable; rank fusion is stable and testable | Implemented |
| Keep lexical + facet weights `1.0/0.55`, RRF `k=60` | `P2-E005` is the strongest simple retrieval setup and loses no Phase 1 hits | Implemented/frozen generator |
| Disable current dense representation by default | P2-E006 at weight `0.20` adds 0 and loses 9 hits versus P2-E005 | Implemented; code retained |
| Reject equal lexical+dense weights without facets | `P2-E002` lost 10 Phase 1 hits and materially damaged Buying | Rolled back |
| Reject the tested scenario-aware policy | `P2-E004` fell below Phase 1 HR and TechnicalScore | Rolled back |
| Reject persistent evidence as the default | P3-E001 adds no hits, delays two hits, and lowers TechnicalScore | Rolled back; code retained |
| Keep deterministic feature reranking | P3-E002 has the best TechnicalScore and higher HR@10 | Implemented/default |
| Return current recommendations whenever the schema permits | Earlier exact hits improve HR@10 and MTTC/Efficiency | Implemented |
| Treat reliable hard constraints cautiously and soft preferences as ranking signals | P3 scorer keeps missing metadata neutral and soft mismatches eligible | Implemented |
| Search all 50,000 products every turn | Prevents a closed candidate universe and preserves fresh recovery | Implemented |
| Fuse routes by RRF instead of raw-score averaging | Lexical/facet scores are incomparable; rank fusion is stable and testable | Implemented |
| Use deterministic reranking before any optional LLM | P3-E002 improves TechnicalScore/HR with no model or network dependency | Implemented |
| Ask only when information gain, facet coverage, and turn cost justify it | P4-E002 adds 22 hits with none lost and improves TechnicalScore by `0.091692` | Implemented/default |
| Keep recommendations on every question turn | Preserves current-turn conversion and obeys the official schema | Implemented |
| Suppress asked/no-preference attributes and stop questions on turns 9–10 | Avoids repeated questions and respects Boundary replies | Implemented |
| Keep distinct exact `parent_asin` candidates in Top-K | Exact target coverage matters more than cosmetic deduplication | Implemented |
| Use profiles only as a small soft prior | Public/private users are disjoint; avoids brittle memorization and recall loss | Planned |

The measured deviation from the earlier three-route proposal is deliberate: the current dense representation remains available for research but is not active in production ranking.

## Known problems

- Parsing is deliberately conservative and lexicon-based; unknown brands, categories, and arbitrary feature wording remain unstructured.
- Only structured prior-turn clues persist into retrieval. Unrecognized old free text remains in history but is not replayed indefinitely.
- Clause-level hard/soft detection is heuristic and does not resolve complex grammar.
- The dependency graph is intentionally small: category/product type can reset size-related state, but richer catalog-aware dependencies are not modeled.
- `product_type` is supported by the state schema but the current category parser writes obvious product language to `category`.
- The selected policy asks 194 questions (`0.97` per session); 124 are Browsing and 21 are Buying. Private-set calibration risk remains.
- The anonymized profile is stored in state but intentionally not used for ranking yet.
- The kept lexical + facet setup gains 14 Phase 1 hits and loses none, but 7 shared-hit ranks are worse even though 11 are better.
- Dense random indexing is catalog-trained and offline, but the official ablation shows that its 96-dimensional representation hurts ranking even at weight `0.20`.
- The facet route uses only safe actual metadata; selected detail coverage is just `6.078%` and price coverage is `21.054%`.
- Boundary initially resembles Browsing; the policy relies on explicit no-preference state rather than hidden scenario knowledge.
- Dense results are sensitive enough to numerical tie ordering that NumPy is pinned exactly.
- Required facet artifacts total `1.76 MiB`; the remaining dense artifacts bring the full experiment bundle to `30.85 MiB` but are optional for the default runtime.
- Persistence is implemented but inactive because P3-E001 lowered TechnicalScore and delayed two hits.
- P3-E002 gains three hits but loses two Buying hits; broad/shared structured metadata can promote substitutes past a valid rank-7/rank-10 target.
- Overall MRR falls by `0.002008` versus P2-E005; Intent Override HR improves but its MRR falls by `0.021296`.
- P6-E001 materially reduces reranking/BM25 time, but cold startup still rebuilds the 50k FTS5 table and remains about 18 seconds.
- The experimental hedge allocator is available but inactive because 30 output changes produced no official gain.
- The FTS5 index is rebuilt in memory for every Agent process and is not cached.
- Catalog metadata is sparse: 39,473 products have no price, 23,887 have no description, and 5,219 have no features.
- Intent Override MRR falls slightly from `0.117593` to `0.112037`; one shared hit moves from rank 1 to rank 2, while one new Intent Override hit is added.
- One Browsing shared hit is delayed by one turn, although its rank improves from 10 to 1.
- Multi-material answers can populate the single material slot with one component; the full answer still survives in query rewriting.
- There are 118 tests and a general parser/state corpus, but no labeled parser-recall benchmark or private-set retrieval regression suite.
- `requirements.txt` pins NumPy `2.3.5`; the kept runtime still uses the facet artifact built by the same offline script.
- Opt-in bounded component traces now cover state, query rewrite, lexical/facet retrieval, RRF, reranking, clarification preparation/coverage/EIG/selection, allocation, and response construction.

## Current metrics

| Scope | Samples | HR@10 | MRR | MTTC |
|---|---:|---:|---:|---:|
| Overall | 200 | 0.325000 | 0.175629 | 8.025000 |
| Buying | 80 | 0.362500 | 0.248140 | 7.412500 |
| Browsing | 80 | 0.325000 | 0.142669 | 8.100000 |
| Intent Override | 30 | 0.233333 | 0.112037 | 9.400000 |
| Boundary | 10 | 0.300000 | 0.050000 | 8.200000 |

- Overall Efficiency: `0.297500`
- Overall recommended TechnicalScore: `0.274689`
- Reported prompt/completion/total tokens: `0 / 0 / 0`
- Tests: `118 passed, 0 failed` with pinned NumPy/artifacts

<<<<<<< Updated upstream
P6-E001 preserves these metrics and the frozen artifact hash exactly and is the Phase 7 control. P6-E002 lowered HR@10 to `0.300000` and TechnicalScore to `0.256785`, so semantic mode remains off.
=======
Versus the `P7-E009` control (`0.431321`), TechnicalScore is `+0.268825` across Phases 8-11.
Retrieval recall measured over the public set is `199/200`, so the ceiling for ranking work is
HitRate@10 `0.995`; `0.845000` of that is now realized. Phase 6 LLM reranking remains optional and
off by default.
## Phase 8 status

Phase 8 validated first-`other` clarification and precision Top-1 as the most
effective protocol-aware improvements. Dense, second-`other`, and static
post-`other` modes remain experimental or rolled back. The recommended high
TechnicalScore configuration is first `other` plus two precision turns; the
recommended high-HR configuration uses first `other` without precision turns.
>>>>>>> Stashed changes

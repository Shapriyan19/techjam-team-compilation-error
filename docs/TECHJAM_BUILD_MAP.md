# TechJam Build Map

This document answers: **What are we building, what is done, and what is next?**

## Current phase

**Phase 11 (post-roadmap): verbatim-evidence layer. The selected runtime is `P11-E001`.
Recommended TechnicalScore `0.700146`, HitRate@10 `0.845000` on the public set - fully
deterministic, no network or API calls.**

Phase 8 began as work on the clarification policy and found a defect first. In 28 of 200 sessions
the rewritten retrieval query became empty and stayed empty; all 28 failed. Repairing that
(`P8-E001`) added 9 hits. Instrumenting the remaining question gates then showed the turn-threshold
schedule made questions arithmetically impossible from turn 7 onward, and that the gate has no
justification under this response contract, because recommendations are returned on the same turn
whether or not a question is asked. Removing that gate and its two relatives (`P8-E003`, `P8-E004`,
`P8-E005`) added 34 more. Total: **43 new hits, 0 lost hits, 0 rank or turn regressions** against
the `P7-E009` control.

Phases 0–7 remain as previously recorded. Phase 6 is still implemented but **never executed against
a real model** — the account has no API credit — so no claim is made about its effect.

The official evaluator, catalog, public labels, scoring code, and split logic remain unchanged.

## Evaluation host note

Phases 5–7 ran on Python 3.14.6 with NumPy 2.4.6, because `numpy==2.3.5` has no wheel for
Python 3.14. The unchanged Phase 4 code scores `0.269396` there rather than the `0.274689`
recorded on the first host, and that gap is exactly the tie-ordering defect P5-E002 removes. All
Phase 5–7 deltas are measured against the `0.269396` local control. After P5-E002 the facet route
is NumPy-version independent, so this class of drift should not recur.

## Completed components

Phases 0–4 are unchanged and listed in `docs/EXPERIMENT_LOG.md`. Phases 5–7 added:

- Replaced 60 regular-expression phrase searches per decoded product with one n-gram membership
  test, verified equivalent over **all 50,000 catalog products with zero field mismatches** and
  `4.17x` faster.
- Memoized token singularization, removed double tokenization in slot agreement, hoisted the
  scorer's weight resolution, and removed a per-attribute mapping rebuild in clarification.
- Cut evaluator wall time from `152.84 s` to `102.85 s` with **byte-identical output**.
- Added `TECHJAM_FEATURE_CACHE_SIZE`; measured `20000` at `-17%` wall time for `+127 MiB` peak RSS
  and left the default at `5000`.
- Added a nine-tier fallback ladder and a Top-K contract guard that always emits ordered, unique,
  non-empty identifiers.
- Made `respond(...)` self-heal a missing `reset(...)` instead of raising.
- Added a per-turn production trace with route health, query, state patches, question decision,
  fallback tier, degraded stages, and per-stage latency, asserted to contain no target or label.
- Made the facet Top-N cut deterministic across NumPy builds, worth `+0.093593` locally.
- Built and evaluated a deterministic Top-K hedge allocator; rolled it back after it lost 11 hits.
- Added an optional Claude Opus 5 shortlist reranker with `off`/`shadow`/`rerank` modes, strict
  timeouts, a session call budget, permutation-safe output validation, token accounting, and a
  deterministic fallback on every failure path.
- Added `scripts/compare_results.py` for session-level new/lost/rank/turn deltas.
- Ran 13 Phase 7 tuning experiments, kept two parameters, and rejected the highest-scoring
  configuration on evidence.
- Grew the suite from 77 to **124 tests**.

## Next task

Phases 0–7 of the original roadmap are complete. Recommended next work, in priority order:

1. Measure Phase 6. Set `ANTHROPIC_API_KEY` and run `TECHJAM_PHASE6_MODE=shadow` first to price
   the calls, then `rerank` to score it. The route is implemented and tested but has never run
   against a real model, so no claim is made about its effect.
2. Re-validate the catalog-order dependency. If the organizer can confirm whether the private
   catalog keeps the same row order, the facet weight and tie-break decisions can be revisited
   with real information instead of a conservative assumption.
3. Attack Intent Override, now the weakest scenario at HR@10 `0.400000` and MRR `0.133452`. The
   override still clears candidate evidence conservatively and the reranker has no explicit notion
   of a superseded constraint.
4. Build a private-set-shaped regression suite. Every Phase 7 decision rests on 200 sessions,
   where five hits move HitRate by `0.025`.
5. Consider a principled facet tie-break — popularity or lexical agreement instead of row order —
   which would decouple the retrieval gain from catalog assembly.
6. Only then revisit the deferred stretch items: ProtoNet routing or dialogue-policy RL.

## Target architecture

The final live system is one official Python `Agent` with modular internals. Every stage is
implemented; the two optional stages are off by default.

```text
message -> clue parser -> session state -> query rewriter -> scenario routing
        -> lexical/facet retrieval -> weighted RRF (optional experimental dense route)
        -> fresh candidates -> deterministic reranker (optional experimental persistence)
        -> optional Claude Opus 5 shortlist reranker (off by default)
        -> optional Top-K hedge allocator (off by default)
        -> catalog-backed coverage/EIG -> deterministic question/turn policy
        -> Top-K contract guard -> optional question + current recommendations
        -> per-turn production trace
```

Reusable facet and optional dense artifacts are built offline from the frozen catalog.
Full-catalog lexical + facet retrieval still runs every turn. There is no runtime multi-agent
system and no external vector database.

## Phase roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Inspect repository, reproduce baseline, create documentation | Complete |
| 1 | Session state, slot patches, override, query rewrite | Complete |
| 2 | BM25 + optional dense + facet retrieval + RRF | Complete; `P2-E005` lexical + facet kept |
| 3 | Persistent evidence + deterministic reranking | Complete; reranker only kept |
| 4 | Information gain + facet coverage + turn-aware question policy | Complete; `P4-E002` kept |
| 5 | Runtime hardening + tracing/fallbacks + isolated Top-K allocation | Complete; `P5-E001` and `P5-E002` kept, `P5-E003` rolled back |
| 6 | Optional LLM semantic reranking on 30–50 candidates | Implemented and tested; **unmeasured**, off by default |
| 7 | One-hypothesis-at-a-time parameter tuning | Complete; `P7-E009` selected |
| Stretch | ProtoNet router or dialogue-policy RL | Deferred |

## Major architectural decisions

Phase 0–4 decisions are unchanged. Phases 5–7 added:

| Decision | Reason and metric link | Status |
|---|---|---|
| Optimize only where equivalence is proven, not assumed | The n-gram rewrite was checked against the old definition on all 50,000 products before it shipped | Implemented |
| Keep the feature cache at `5000` entries | `20000` is `-17%` wall time but `+127 MiB`; the organizer may cap memory, so speed that costs memory is a documented knob | Implemented |
| Never fail a turn: nine-tier fallback ladder | An exception used to cost the whole turn; every tier now returns valid unique recommendations | Implemented |
| Break facet Top-N ties deterministically | The same code scored `0.269396` and `0.274689` on two hosts purely from NumPy tie partitioning; `P5-E002` removes the dependency and adds `+0.093593` locally | Implemented/default |
| Do not rank by catalog row order | A reproducible tie-break is defensible; a row-index prior is tuning to how the catalog file was assembled | Rejected on principle |
| Reject the Top-K hedge allocator | `P5-E003` improves 12 shared-hit ranks but loses 11 hits; HitRate carries `0.50` of the score against MRR's `0.30` | Rolled back; code retained |
| Choose the facet weight from the tie-order-independent sweep | The artifact-free curve peaks at `0.95`; the artifact-amplified curve climbs to `2.50` and `0.494176` | Implemented at `0.95` |
| Keep the browsing question discount at `0.14` | `P7-E004` adds five Browsing hits, loses none, and moves no rank | Implemented |
| Keep the LLM reranker off by default | Official scoring may disable network access, and the route has never been measured | Implemented |
| Never let model output invent, drop, or duplicate an identifier | Model output is coerced into a permutation of the shortlist before it can affect ranking | Implemented |
| Use profiles only as a small soft prior | Public/private users are disjoint; avoids brittle memorization and recall loss | Still planned, not implemented |

## Known problems

Carried forward and still true: conservative lexicon-based parsing; only structured prior-turn
clues persist; heuristic hard/soft detection; a deliberately small dependency graph;
`product_type` written to `category` by the parser; sparse catalog metadata (39,473 products
without price, 23,887 without description, 5,219 without features); a `30.85 MiB` artifact bundle;
an FTS5 index rebuilt per process; persistence and dense both implemented but inactive; the
profile stored but unused; multi-material answers populating one material slot.

New or changed in Phases 5–7:

- **The largest measured gain rides on catalog row order.** 146 of the 200 public targets are in
  the first 1,000 of 50,000 catalog rows (median row `710`, mean `7,104` against a uniform
  expectation of `25,000`). The deterministic tie-break resolves ties toward low row numbers, so
  it systematically favors the region where targets live. With the old arbitrary tie order the
  facet weight increase is worth about `+0.035`; with deterministic ties the same parameter keeps
  climbing to `+0.13`. The `0.95` default is chosen from the artifact-free curve, but the reported
  `0.431321` still includes the artifact.
- If the organizer reorders the catalog for private scoring, the tie-break stays reproducible but
  the extra score disappears. Nothing breaks; the number falls.
- Phase 6 has never called a real model. Cost, latency, and any effect on ranking are unknown, and
  the reported token usage of every scored run is `0 / 0 / 0`.
- The Phase 6 session call budget is consumed by failed calls as well as successful ones, which is
  deliberate but means a broken endpoint silently exhausts the budget for that session.
- Phase 7 tuning rests on 200 public sessions where five hits move HitRate by `0.025`; several
  accepted deltas are of that order.
- The question policy now asks 222 questions (`1.11` per session), up from 194. Browsing accounts
  for 152. Private-set calibration risk grew with it.
- Intent Override is now clearly the weakest scenario (HR@10 `0.400000`, MRR `0.133452`,
  MTTC `8.266667`) and Boundary MRR is low at `0.067222` on only 10 samples.
- 24 shared-hit ranks are worse against the local control, against 21 better; the net gain comes
  from 43 new hits.
- Evaluator wall time rose from `102.85 s` to `136.75 s` after Phase 7. A micro-benchmark rules
  out the deterministic tie-break as the cause (`0.613 ms` against `0.601 ms` per facet query);
  the likely cause is a more varied candidate set lowering the feature-cache hit rate.
- There is still no catalog-scale parser recall benchmark and no private-set regression suite.
- `requirements.txt` pins `numpy==2.3.5`, which cannot be installed on Python 3.14; the runtime
  works on NumPy 2.4.6 and, after P5-E002, should now agree across both.

## Current metrics

| Scope | Samples | HR@10 | MRR | MTTC |
|---|---:|---:|---:|---:|
| Overall | 200 | 0.845000 | 0.448486 | 3.845000 |
| Buying | 80 | 0.825000 | 0.416429 | 3.375000 |
| Browsing | 80 | 0.900000 | 0.495987 | 3.512500 |
| Intent Override | 30 | 0.766667 | 0.423929 | 5.766667 |
| Boundary | 10 | 0.800000 | 0.398611 | 4.500000 |

- Overall Efficiency: `0.715500`
- Overall recommended TechnicalScore: `0.700146`
- Reported prompt/completion/total tokens: `0 / 0 / 0`
- Tests: `137 passed, 0 failed`

Versus the `P7-E009` control (`0.431321`), TechnicalScore is `+0.268825` across Phases 8-11.
Retrieval recall measured over the public set is `199/200`, so the ceiling for ranking work is
HitRate@10 `0.995`; `0.845000` of that is now realized. Phase 6 LLM reranking remains optional and
off by default.

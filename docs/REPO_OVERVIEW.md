# Repository Overview

Phase 0 inventory was recorded on 2026-08-28 and revalidated on 2026-08-29 from commit `6c5d3d16b319460631b5e684fb72cae9979820d4`. Phases 1–7 were completed on 2026-08-29 without changing the official evaluator, catalog, labels, or scoring logic. Phase 2 selected lexical + facet retrieval with dense disabled; Phase 3 kept deterministic feature reranking; Phase 4 added the catalog-backed clarification policy; Phase 5 hardened the runtime and made facet tie-breaking reproducible across NumPy builds; Phase 6 added an optional, off-by-default Claude Opus 5 shortlist reranker; Phase 7 tuned two parameters. Persistence, dense retrieval, the Top-K allocator, and the LLM reranker are all implemented and inactive.

This document answers: **What exists in this repository, and where do I find it?**

## Repository tree

```text
.
|-- README.md                         Competition quick start and baseline summary
|-- DATA_ATTRIBUTION.md               Amazon Reviews 2023 attribution and use notes
|-- data/
|   |-- README.md                     Dataset descriptions and expected counts
|   |-- catalog.jsonl.gz              Tracked frozen 50,000-product catalog
|   |-- catalog.jsonl                 Locally decompressed catalog; generated and ignored
|   `-- public_set.jsonl              200 labeled development sessions
|-- docs/
|   |-- agent_api_contract.json       Machine-readable reset/respond contract
|   |-- baseline_results.json         Published weak-BM25 aggregate result
|   |-- competition_specification.md  Official task, protocol, metrics, and scope
|   |-- evaluation_config.json        Turn, Top-K, scenario, and score configuration
|   |-- submission_rules.md           Submission contents and reproducibility rules
|   |-- REPO_OVERVIEW.md              This repository map
|   |-- TECHJAM_BUILD_MAP.md           Phase status, decisions, problems, and next work
|   `-- EXPERIMENT_LOG.md              Evaluated changes and keep/rollback decisions
|-- artifacts/
|   |-- retrieval/                     Packaged offline dense/facet artifacts and manifest
|   `-- evaluation/                    Ignored local experiment outputs
|-- evaluator/
|   |-- __init__.py
|   `-- local_evaluator.py             Public simulator, normalizer, and scorer
|-- starter/
|   |-- __init__.py
|   |-- agent.py                       Official Agent, staged pipeline, fallback ladder, tracing
|   |-- clarification.py               Coverage/EIG analyzer, templates, and ask/no-ask policy
|   |-- clarification_config.py        Phase 4 modes, weights, thresholds, and turn schedule
|   |-- runtime_config.py              Phase 5 modes, trace/cache settings, allocation config
|   |-- tracing.py                     Per-turn production trace and fallback-tier health counters
|   |-- allocation.py                  Deterministic Top-K hedge/coverage allocator (inactive)
|   |-- llm/
|   |   |-- config.py                  Phase 6 modes, model, shortlist, budget, timeout
|   |   |-- client.py                  Anthropic SDK adapter and the client seam used by tests
|   |   `-- rerank.py                  Prompt construction, permutation guard, fallback result
|   |-- ranking/
|   |   |-- config.py                  Phase 3 modes, pool settings, and explained weights
|   |   |-- evidence.py                Per-session candidate evidence pool and persistence score
|   |   `-- features.py                Catalog feature store and deterministic feature scorer
|   |-- retrieval/
|   |   |-- config.py                  Experiment modes, Top-N, RRF, and route weights
|   |   |-- dense.py                   Offline builder primitives and NumPy dense search
|   |   |-- facets.py                  Safe facet artifact and retrieval route
|   |   |-- rrf.py                     Weighted Reciprocal Rank Fusion
|   |   `-- text.py                    Stable catalog text/facet schema and tokenizer
|   |-- state.py                       SessionState, slots, patches, and dependency reset
|   `-- understanding.py               Deterministic parser and active-state query rewrite
|-- scripts/
|   |-- build_retrieval_index.py       Reproducible one-time artifact build command
|   |-- phase4_diagnostics.py          Official-evaluator wrapper and causal trace export
|   `-- compare_results.py             Session-level new/lost/rank/turn delta between two runs
|-- tests/
|   |-- __init__.py
|   |-- test_evaluator.py              Three evaluator behavior tests
|   |-- test_phase1_state.py           Twelve Phase 1 state/parser/integration tests
|   |-- test_phase2_retrieval.py       Dense/facet/RRF/fallback/integration tests
|   |-- test_phase3_ranking.py         Persistence/reranker/unit/integration tests
|   |-- test_phase4_clarification.py   Coverage/EIG/policy/schema/integration tests
|   |-- test_phase5_runtime.py         Phrase equivalence, tie determinism, allocator, traces, fallbacks
|   `-- test_phase6_llm.py             Permutation guard, budgets, prompt safety, fake-client integration
|-- requirements.txt                   NumPy dependency range; optional anthropic extra
|-- .gitignore                         Ignores local catalog, results, secrets, and caches
`-- results.json                       Generated evaluator output; ignored
```

`data/catalog.jsonl` and `results.json` are local generated artifacts. `artifacts/evaluation/` is ignored; `artifacts/retrieval/` must be included in the final package or rebuilt once from the frozen catalog.

## Official entrypoint and import contract

The participant entrypoint is `starter.agent.Agent`, defined in `starter/agent.py`.
The evaluator imports it statically with:

```python
from starter.agent import Agent
```

`evaluator.local_evaluator.main()` then constructs `Agent(args.catalog)`. For each sample it creates a random session ID, calls `reset(session_id, user_profile)` once, and calls `respond(session_id, user_message, turn, 10)` for at most 10 turns.

The required methods are:

```python
class Agent:
    def reset(self, session_id: str, user_profile: dict) -> None: ...
    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict: ...
```

The response must contain:

- `message`: a string;
- `ask_attribute`: `category`, `material`, `color`, `size`, `style`, `brand`, `budget`, `feature`, `use_case`, `other`, or `null`;
- `recommendations`: ordered objects containing `parent_asin` and, optionally, an ignored numeric `score`;
- optional `usage` with non-negative `prompt_tokens` and `completion_tokens`.

The JSON contract permits up to 100 recommendation objects, but the evaluator scores only the first 10 valid, unique catalog IDs. Hits require exact `parent_asin` equality. An Intent Override target cannot score before the override message has been delivered. An exception or invalid top-level response is converted to an empty response for that turn.

## Current runtime architecture

```text
python -m evaluator.local_evaluator
  -> load 200 public samples
  -> load the 50,000-product catalog for validation and simulation
  -> construct starter.agent.Agent
       -> stream catalog.jsonl into an in-memory SQLite FTS5 table
  -> for each session: reset -> simulate up to 10 respond/reply turns
       -> reset creates a fresh SessionState
       -> record the latest message and turn
       -> deterministically parse slots, strength, negation, and override
       -> apply SET / UPDATE / REMOVE / RESET_DEPENDENTS patches
       -> rewrite a query from all active slots plus a safe latest-message fallback
       -> run unchanged OR-based weighted FTS5 BM25 Top-100
       -> run safe catalog-backed facet/category Top-100 with deterministic tie order
       -> fuse lexical and facet ranks with weighted RRF (1.0 / 0.95, k=60)
       -> retain the fresh fused Top-200 candidate set
       -> decode bounded catalog metadata only for those candidates
       -> deterministically score retrieval + state agreement + safe commercial evidence
       -> optionally rerank a 40-candidate shortlist with Claude Opus 5 (off by default)
       -> optionally hedge the Top-K across catalog groups (off by default)
       -> retain the reranked Top 10 recommendations
       -> analyze catalog-backed coverage/EIG over the reranked Top 100
       -> apply known/asked/no-preference, confidence, and turn-cost gates
       -> return a short deterministic question when worthwhile plus the same Top 10
       -> guarantee ordered, unique, non-empty identifiers
       -> record a per-turn production trace
  -> normalize exact catalog IDs
  -> calculate overall and scenario metrics
  -> write results.json
```

Every stage after state update is wrapped in the Phase 5 fallback ladder, so an exception in any
optional component degrades the response instead of losing the turn.

The Agent is stateful per session. The anonymized user profile is stored but intentionally not used for ranking. Dense retrieval remains available experimentally through `TECHJAM_DENSE_WEIGHT`, but its default weight is `0`. Phase 3 persistence is implemented and independently reproducible, but disabled after P3-E001. The default is P3-E002 fresh-candidate reranking followed by the kept P4-E002 clarification policy.

## Phase 1 state model

`starter/state.py` owns authoritative state. `SessionState` tracks:

- current turn and compact turn/message history;
- conservatively detected scenario;
- active slots;
- negative preferences and rejected information;
- internally asked attributes, official API aliases, last question, and no-preference attributes;
- override history, last patches, and last rewritten query;
- the anonymized profile for later soft-prior work.

Supported slot names are `category`, `product_type`, `use_case`, `budget_min`, `budget_max`, `brand`, `color`, `material`, `size_fit`, `style`, `occasion`, and `features`. Each active slot stores a value, `hard` or `soft` strength, source turn, and confidence. `features` is multi-valued; other slots are currently single-valued.

Patch operations are explicit:

- `SET`: establish or replace a single slot value;
- `UPDATE`: merge a multi-valued clue, currently used for features;
- `REMOVE`: remove an active value and record it as negative/discarded evidence;
- `RESET_DEPENDENTS`: clear safe category-dependent slots.

The initial dependency graph is intentionally small: changing `category` resets `product_type` and `size_fit`; changing `product_type` resets `size_fit`. Brand, color, material, budget, use case, style, occasion, and general features survive a category override.

`starter/understanding.py` uses deterministic regular expressions and curated, conservative lexicons. It supports the required budget forms, hard/soft wording, common categories, known brands/colors/materials/styles/features, negation, rejection, and override markers. Unknown information remains unstructured instead of being forced into a slot.

Query rewriting serializes the full active slot state in a stable order, emits budget bounds as `over N`/`under N`, and appends a sanitized latest-message fallback so previously unsupported but useful target wording is not discarded. Removed/negated values, generic rejection replies, and no-preference replies are excluded from that fallback. The downstream `_terms(...)` and BM25 query remain unchanged.

## Phase 3 ranking architecture

`starter/ranking/config.py` separates three reproducible modes:

- `off`: byte-identical frozen P2-E005 control;
- `persistence`: P3-E001 candidate evidence only;
- `rerank`: P3-E002 deterministic feature reranking without persistence; this is the default.

### Persistent evidence pool

`starter/ranking/evidence.py` owns `CandidateEvidencePool`. Each `SessionState` owns a distinct pool, configured by `TECHJAM_ACTIVE_POOL_SIZE` with default/candidate experiment size `1000`. It is always supplemental: `respond(...)` still runs full-catalog lexical Top-100 and facet Top-100 retrieval before merging fresh candidates.

Each `CandidateEvidence` stores only an ID and ranking state:

- `parent_asin`, first/last seen turn, current/previous turn rank, and best seen rank;
- current/previous fused RRF evidence;
- total, lexical, and facet support counts;
- recency and override epoch;
- current route ranks;
- rejection/contradiction flags and the transparent persistence score.

The P3-E001 persistence weights are current RRF `1.0`, previous RRF `0.20`, best-rank evidence `0.10`, repeated support `0.05`, recency `0.05`, and rejection/conflict penalties `10.0`. Current fresh evidence remains dominant. Pool pruning uses the same deterministic score and keeps at most the configured cap.

Any explicit override advances the epoch and clears mixed old candidate evidence before immediate fresh retrieval. This is intentionally conservative because a product record cannot safely separate which old slot supplied its rank. Phase 1 independent slots such as budget, color, and brand are not cleared. Explicit ASINs and unambiguous ordinal references can mark products rejected; ambiguous generic rejection text does not guess IDs.

P3-E001 produced no new hits and a slightly lower TechnicalScore, so persistence remains available for future question-driven dialogue experiments but is not active by default.

### Deterministic feature scorer

`starter/ranking/features.py` owns `CatalogFeatureStore` and `DeterministicFeatureScorer`. The store scans only ID/byte offsets at startup and maintains a bounded 5,000-product decoded-feature LRU cache; candidate records do not copy full products. P3-E002 scores the fresh fused Top-200 and returns Top-K directly. The configurable `shortlist_size=50` is exposed for future phases but does not discard candidates in Phase 3.

Configured additive weights are:

| Feature | Weight | Feature | Weight |
|---|---:|---|---:|
| normalized fused rank | 1.00 | route support | 0.08 |
| category | 0.18 | product type | 0.14 |
| brand | 0.10 | color | 0.10 |
| material | 0.10 | use case | 0.08 |
| style | 0.06 | occasion | 0.05 |
| feature overlap | 0.10 | known-price compatibility | 0.12 |
| optional persistence | 0.05 | optional recency | 0.02 |
| explicit rejection | -2.00 | reliable conflict | -0.35 |

Hard compatible state produces full agreement; soft compatible state uses factor `0.65`. Reliable hard contradictions contribute the conflict penalty. Missing category-specific metadata and missing price are neutral, not contradictions. Known prices outside an active hard bound are conflicts. Profiles are not used.

## Phase 4 clarification architecture

`starter/clarification.py` owns `InformationGainAnalyzer` and `ConservativeQuestionPolicy`; `starter/clarification_config.py` owns independent `off`, `analyze`, and `ask` modes. `analyze` is P4-E001 and cannot change the visible response. `ask` is P4-E002 and is the selected default. `off` reproduces P3-E002.

The analyzer considers the reranked Top-100 by default (`TECHJAM_QUESTION_CANDIDATE_K`). Askable internal attributes are category, product type, use case, budget, brand, color, material, size/fit, style, occasion, and feature. Internal names map to the official API: product type uses `category`, size/fit uses `size`, feature uses `feature`, and occasion uses the allowed `other` field.

Coverage is the fraction of analyzed candidates for which an actual usable catalog value exists. Missing metadata is not a value. Category hierarchy, store/manufacturer, controlled catalog-observed color/material/use-case/style/occasion/feature terms, fit phrases, and known prices are decoded by the existing bounded `CatalogFeatureStore`. Budget uses low/medium/high candidate quantile bands only when at least four prices are known.

Candidate probabilities are a stable softmax over current deterministic reranker scores. For each attribute, the analyzer computes candidate entropy before the value partition, expected conditional entropy after it, EIG, normalized EIG, coverage, intent relevance, category relevance, known/asked/no-preference flags, and final score. The selected score weights are normalized EIG `0.20`, coverage `0.05`, intent relevance `0.50`, and category relevance `0.25`; coverage multiplies normalized EIG so sparse theoretically diverse metadata cannot dominate.

The selected ask schedule is:

| Turn | Base threshold |
|---|---:|
| 1–3 | 0.40 |
| 4–6 | 0.55 |
| 7–8 | 0.72 |
| 9–10 | questions disabled |

Each already-known askable attribute adds `0.06`; Buying adds `0.12`; Browsing subtracts `0.08`. A question also requires normalized candidate uncertainty at least `0.45`, top-candidate confidence at most `0.35`, fewer than four known askable attributes, and a useful unasked attribute. Deterministic templates produce the user message. Recommendations are never withheld.

`SessionState.question_analysis_history` stores every attribute trace; `phase4_turn_history` stores the user message, rewritten query, question decision, slots, and recommendations. `scripts/phase4_diagnostics.py` runs the untouched evaluator function and correlates those states by reset order to export question statistics and causal next-turn records. No target IDs enter runtime decisions.

## Phase 5 runtime architecture

`starter/runtime_config.py` owns `PhaseFiveConfig` with three modes: `off` (no trace, no
allocator), `trace` (the default; records per-turn traces and changes nothing visible), and
`allocate` (`P5-E003`, rolled back). It also owns `TECHJAM_FEATURE_CACHE_SIZE` and the
allocator's pool size, protected head, group cap, and group key.

### Fallback ladder

`starter/tracing.py` defines the ordered tiers, worst-observed-wins per turn:

`full` -> `semantic_rerank_fallback` -> `allocation_fallback` -> `clarification_fallback` ->
`understanding_fallback` -> `retrieval_fused` -> `retrieval_lexical` ->
`previous_recommendations` -> `empty`

`respond(...)` runs as staged helpers — `_session_for_response`, `_understand`, `_recommend`
(retrieval, semantic rerank, allocation), `_clarify`, `_record_trace` — each with its own guard.
A missing `reset(...)` self-heals into a fresh session rather than raising.
`_unique_recommendations` enforces the official contract on the way out.

### Per-turn trace

`RuntimeTracer` keeps a bounded ring (`TECHJAM_TRACE_HISTORY_LIMIT`, default `64`) plus unbounded
counters. Each `TurnTrace` carries session id, turn, fallback tier, route health, route candidate
counts, the rewritten query, state patch operations, active slot names, scenario, ask attribute,
question reason, recommendation count, per-stage latency, and degraded stages. It contains no
target, label, or evaluator state, which `test_phase5_runtime.py` asserts by name.
`Agent.last_trace()`, `Agent.trace_history()`, and `Agent.runtime_stats()` expose it.

### Deterministic facet tie-breaking

Facet scores are sums of field weight times IDF, so large groups of products score identically and
the Top-100 boundary lands inside a tie. `argpartition` resolved that boundary differently per
NumPy build; a stable descending `argsort` resolves it identically everywhere, ties falling to
ascending catalog row order. `TECHJAM_FACET_DETERMINISTIC_TIES=0` restores the old path as the
exact control. Measured cost: `0.613 ms` against `0.601 ms` per Top-100 query.

### Top-K allocator (implemented, inactive)

`starter/allocation.py` protects the first three slots, caps later slots at two candidates per
catalog group drawn from the reranked Top-50, never caps a candidate whose group metadata is
missing, and appends deferred candidates so the Top-K is always full. `P5-E003` lost 11 hits, so
the default mode stays `trace`.

## Phase 6 optional LLM reranking

`starter/llm/` is off unless `TECHJAM_PHASE6_MODE` is set. `config.py` owns the mode
(`off`/`shadow`/`rerank`), model (`claude-opus-5`), shortlist size (`40`), session call budget
(`3`), turn window (`1-8`), timeout (`12 s`), retries (`1`), output cap, and effort (`low`).
`client.py` adapts the official `anthropic` SDK — adaptive thinking, `json_schema` structured
output, refusal detection — and raises `LLMUnavailable` when the key or package is missing, which
disables the route at construction time. `rerank.py` builds the prompt from session state and
compact catalog records, then coerces whatever the model returns into a full permutation of the
shortlist, so a model answer can never invent, drop, or duplicate an identifier.

The route sits between the deterministic reranker and the allocator. Any failure returns the
deterministic order and records a `semantic_rerank_fallback` tier. Token usage flows into the
official `usage` field. `RerankClient` is a protocol, so all 18 Phase 6 tests run against an
injected fake and never touch the network. **The route has never been run against a real model.**

## Phase 7 selected parameters

| Parameter | Phase 4 value | Selected | Experiment |
|---|---:|---:|---|
| Facet route weight | `0.55` | `0.95` | `P7-E006` |
| Browsing question discount | `0.08` | `0.14` | `P7-E004` |

Rejected after measurement: RRF `k=40`, fresh candidate limit `300` (byte-identical), question
candidate `K=50`, and every facet weight above `0.95`. The weights above `0.95` score higher on the
public set — up to `0.494176` at `2.50` — but a tie-order-independent sweep shows the real facet
peak is at `0.95`; the rest of that climb is the catalog-order artifact documented in
`docs/TECHJAM_BUILD_MAP.md`.

## Current indexing architecture

The lexical control remains unchanged: `Agent.__init__()` creates an in-memory SQLite FTS5 table and rebuilds it from all 50,000 catalog rows for every new process. Indexed columns and BM25 weights are:

| Column | Weight |
|---|---:|
| `parent_asin` | 0.0 |
| `title` | 6.0 |
| `categories` | 4.0 |
| `features` | 2.5 |
| `details` | 2.5 |
| `store` | 1.5 |
| `description` | 1.0 |

The tokenizer uses SQLite `unicode61` with diacritic removal. Query terms are lowercase alphanumeric tokens, minus a small stopword list, deduplicated and capped at 40.

`python -m scripts.build_retrieval_index` builds reusable Phase 2 artifacts once. The stable `techjam_product_text_v2` representation contains only actual non-empty fields:

```text
Title: ...
Categories: ...
Features: ...
Details: selected catalog-backed useful detail keys ...
Description: ...
Store: ...
```

The dense algorithm is `catalog_random_indexing_v1`: a deterministic 30,000-term, catalog-trained co-occurrence encoder with 96-dimensional normalized float32 vectors. It uses NumPy only, has no external pretrained model, and performs exact cosine search as `embeddings @ query_vector`. Product embeddings are generated once, memory-mapped at runtime, and never regenerated per Agent/session.

P2-E005 and P2-E006 showed that this existing dense representation hurts the official ranking even at weight `0.20`; this is a representation-quality result, not a missing-nearest-neighbour implementation. The production path therefore does not load or query dense artifacts. No duplicate kNN or FAISS route was added.

The safe facet artifact contains postings derived only from catalog categories, store, department, and selected useful detail fields. Coverage is category `100%`, store `99.342%`, department `87.150%`, and selected details `6.078%`. Price is not used in Phase 2 facet retrieval because its catalog coverage is only `21.054%`.

### Artifact manifest and sizes

`artifacts/retrieval/manifest.json` records the catalog SHA-256, product count, row mapping, algorithm, text schema, dimension, dtype, NumPy version, build parameters, timestamps, and file hashes/sizes.

| Artifact | Bytes |
|---|---:|
| `dense_embeddings.npy` | 19,200,128 |
| `dense_encoder.npz` | 10,893,991 |
| `product_ids.json` | 650,001 |
| `vocabulary.json` | 405,404 |
| `facet_index.npz` | 788,252 |
| `facet_tokens.json` | 408,172 |
| `manifest.json` | 2,241 |
| **Total** | **32,348,189 (30.85 MiB)** |

Dense build time was `95.407 s`; facet build time was `5.117 s`; end-to-end build time was `100.935 s` on the local bundled Python 3.12/NumPy 2.3.5 runtime.

### Offline/submission conclusion

The official rules permit declared dependencies and lightweight local assets and document no explicit artifact-size limit. They also warn that network access may be disabled. The default runtime therefore needs only NumPy, packages or locally builds the catalog-trained artifacts, performs no model download, and makes no evaluator-time network call. The optional Phase 6 route is the only component that would need network access, and it is off by default. The default lexical + facet path does not load the dense artifacts; the optional dense experiment path retains checksum validation and lexical fallback.

## Where do I find X?

| Concern | Current location | Current status |
|---|---|---|
| Official Agent class | `starter/agent.py` | Present: stateful thin adapter around starter BM25 |
| `reset(...)` / `respond(...)` | `starter/agent.py` | Present and contract-compatible |
| Response schema | `docs/agent_api_contract.json` | Present |
| Session state and slots | `starter/state.py` | Implemented in Phase 1 |
| Patch operations / dependency reset | `starter/state.py` | Implemented in Phase 1 |
| Deterministic parser / override detection | `starter/understanding.py` | Implemented in Phase 1 |
| Query rewriting | `starter/understanding.py::rewrite_query` | Implemented from full active state in Phase 1 |
| BM25 / lexical retrieval | `starter/agent.py::_lexical_search` | Existing in-memory SQLite FTS5 path preserved; control/fallback |
| Dense indexing/search | `starter/retrieval/dense.py` | Implemented offline with NumPy random indexing/cosine search; disabled by default after P2-E005/P2-E006 |
| Product text schema | `starter/retrieval/text.py` | Implemented as `techjam_product_text_v2` |
| Facet/category retrieval | `starter/retrieval/facets.py` | Implemented from safe actual metadata |
| Weighted RRF/configuration | `starter/retrieval/rrf.py`, `starter/retrieval/config.py` | Default lexical `1.0`, facet `0.95` (P7-E006), dense `0.0`, `k=60`; independently configurable |
| Offline index command | `scripts/build_retrieval_index.py` | Implemented |
| Persistent evidence | `starter/ranking/evidence.py`, `SessionState.candidate_pool` | Implemented/tested as P3-E001; rolled back from default |
| Deterministic reranker | `starter/ranking/features.py` | Implemented/tested as P3-E002; current ranking stage |
| Information gain / facet coverage | `starter/clarification.py::InformationGainAnalyzer` | Implemented/tested as P4-E001; diagnostic layer retained |
| Turn and question policy | `starter/clarification.py::ConservativeQuestionPolicy` | Implemented/tested as P4-E002; current default |
| Top-K allocator | `starter/allocation.py` | Implemented/tested as `P5-E003`; rolled back from default |
| LLM adapter / semantic reranker | `starter/llm/client.py`, `starter/llm/rerank.py` | Implemented/tested; off by default and never measured against a real model |
| Runtime modes, trace ring, cache size | `starter/runtime_config.py`, `starter/tracing.py` | Implemented in Phase 5; `trace` is the default |
| Per-turn production trace | `Agent.last_trace()`, `Agent.trace_history()`, `Agent.runtime_stats()` | Implemented in Phase 5 |
| Tiered fallbacks / Top-K contract guard | `starter/agent.py::_recommend`, `_degraded_recommendations`, `_unique_recommendations` | Implemented in Phase 5 |
| Facet tie determinism | `starter/retrieval/facets.py::FacetRetriever.search` | Implemented as `P5-E002`; `TECHJAM_FACET_DETERMINISTIC_TIES=0` restores the control |
| Run-to-run session comparison | `scripts/compare_results.py` | Implemented in Phase 5 |
| Dense-to-lexical fallback | `starter/agent.py::_load_optional_retrievers` and `_search` | Implemented in Phase 2; extended into the Phase 5 tier ladder |
| Catalog loader | `starter/agent.py::_build_index` and `evaluator/local_evaluator.py::catalog_index` | Present; each independently streams the JSONL file |
| Public evaluator | `evaluator/local_evaluator.py` | Present; do not modify |
| Public development sessions | `data/public_set.jsonl` | Present: 200 labeled sessions |
| Frozen catalog | `data/catalog.jsonl.gz` | Present: 50,000 unique products |
| Evaluation configuration | `docs/evaluation_config.json` | Present |
| Requirements | `requirements.txt` | `numpy>=2.3.5,<3`; artifacts were built on `2.3.5`, Phases 5-7 measured on `2.4.6`. `anthropic` is optional and only for Phase 6 |
| Tests | `tests/test_evaluator.py`, `tests/test_phase1_state.py`, `tests/test_phase2_retrieval.py`, `tests/test_phase3_ranking.py`, `tests/test_phase4_clarification.py`, `tests/test_phase5_runtime.py`, `tests/test_phase6_llm.py` | 124 total tests |
| Experiment log | `docs/EXPERIMENT_LOG.md` | Created in Phase 0 |

## Data snapshot

- Catalog: 50,000 rows and 50,000 unique `parent_asin` values; all rows share the documented 10-field schema.
- Public set: 200 unique sessions and 200 unique targets; every target exists in the catalog.
- Scenario mix: 80 Buying, 80 Browsing, 30 Intent Override, and 10 Boundary.
- Hidden `intent_card` and `behavior` fields are not shipped; the local evaluator deterministically derives them from target metadata.
- Price is missing for 39,473 of 50,000 products. Later hard filtering must therefore treat missing metadata cautiously.
- Catalog archive SHA-256: `07FD142631FD6B03E2B4D09988C3EB7D53720E9D57010C79DB48EEAADA50A8F8`.
- Decompressed catalog SHA-256: `DA979B05A68AF864CB0DCF9EE6A81C010C7E66A57978AD286C7A2E005FC69A67`.

## Commands

Phase 2 requires Python 3.11 or later because NumPy 2.3.5 declares that minimum. NumPy is the only third-party dependency of the default runtime; `anthropic` is needed only for the optional Phase 6 route. NumPy `2.3.5` has no wheel for Python 3.14, so newer 2.x releases are accepted: after P5-E002 the active lexical + facet path no longer depends on NumPy tie partitioning.

The kept facet route uses NumPy at runtime. If the dependency has not been installed, the Agent deliberately disables the facet route and returns the exact lexical safety control rather than failing; that fallback is valid but does not produce the kept P2-E005 score.

### Setup

The tracked catalog archive must be decompressed once:

```bash
python -c "import gzip,shutil; shutil.copyfileobj(gzip.open('data/catalog.jsonl.gz','rb'),open('data/catalog.jsonl','wb'))"
python -m pip install -r requirements.txt
```

### Indexing

Build the reusable dense and facet artifacts once:

```bash
python -m scripts.build_retrieval_index
```

The lexical FTS5 index is still built in memory whenever `Agent` is constructed.

### Tests

```bash
python -m unittest discover -s tests -v
```

### Official local evaluator

```bash
python -m evaluator.local_evaluator
```

This writes the ignored `results.json` artifact.

### One-session debug

```bash
python -c "import json; from starter.agent import Agent; from evaluator.local_evaluator import catalog_index,evaluate,load_jsonl; ids,categories,products=catalog_index('data/catalog.jsonl'); result=evaluate(Agent('data/catalog.jsonl'),load_jsonl('data/public_set.jsonl')[:1],ids,categories,products); print(json.dumps(result,indent=2))"
```

This uses the official evaluator flow but restricts evaluation to the first public sample. State can be inspected through `Agent.session_state(session_id).to_dict()` and retrieval health/latency through `Agent.runtime_stats()`.

### Reproducing Phases 2–4 after Phases 5–7

Three defaults moved after those experiments were recorded. To reproduce a Phase 2, 3, or 4
number exactly, restore all three alongside that phase's own settings:

```bash
TECHJAM_FACET_DETERMINISTIC_TIES=0    # pre-P5-E002 facet tie order
TECHJAM_FACET_WEIGHT=0.55             # pre-P7-E006 facet weight
TECHJAM_QUESTION_BROWSING_DISCOUNT=0.08  # pre-P7-E004 browsing discount
```

Even then, the recorded Phase 2–4 numbers came from a NumPy 2.3.5 host; see the host note in
`docs/EXPERIMENT_LOG.md`.

### Reproduce Phase 2 sub-experiments

Set `TECHJAM_RETRIEVAL_MODE` to `lexical`, `hybrid`, `hybrid_facet`, or `scenario`, then run the official evaluator. The kept default is `hybrid_facet` with lexical weight `1.0`, facet weight `0.95` after P7-E006, dense weight `0.0`, route Top-N `100`, and RRF `k=60`. Phase 2 experiments below therefore pass `TECHJAM_FACET_WEIGHT='0.55'` explicitly to restore the Phase 2 setting. Top-N values, route weights, artifact location, and checksum validation have `TECHJAM_*` environment overrides in `starter/retrieval/config.py`.

The final controlled ablations are reproducible in PowerShell as:

```powershell
$env:TECHJAM_PHASE3_MODE='off'
$env:TECHJAM_RETRIEVAL_MODE='hybrid_facet'
$env:TECHJAM_LEXICAL_WEIGHT='1.0'
$env:TECHJAM_FACET_WEIGHT='0.55'
$env:TECHJAM_DENSE_WEIGHT='0'
$env:TECHJAM_RRF_K='60'
python -m evaluator.local_evaluator --output artifacts/evaluation/p2_e005.json

$env:TECHJAM_DENSE_WEIGHT='0.20'
python -m evaluator.local_evaluator --output artifacts/evaluation/p2_e006.json
```

### Reproduce Phase 3 sub-experiments

```powershell
$env:TECHJAM_PHASE3_MODE='persistence'
$env:TECHJAM_ACTIVE_POOL_SIZE='1000'
python -m evaluator.local_evaluator --output artifacts/evaluation/p3_e001.json

$env:TECHJAM_PHASE3_MODE='rerank'
$env:TECHJAM_PHASE4_MODE='off'
python -m evaluator.local_evaluator --output artifacts/evaluation/p3_e002.json
```

Unset `TECHJAM_PHASE3_MODE` to use the selected `rerank` default. Set Phase 3 `off` and Phase 4 `off` to reproduce P2-E005; use Phase 4 `off` to reproduce P3-E002.

### Reproduce Phase 4 sub-experiments

```powershell
$env:TECHJAM_PHASE3_MODE='rerank'
$env:TECHJAM_PHASE4_MODE='analyze'
python -m evaluator.local_evaluator --output artifacts/evaluation/p4_e001.json

$env:TECHJAM_PHASE4_MODE='ask'
python -m evaluator.local_evaluator --output artifacts/evaluation/p4_e002.json
python -m scripts.phase4_diagnostics --output artifacts/evaluation/p4_e002_diagnostic_result.json --diagnostics artifacts/evaluation/p4_e002_diagnostics.json
```

Unset `TECHJAM_PHASE4_MODE` to use the selected `ask` default.

### Reproduce Phase 5 sub-experiments

```bash
# P5-E001 / P5-E002 control: the pre-Phase-5 tie order
TECHJAM_FACET_DETERMINISTIC_TIES=0 TECHJAM_FACET_WEIGHT=0.55 \
  TECHJAM_QUESTION_BROWSING_DISCOUNT=0.08 \
  python -m evaluator.local_evaluator --output artifacts/evaluation/p5_e001.json

# P5-E002 alone, on the Phase 4 parameters
TECHJAM_FACET_WEIGHT=0.55 TECHJAM_QUESTION_BROWSING_DISCOUNT=0.08 \
  python -m evaluator.local_evaluator --output artifacts/evaluation/p5_e002.json

# P5-E003 Top-K allocator (rolled back)
TECHJAM_PHASE5_MODE=allocate TECHJAM_FACET_WEIGHT=0.55 \
  TECHJAM_QUESTION_BROWSING_DISCOUNT=0.08 \
  python -m evaluator.local_evaluator --output artifacts/evaluation/p5_e003.json

python -m scripts.compare_results artifacts/evaluation/p5_e002.json artifacts/evaluation/p5_e003.json --brief
```

`TECHJAM_PHASE5_MODE` accepts `off`, `trace` (default), and `allocate`.
`TECHJAM_TRACE_HISTORY_LIMIT` and `TECHJAM_FEATURE_CACHE_SIZE` tune the trace ring and the
decoded-feature cache; the cache trades `-17%` wall time for `+127 MiB` at `20000`.

### Run the optional Phase 6 LLM reranker

```bash
python -m pip install []  
TECHJAM_PHASE6_MODE=shadow python -m evaluator.local_evaluator --output artifacts/evaluation/p6_shadow.json
TECHJAM_PHASE6_MODE=rerank python -m evaluator.local_evaluator --output artifacts/evaluation/p6_e001.json
```

`shadow` prices the calls without changing the visible response; `rerank` applies the ordering.
Other overrides: `TECHJAM_LLM_MODEL`, `TECHJAM_LLM_SHORTLIST`, `TECHJAM_LLM_MAX_CALLS`,
`TECHJAM_LLM_FIRST_TURN`, `TECHJAM_LLM_LAST_TURN`, `TECHJAM_LLM_TIMEOUT`,
`TECHJAM_LLM_MAX_RETRIES`, `TECHJAM_LLM_MAX_TOKENS`, `TECHJAM_LLM_EFFORT`. With the mode unset the
agent makes no network call, declares no credential, and reports zero tokens.

### Reproduce Phase 7 tuning

```bash
TECHJAM_RRF_K=40 python -m evaluator.local_evaluator --output artifacts/evaluation/p7_e001.json
TECHJAM_FACET_WEIGHT=0.75 python -m evaluator.local_evaluator --output artifacts/evaluation/p7_e002.json
TECHJAM_QUESTION_BROWSING_DISCOUNT=0.14 python -m evaluator.local_evaluator --output artifacts/evaluation/p7_e004.json
python -m evaluator.local_evaluator --output artifacts/evaluation/p7_final.json
```

The last command uses the selected defaults and reproduces `P7-E009` byte-for-byte.

## Current implementation status

Phases 0–7 are complete. `P2-E005` remains the frozen candidate generator with the Phase 7 facet
weight; `P3-E002` remains the ranking stage; `P4-E002` remains the question policy with the Phase 7
browsing discount; `P5-E001` hardening and `P5-E002` deterministic tie-breaking are active. Dense
retrieval, persistence, the Top-K allocator, and the LLM reranker are implemented, tested, and
inactive.

## Current best metrics

The current best official TechnicalScore is `P7-E009`:

| Scope | Samples | HR@10 | MRR | MTTC |
|---|---:|---:|---:|---:|
| Overall | 200 | 0.530000 | 0.233736 | 6.190000 |
| Buying | 80 | 0.537500 | 0.289752 | 5.687500 |
| Browsing | 80 | 0.587500 | 0.236141 | 5.775000 |
| Intent Override | 30 | 0.400000 | 0.133452 | 8.266667 |
| Boundary | 10 | 0.400000 | 0.067222 | 7.300000 |

Overall Efficiency is `0.481000`; overall recommended TechnicalScore is `0.431321`. Against the
same-host Phase 4 control (`0.269396`) that is 43 new hits, 1 lost hit, 21 better shared-hit ranks,
24 worse, 10 earlier shared hits, and 2 later, for `+0.161925`. Roughly `0.09` of the gain is
attributable to catalog row ordering rather than to retrieval, which
`docs/TECHJAM_BUILD_MAP.md` records under known problems. Reported token usage remains zero
because Phase 6 is off. The final suite has `124 passed, 0 failed` and the evaluator takes
`136.75 s`.

## Conceptual reference

- `docs/ARCHITECTURE_EXPLAINED.md` — plain-language walkthrough of the two retrieval routes, the five stages of a turn, and the current ranking diagnosis (P18-D001).

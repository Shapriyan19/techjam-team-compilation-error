# Repository Overview

Phase 0 inventory was recorded on 2026-08-28 and revalidated on 2026-08-29 from commit `6c5d3d16b319460631b5e684fb72cae9979820d4`. Phases 1–7 were completed on 2026-08-29 without changing the official evaluator, catalog, labels, or scoring logic. Phase 7 froze P6-E001 and added submission auditing, defensive validation, regression tests, profiling, and final documentation without changing the selected algorithm.

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
|   |-- agent.py                       Official Agent, lexical route, hybrid orchestration/fallback
|   |-- clarification.py               Coverage/EIG analyzer, templates, and ask/no-ask policy
|   |-- clarification_config.py        Phase 4 modes, weights, thresholds, and turn schedule
|   |-- phase5_config.py               Tracing/cache and rank-only/hedge experiment settings
|   |-- phase6_config.py               Optional semantic provider/shortlist/fusion settings
|   |-- runtime_trace.py               Bounded opt-in component latency/failure records
|   |-- semantic.py                    Provider-neutral shortlist reranker, local provider, cache/fallback
|   |-- topk.py                        Rank-only control and conservative rank-10 hedge
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
|   |-- phase5_benchmark.py            Full evaluator latency/behavior/memory benchmark
|   |-- phase7_submission_audit.py     Environment/artifact/contract/output audit
|   `-- phase7_demo.py                 Reproducible two-turn demonstration trace
|-- tests/
|   |-- __init__.py
|   |-- test_evaluator.py              Three evaluator behavior tests
|   |-- test_phase1_state.py           Twelve Phase 1 state/parser/integration tests
|   |-- test_phase2_retrieval.py       Dense/facet/RRF/fallback/integration tests
|   |-- test_phase3_ranking.py         Persistence/reranker/unit/integration tests
|   |-- test_phase4_clarification.py   Coverage/EIG/policy/schema/integration tests
|   |-- test_phase5_reliability_topk.py Cache/fallback/tracing/allocator tests
|   |-- test_phase6_performance_semantic.py Deterministic equivalence and semantic safety tests
|   |-- test_phase7_submission.py      Failure matrix, contract, and parser corpus
|   `-- fixtures/phase7_state_regression.json General language/state cases
|-- requirements.txt                   Pinned NumPy 2.3.5 dependency
|-- .gitignore                         Ignores local catalog, results, secrets, and caches
`-- results.json                       Generated evaluator output; ignored
```

`data/catalog.jsonl` and `results.json` are local generated artifacts. `artifacts/evaluation/` is ignored. The compressed catalog and required facet files in `artifacts/retrieval/` are packaged; the dense files are optional rolled-back experiment assets.

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
       -> run safe catalog-backed facet/category Top-100
       -> fuse lexical and facet ranks with weighted RRF (1.0 / 0.55, k=60)
       -> retain the fresh fused Top-200 candidate set
       -> decode bounded catalog metadata only for those candidates
       -> deterministically score retrieval + state agreement + safe commercial evidence
       -> retain the unchanged reranked Top 10 recommendations
       -> analyze catalog-backed coverage/EIG over the reranked Top 100
       -> apply known/asked/no-preference, confidence, and turn-cost gates
       -> return a short deterministic question when worthwhile plus the same Top 10
  -> normalize exact catalog IDs
  -> calculate overall and scenario metrics
  -> write results.json
```

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

## Phase 5 performance, reliability, and allocation

P5-E001 reuses the Phase 3 `CatalogFeatureStore` as the clarification cache. When clarification is active, `retain_all()` raises the existing bounded cache to catalog capacity, so each immutable product record is decoded at most once per Agent process. `starter/clarification.py::_attribute_value_matrix` transposes the same P4 values in one candidate pass; extraction, coverage, entropy, EIG, thresholds, templates, and selection order are unchanged.

`starter/runtime_trace.py` provides bounded opt-in records controlled by `TECHJAM_TRACE_ENABLED` and `TECHJAM_TRACE_LIMIT`. Records contain session ID, turn, component, elapsed time, success/failure, fallback use, and error type. Components cover state, query rewrite, lexical/facet retrieval, RRF, reranking, clarification preparation, coverage, entropy/EIG, selection, Top-K allocation, and response construction. Tracing is disabled by default and never enters the official response.

Fallback order is:

```text
clarification unavailable/corrupt/exception -> no question + ranked recommendations
Top-K allocation exception                -> raw reranker Top 10
facet route unavailable                   -> existing lexical route/fusion fallback
```

`starter/topk.py` contains both P5-E002 modes. `rank_only` is the selected default and reproduces P5-E001/P4-E002 exactly. Experimental `hedge` protects ranks 1–9 and may replace only rank 10 from configurable ranks 11–30 when an unknown/soft catalog attribute is strongly dominated, the candidate is close in score, and no hard conflict/rejection exists. It activated 30 times without changing any official outcome, so it remains inactive.

## Phase 6 deterministic and semantic paths

P6-E001 preserves the P5 scoring path exactly. `DeterministicFeatureScorer.rank()` compiles slot token sets, hard/soft flags, negative token sets, budgets, rejected IDs, and weight iteration once per turn rather than once per product. `Agent._lexical_search()` maintains a bounded 4,096-entry LRU keyed by the exact prepared FTS5 OR expression and requested Top-N. The FTS5 table, BM25 weights, tokenizer, query terms, Top-100 limit, and row ordering are unchanged. The selected output remains byte-identical to P5-E001.

Startup still builds the immutable 50k FTS5 index in memory. An offline SQLite FTS artifact was considered but not added: it would duplicate the 60.5 MB source into a substantially larger submission asset for a one-process startup benefit, while missing-artifact fallback would still need the existing builder.

`starter/semantic.py` contains the P6-E002 provider-neutral interface, strict ID validator, exact-input cache, conservative rank fusion, and local `CatalogEncoderSemanticProvider`. The experiment uses the existing checksum-validated `catalog_random_indexing_v1` artifacts only to score the supplied Top-30 shortlist; it never invokes global nearest-neighbour retrieval. The request contains active rewritten intent, structured hard/soft slots, negatives, and compact catalog candidate information. The deterministic Top 3 are protected; positions 4–30 use deterministic/semantic RRF with semantic weight `0.35`. Reliable conflicts and rejected products cannot be promoted.

Configuration:

- `TECHJAM_SEMANTIC_RERANK_MODE=off|optional` (`off` selected)
- `TECHJAM_SEMANTIC_PROVIDER=none|catalog_encoder`
- `TECHJAM_SEMANTIC_RERANK_K` (20–40; experiment 30)
- `TECHJAM_SEMANTIC_PROTECTED_TOP_N` (experiment 3)
- `TECHJAM_SEMANTIC_WEIGHT` (experiment `0.35`)
- `TECHJAM_SEMANTIC_RRF_K` (experiment `60`)
- `TECHJAM_SEMANTIC_CACHE_SIZE` (default 2,048)

The default is fully offline deterministic P6-E001. In optional mode, missing/corrupt artifacts, unavailable provider, invalid IDs, empty encodings, timeouts, and provider exceptions fall back to P6-E001. P6-E002 was rolled back because it lost six hits while adding one.

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

Only `manifest.json`, `product_ids.json`, `facet_index.npz`, and `facet_tokens.json` are required for the active facet route: `1,848,666` bytes (`1.76 MiB`) total. The other `30,499,523` bytes are optional dense-experiment reproducibility assets and are not loaded by default.

### Offline/submission conclusion

The official rules permit declared dependencies and lightweight local assets and document no explicit artifact-size limit. They also warn that network access may be disabled. The project therefore pins `numpy==2.3.5`, packages or locally builds the catalog-trained artifacts, performs no model download, and needs no evaluator-time network access. The default lexical + facet path does not load the dense artifacts; the optional dense experiment path retains checksum validation and lexical fallback.

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
| Weighted RRF/configuration | `starter/retrieval/rrf.py`, `starter/retrieval/config.py` | Default lexical `1.0`, facet `0.55`, dense `0.0`, `k=60`; independently configurable |
| Offline index command | `scripts/build_retrieval_index.py` | Implemented |
| Persistent evidence | `starter/ranking/evidence.py`, `SessionState.candidate_pool` | Implemented/tested as P3-E001; rolled back from default |
| Deterministic reranker | `starter/ranking/features.py` | Implemented/tested as P3-E002; current ranking stage |
| Information gain / facet coverage | `starter/clarification.py::InformationGainAnalyzer` | Implemented/tested as P4-E001; diagnostic layer retained |
| Turn and question policy | `starter/clarification.py::ConservativeQuestionPolicy` | Implemented/tested as P4-E002; current default |
| Deterministic performance path | `starter/agent.py::_lexical_search`, `starter/ranking/features.py` | P6-E001 exact-expression BM25 cache and compiled state; current default |
| Semantic shortlist reranker | `starter/semantic.py`, `starter/phase6_config.py` | P6-E002 implemented/tested; disabled after regression |
| Top-K allocator | `starter/topk.py` | `rank_only` default; experimental rank-10 hedge rolled back after no measurable gain |
| LLM adapter / semantic reranker | None | Not implemented; optional Phase 6 |
| Runtime tracing/fallback | `starter/runtime_trace.py`, `starter/agent.py` | Opt-in bounded component traces; clarification and allocation fail safely |
| Phase 5 benchmark | `scripts/phase5_benchmark.py` | Official-evaluator timing, behavior fingerprints, and allocation statistics |
| Catalog loader | `starter/agent.py::_build_index` and `evaluator/local_evaluator.py::catalog_index` | Present; each independently streams the JSONL file |
| Public evaluator | `evaluator/local_evaluator.py` | Present; do not modify |
| Public development sessions | `data/public_set.jsonl` | Present: 200 labeled sessions |
| Frozen catalog | `data/catalog.jsonl.gz` | Present: 50,000 unique products |
| Evaluation configuration | `docs/evaluation_config.json` | Present |
| Requirements | `requirements.txt` | `numpy==2.3.5`; exact pin matches artifact build runtime |
| Tests | `tests/test_evaluator.py`, `tests/test_phase1_state.py`, `tests/test_phase2_retrieval.py`, `tests/test_phase3_ranking.py`, `tests/test_phase4_clarification.py`, `tests/test_phase5_reliability_topk.py`, `tests/test_phase6_performance_semantic.py` | 103 total tests |
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

Phase 2 requires Python 3.11 or later because the reproducibility-pinned NumPy 2.3.5 package declares that minimum. NumPy is the only third-party dependency.

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

### Reproduce Phase 2 sub-experiments

Set `TECHJAM_RETRIEVAL_MODE` to `lexical`, `hybrid`, `hybrid_facet`, or `scenario`, then run the official evaluator. The kept default is `hybrid_facet` with lexical weight `1.0`, facet weight `0.55`, dense weight `0.0`, route Top-N `100`, and RRF `k=60`. Top-N values, route weights, artifact location, and checksum validation have `TECHJAM_*` environment overrides in `starter/retrieval/config.py`.

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

```powershell
$env:TECHJAM_TOPK_MODE='rank_only'
python -m scripts.phase5_benchmark --output artifacts/evaluation/p5_e001_final.json --timing-output artifacts/evaluation/p5_e001_final_timing.json --diagnostic-output artifacts/evaluation/p5_e001_diagnostics.json

$env:TECHJAM_TOPK_MODE='hedge'
python -m scripts.phase5_benchmark --output artifacts/evaluation/p5_e002.json --timing-output artifacts/evaluation/p5_e002_timing.json --diagnostic-output artifacts/evaluation/p5_e002_diagnostics.json
```

Unset `TECHJAM_TOPK_MODE` to use selected `rank_only`. Set `TECHJAM_TRACE_ENABLED=1` only for diagnostics; normal evaluation keeps tracing off.

### Reproduce Phase 6 sub-experiments

```powershell
$env:TECHJAM_SEMANTIC_RERANK_MODE='off'
python -m scripts.phase5_benchmark --output artifacts/evaluation/p6_e001.json --timing-output artifacts/evaluation/p6_e001_timing.json --diagnostic-output artifacts/evaluation/p6_e001_diagnostics.json

$env:TECHJAM_SEMANTIC_RERANK_MODE='optional'
$env:TECHJAM_SEMANTIC_PROVIDER='catalog_encoder'
$env:TECHJAM_SEMANTIC_RERANK_K='30'
$env:TECHJAM_SEMANTIC_PROTECTED_TOP_N='3'
$env:TECHJAM_SEMANTIC_WEIGHT='0.35'
python -m scripts.phase5_benchmark --output artifacts/evaluation/p6_e002.json --timing-output artifacts/evaluation/p6_e002_timing.json --diagnostic-output artifacts/evaluation/p6_e002_diagnostics.json
```

Unset the semantic variables to use the selected offline deterministic control. The experiment requires the already-packaged Phase 2 dense encoder artifacts, but global dense retrieval remains disabled.

## Current implementation status

Phase 7 is complete and the final algorithm is frozen. P6-E001 preserves P5-E001 byte-for-byte while materially reducing reranking and BM25 latency. P6-E002 degraded official metrics and is disabled. The selected runtime is P6-E001 with semantic mode `off`, Top-K `rank_only`, dense retrieval disabled, and persistence disabled. Phase 7 adds output validation and submission evidence only.

## Current best metrics

The current best official TechnicalScore is shared by P4-E002, P5-E001, and the behavior-identical kept P6-E001:

| Scope | Samples | HR@10 | MRR | MTTC |
|---|---:|---:|---:|---:|
| Overall | 200 | 0.325000 | 0.175629 | 8.025000 |
| Buying | 80 | 0.362500 | 0.248140 | 7.412500 |
| Browsing | 80 | 0.325000 | 0.142669 | 8.100000 |
| Intent Override | 30 | 0.233333 | 0.112037 | 9.400000 |
| Boundary | 10 | 0.300000 | 0.050000 | 8.200000 |

Overall Efficiency is `0.297500`; overall recommended TechnicalScore is `0.274689`. Three Phase 7 evaluator-compatible reproductions and the final official run retain SHA-256 `F811F9B1440CA86A31B449CC2D770E4A15C5DE8BC01E685835FF98D936392B36`. P6-E002 scored HR@10 `0.300000`, MRR `0.171950`, and TechnicalScore `0.256785`, so it is rolled back. Reported token usage remains zero. The final suite has `118 passed, 0 failed`.

## Phase 7 submission hardening

`starter/agent.py::_validate_recommendations` and `starter/ranking/features.py::CatalogFeatureStore.contains` form the last response boundary: valid catalog IDs are retained in ranking order, duplicates/empty/non-catalog IDs are removed, and output is capped at `top_k`. Missing/corrupt facet artifacts still degrade to BM25; clarification and allocator exceptions preserve recommendations; disabled dense/semantic artifacts are not required.

`scripts/phase7_submission_audit.py` inventories environment, catalog, artifacts, checksums, contract signatures, and evaluator output. `scripts/phase5_benchmark.py` now reports startup components and approximate process RSS. `scripts/phase7_demo.py` generates the human-readable two-turn evidence in `docs/FINAL_DEMONSTRATION.md`. The complete submission checklist, commands, hashes, latency/memory results, fallbacks, disclosure, and limitations are in `docs/FINAL_REPRODUCIBILITY_REPORT.md`.

Final architecture classification:

- **Active default:** session state, deterministic parser/patches, query rewriting, BM25, facets, weighted RRF, fresh deterministic reranker, information-gain clarification, rank-only Top 10, and safe fallbacks/output validation.
- **Implemented but disabled/rolled back:** custom dense retrieval, persistent evidence, semantic reranking, and Top-K hedge allocation.
- **Stretch not implemented:** RL, ProtoNet, FAISS, external LLM reranking, and runtime multi-agent orchestration.

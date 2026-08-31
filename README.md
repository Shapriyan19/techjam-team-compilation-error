# Conversational Shopping Copilot — TikTok TechJam 2026, Track 4

**Team: Compilation Error**

A conversational shopping agent that guesses a hidden Amazon product (`parent_asin`) from a
frozen 50,000-product clothing catalog within at most 10 conversational turns. Every turn it
returns up to 10 ranked `parent_asin` values and may ask one clarification question.

The shipped agent is **fully deterministic**: it makes no network call, needs no API key,
downloads no model, and reports zero token usage. An optional LLM reranker exists but is off
by default (see [Model choice and cost](#model-choice-and-cost)).

---

## 1. How this addresses the problem statement

The task is a constrained product-guessing game: one hidden target `parent_asin`, ≤10 turns,
scored by `0.50·HitRate@10 + 0.30·MRR + 0.20·Efficiency`. Each user turn is evidence that
should move that one target higher in the ranking.

Our approach models every turn as five stages:

1. **Understand** the newest message — deterministic clue parser extracts slots
   (category, product type, budget, brand, colour, material, size/fit, style, occasion,
   features), hard-vs-soft strength, negation, and intent-override markers.
2. **Accumulate state** — explicit `SET` / `UPDATE` / `REMOVE` / `RESET_DEPENDENTS` patches
   on a per-session `SessionState`, so "women's shoes" then "under $80" retains both, and
   "actually sneakers instead" replaces the category and clears stale category-specific
   evidence while keeping independent constraints.
3. **Retrieve** the full catalog every turn via two complementary routes — lexical BM25 and
   a safe catalog-facet route — fused with weighted Reciprocal Rank Fusion (RRF).
4. **Rerank** the fused Top-200 with a deterministic feature scorer (IDF-weighted phrase and
   slot agreement, category/type/brand/colour/material match, price compatibility, negative
   evidence penalties).
5. **Decide** whether a clarification question is worth a turn, using catalog-backed
   coverage and expected-information-gain analysis over the reranked candidates, gated by a
   turn-aware schedule. Recommendations are **always** returned alongside any question.

Design constraints we deliberately respected (see `CLAUDE.md`):

- Missing metadata is neutral, never disqualifying — price is present on only ~21% of
  products, so hard filters are reserved for explicit, reliable constraints.
- Rankings are fused with RRF, never by averaging incomparable BM25 / cosine / facet scores.
- The candidate universe never closes to a persisted subset; fresh full-catalog retrieval
  runs every turn, with persistence layered on top.
- Every fallback tier returns valid recommendations; `respond()` never raises.

---

## 2. Results

Run with the official local evaluator (`python -m evaluator.local_evaluator`) on the
released 200-session public set. The selected runtime is experiment **P19-E001**.

### Public set (200 sessions)

| Metric | Value |
|---|---:|
| Hit Rate@10 | **0.940** |
| MRR | **0.721** |
| MTTC (mean turns to first hit) | 3.42 |
| Efficiency | 0.758 |
| Recommended TechnicalScore | **0.838** |
| Reported token usage | 0 |

### Public set by scenario

| Scenario | Samples | HR@10 | MRR | MTTC |
|---|---:|---:|---:|---:|
| Buying | 80 | 0.950 | 0.609 | 3.11 |
| Browsing | 80 | 0.963 | 0.824 | 3.20 |
| Intent Override | 30 | 0.900 | 0.792 | 4.40 |
| Boundary | 10 | 0.800 | 0.588 | 4.70 |

### Held-out synthetic sets (conservative estimate)

The public set is **front-loaded** — 73% of its targets sit in the first 1,000 of 50,000
catalog rows — so it overstates retrieval quality. We built
`data/synthetic_set*.jsonl` (`scripts/generate_synthetic_set.py`) with uniformly distributed,
disjoint targets as a more realistic proxy for the 800 private sessions.

| Set | HR@10 | TechnicalScore |
|---|---:|---:|
| synthetic-1 | 0.875 | 0.755 |
| synthetic-2 | 0.890 | 0.764 |
| synthetic-3 | 0.840 | 0.728 |

**We report both numbers throughout `docs/EXPERIMENT_LOG.md`.** Some accepted changes
deliberately trade a little public score for synthetic score (e.g. RRF `k` in P16-E001)
because the private target distribution is undocumented.

Baseline for comparison: the organizer's weak BM25 starter scores HR@10 `0.125`, MRR
`0.068`, MTTC `9.81` (`docs/baseline_results.json`).

---

## 3. Architecture

```text
respond(session_id, user_message, turn, top_k)
  │
  ├─ understand ....... deterministic parser + intent/override detection   starter/understanding.py
  ├─ update state ..... SET / UPDATE / REMOVE / RESET_DEPENDENTS patches    starter/state.py
  ├─ rewrite query .... full accumulated slot state → retrieval string     starter/understanding.py::rewrite_query
  │
  ├─ retrieve (full 50k catalog, every turn)
  │     ├─ lexical .... in-memory SQLite FTS5 + weighted BM25              starter/agent.py::_lexical_search
  │     └─ facet ...... safe catalog category/store/department postings    starter/retrieval/facets.py
  │     └─ fuse ....... weighted RRF (lexical 1.0, facet 0.95, k=20)       starter/retrieval/rrf.py
  │     └─ (dense route implemented, weight 0 by default)                  starter/retrieval/dense.py
  │
  ├─ rerank .......... deterministic feature scorer over fused Top-200     starter/ranking/features.py
  │     └─ IDF-weighted phrase / slot agreement                           starter/ranking/rarity.py
  │     └─ (persistent evidence pool implemented, off by default)         starter/ranking/evidence.py
  │     └─ (Claude Opus 5 shortlist reranker — off by default)            starter/llm/
  │     └─ (Top-K hedge allocator — off by default)                       starter/allocation.py
  │
  ├─ precision opening turns .. turns 1–2 emit a single high-confidence guess  starter/runtime_config.py
  ├─ clarify ......... catalog-backed coverage + EIG + turn schedule      starter/clarification.py
  ├─ Top-K guard ..... ordered, unique, non-empty parent_asin values      starter/agent.py::_unique_recommendations
  └─ trace .......... per-turn production trace (no target/label)         starter/tracing.py
```

The nine-tier fallback ladder (`starter/tracing.py`) guarantees a valid response even if an
optional component raises:

```text
full → semantic_rerank_fallback → allocation_fallback → clarification_fallback
     → understanding_fallback → retrieval_fused → retrieval_lexical
     → previous_recommendations → empty
```

### Offline artifacts (built once from the frozen catalog)

`python -m scripts.build_retrieval_index` writes `artifacts/retrieval/` (~31 MiB): the
catalog-trained dense encoder/embeddings (`catalog_random_indexing_v1`, NumPy-only, no
pretrained model) and the safe facet index, with a manifest recording the catalog SHA-256,
NumPy version, and file hashes. These are never regenerated per turn or per session.

### Key configuration

All configuration is via `TECHJAM_*` environment variables with sane defaults
(`starter/retrieval/config.py`, `starter/ranking/config.py`,
`starter/clarification_config.py`, `starter/runtime_config.py`, `starter/llm/config.py`):
`TECHJAM_LEXICAL_WEIGHT`, `TECHJAM_FACET_WEIGHT`, `TECHJAM_RRF_K`, `TECHJAM_DENSE_WEIGHT`,
`TECHJAM_PRECISION_TURNS`, `TECHJAM_PHASE3_MODE`, `TECHJAM_PHASE4_MODE`,
`TECHJAM_PHASE5_MODE`, `TECHJAM_PHASE6_MODE`, `TECHJAM_FEATURE_CACHE_SIZE`.

Full walkthroughs:

- `docs/ARCHITECTURE_EXPLAINED.md` — plain-language description of every stage.
- `docs/REPO_OVERVIEW.md` — file-level map and "where do I find X?" table.
- `docs/TECHJAM_BUILD_MAP.md` — phase status, decisions, known problems.
- `docs/EXPERIMENT_LOG.md` — every evaluated change with keep/rollback decisions.

---

## 4. Setup and installation

**Python 3.11 or later** is required (NumPy declares that minimum). Tested on 3.12 and 3.14.

```bash
# 1. clone, then decompress the tracked catalog archive once
python -c "import gzip,shutil; shutil.copyfileobj(gzip.open('data/catalog.jsonl.gz','rb'), open('data/catalog.jsonl','wb'))"

# 2. install dependencies (NumPy is the only runtime dependency)
python -m pip install -r requirements.txt

# 3. build the reusable retrieval artifacts once (no network, no model download)
python -m scripts.build_retrieval_index
```

If the catalog archive is distributed separately, download `catalog.jsonl.gz` from the
GitHub Release, verify it against the published `SHA256SUMS`, then run step 1.

---

## 5. Steps to reproduce the results

```bash
# full test suite (150 tests)
python -m unittest discover -s tests -v

# official evaluator on the public set → writes results.json
python -m evaluator.local_evaluator
```

`python -m evaluator.local_evaluator` is the single command that runs the agent in the
official harness. It imports `starter.agent.Agent`, calls `reset(...)` once per session and
`respond(...)` for up to 10 turns, and writes overall + per-scenario metrics to
`results.json`. The numbers in [section 2](#2-results) are that file's aggregate block.

Reproduce the conservative synthetic scores:

```bash
python -m scripts.generate_synthetic_set          # regenerates data/synthetic_set*.jsonl deterministically
python -m evaluator.local_evaluator --data data/synthetic_set.jsonl --output results_syn1.json
```

Single-session debug:

```bash
python -c "import json; from starter.agent import Agent; from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl; ids,cats,prods = catalog_index('data/catalog.jsonl'); print(json.dumps(evaluate(Agent('data/catalog.jsonl'), load_jsonl('data/public_set.jsonl')[:1], ids, cats, prods), indent=2))"
```

Non-obvious environment variables: none are required for the reported score. To reproduce an
earlier experiment, set the `TECHJAM_*` variables listed in `docs/EXPERIMENT_LOG.md` and
`docs/REPO_OVERVIEW.md` (e.g. `TECHJAM_PRECISION_TURNS=0` for the pre-P19 control,
`TECHJAM_RRF_K=60` for the pre-P16 fusion).

---

## 6. Agent interface

```python
class Agent:
    def reset(self, session_id: str, user_profile: dict) -> None:
        ...

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        return {
            "message": "Do you have a material preference?",
            "ask_attribute": "material",                       # one allowed attribute or null
            "recommendations": [{"parent_asin": "B000..."}],    # ordered best-to-worst, ≤10 scored
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
```

`ask_attribute` is one of `category`, `material`, `color`, `size`, `style`, `brand`,
`budget`, `feature`, `use_case`, `other`, or `null`. See `docs/agent_api_contract.json`.
`starter/agent.py` is a thin adapter; all logic lives in `starter/state.py`,
`starter/understanding.py`, `starter/retrieval/`, `starter/ranking/`, `starter/clarification*.py`.

---

## 7. Development tools, libraries, and data

| Category | What we used |
|---|---|
| Language / runtime | Python 3.11+ |
| Libraries (runtime) | **NumPy** (`numpy>=2.3.5,<3`) only; Python stdlib `sqlite3` (FTS5 + BM25), `json`, `re` |
| Libraries (optional, off by default) | `anthropic` / `google-genai` / `openai` — only if the LLM reranker is enabled |
| Retrieval | In-memory SQLite FTS5 lexical index; NumPy random-indexing dense encoder (`catalog_random_indexing_v1`, trained on the catalog, no pretrained weights); safe catalog-facet index |
| Ranking | Custom deterministic feature scorer; IDF weights read from the offline artifact |
| Dev tools | VS Code, git/GitHub, `unittest` (150 tests), custom diagnostic scripts in `scripts/` |
| APIs | **None** in the default path. Optional Phase 6 reranker can call the Anthropic / Gemini / NVIDIA NIM APIs — disabled by default |
| Dataset | **Amazon Reviews 2023** (`Clothing_Shoes_and_Jewelry`), McAuley Lab, UCSD — provided frozen by the organizer as `data/catalog.jsonl.gz` (50,000 products) and `data/public_set.jsonl` (200 labelled sessions). See `DATA_ATTRIBUTION.md` |
| Generated assets | `artifacts/retrieval/` (dense + facet index, ~31 MiB, built offline from the frozen catalog); `data/synthetic_set*.jsonl` (deterministically generated held-out sessions) |
| Assets NOT used | No images, video, audio, external vector DB, fine-tuned models, or multimodal processing |

---

## 8. Model choice and cost

**The submitted agent uses no model and makes no network call.** Retrieval and ranking run
in-memory from the frozen catalog. Reported token usage for every scored run is `0 / 0 / 0`.

- **Network requirement:** none. The agent runs correctly with zero network access and no
  credentials.
- **Offline fallback:** the deterministic pipeline *is* the default path; there is nothing
  to fall back from.
- **Latency:** the full 200-session public evaluation completes in ~135 s on a laptop
  (≈0.5 s/turn wall time, single-threaded), including the one-time in-memory FTS5 index
  build per process.
- **Estimated model cost:** **$0.00** for the submitted configuration.

### Optional LLM reranker (Phase 6 — off by default)

`starter/llm/` implements a shortlist reranker over ≤40 candidates, ≤3 calls per session,
12-second timeout, permutation-safe output validation, and a deterministic fallback on every
failure path. It activates only when `TECHJAM_PHASE6_MODE` is set to `shadow` or `rerank`
and the matching SDK + API key are present.

```bash
TECHJAM_PHASE6_MODE=shadow python -m evaluator.local_evaluator   # price the calls, response unchanged
TECHJAM_PHASE6_MODE=rerank python -m evaluator.local_evaluator   # apply the model ordering
```

We ran a 30-session paired pilot against `nvidia/nemotron-3.5-lightning-30b-a3b` (P17-E001):
it moved the target **down** more often than up (mean rank delta −1.9, 0 promotions into
Top-10, 9 demotions out). **We do not use it.** The default Anthropic model id is
`claude-opus-5`; estimated cost if enabled would be a few cents per session at most, but no
score is claimed for it because it never improved results.

---

## 9. Limitations and what we would improve with more time

1. **The largest measured retrieval gain rides on catalog row order.** 146 of the 200 public
   targets sit in the first 1,000 of 50,000 rows. Our deterministic facet tie-break resolves
   ties toward low row numbers, which systematically favours where public targets live. On
   the uniform synthetic sets the effect shrinks by ~4×. If the private catalog is reordered,
   nothing breaks but the score falls toward the synthetic figure. **Next:** a principled
   tie-break by popularity or lexical agreement instead of row index.
2. **Precision opening turns optimise the scoring rule, not shopping quality.** Emitting one
   guess on turns 1–2 trades MTTC for MRR (public MRR 0.56 → 0.72). A real shopper wants
   options. It is within the contract and a peer system does the same, but it is a
   scoring-rule optimisation we would revisit if the metric changed.
3. **Intent Override is the weakest scenario.** The override clears candidate evidence
   conservatively and the reranker has no explicit notion of a *superseded* constraint.
4. **Conservative lexicon-based parsing.** Slots are extracted with curated regex lexicons;
   unusual phrasing falls through to a token/phrase-overlap fallback. No catalog-scale
   parser-recall benchmark exists yet. An LLM clue parser (not reranker) is the most
   promising unused lever.
5. **Sparse metadata.** 39,473 products have no price, 23,887 no description, 5,219 no
   features. We treat missing metadata as neutral, which is safe but leaves signal on the
   table for the products that *do* have it.
6. **Tuning rests on 200 public sessions**, where 5 hits move HitRate by 0.025. Several
   accepted deltas are that small. A private-set-shaped regression suite is the top
   infrastructure gap.
7. **Dense retrieval, the persistent evidence pool, and the Top-K hedge allocator are all
   implemented and tested but inactive** — each hurt the score in its current form. They are
   kept as opt-in code for future work.
8. **The FTS5 index is rebuilt in memory per process** (~a few seconds). Fine for evaluation;
   would be persisted for a real deployment.
9. **The anonymised user profile is stored but unused.** It is intended as a small soft
   ranking prior; we have not yet found a way to use it that generalises without risking
   memorisation.

---

## 10. Team member contributions

| Member | Focus |
|---|---|
| Murugapz | Architecture, retrieval/RRF fusion, ranking and IDF weighting, experiment log, evaluation harness |
| Shapriyan19 | Session state and clue parser, clarification / information-gain policy, scenario handling, tests |
| Sushruth2911 | Retrieval and ranking tuning, synthetic-set evaluation |
| mu7hu | Diagnostics, ranking-config tuning |

(Contribution areas are approximate; see `git shortlog -sne` and `docs/EXPERIMENT_LOG.md`
for the detailed history.)

---

## 11. Repository layout

```text
starter/
  agent.py                official Agent — thin adapter, lexical index, fallback ladder, tracing
  state.py                SessionState, slots, SET/UPDATE/REMOVE/RESET_DEPENDENTS patches
  understanding.py        deterministic parser, intent/override detection, query rewrite
  retrieval/              lexical config, facet route, dense route, weighted RRF
  ranking/                candidate evidence pool, deterministic feature scorer, IDF rarity
  clarification.py        catalog-backed coverage + EIG analysis, turn-aware question policy
  allocation.py           deterministic Top-K hedge allocator (off by default)
  llm/                    optional shortlist reranker: Anthropic / Gemini / NVIDIA adapters
  runtime_config.py       runtime modes, trace ring, precision-turn config
  tracing.py              per-turn production trace + nine-tier fallback ladder
scripts/
  build_retrieval_index.py   one-time offline artifact build
  generate_synthetic_set.py  deterministic held-out session generator
  compare_results.py         session-level delta between two evaluator runs
tests/                    150 tests across 7 phase suites
evaluator/local_evaluator.py   frozen organizer simulator and scorer — never modified
data/                     frozen catalog + public sessions (organizer artifacts)
artifacts/retrieval/      offline dense + facet index (built from the frozen catalog)
docs/                     ARCHITECTURE_EXPLAINED, REPO_OVERVIEW, BUILD_MAP, EXPERIMENT_LOG
```

## 12. Data source and attribution

The catalog and sessions derive from **Amazon Reviews 2023** by McAuley Lab, UCSD
(`Clothing_Shoes_and_Jewelry`, joined on `parent_asin`, text and structured metadata only).
See `DATA_ATTRIBUTION.md` before using or redistributing the data. No images, videos,
credentials, or private holdout sessions are included.

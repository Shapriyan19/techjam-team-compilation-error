# Experiment Log

This document answers: **What changes improved or worsened the score?**

## Evaluation rules

- Use the untouched official evaluator, frozen catalog, and public labels.
- Change one meaningful hypothesis at a time.
- Run tests, run the 200-session evaluator, inspect every scenario, and choose Keep or Rollback.
- Do not hard-code public targets, users, sessions, or scenario-specific target IDs.
- Record a row for every evaluated change, including regressions and failed ideas.
- Scenario cells below use `HR@10 / MRR / MTTC`. The evaluator currently emits scenario HR@10, MRR, and MTTC, but not scenario Efficiency or TechnicalScore.

## Experiments

| ID | Description | HR@10 | MRR | MTTC | Efficiency | TechnicalScore | Buying | Browsing | Intent Override | Boundary | Decision |
|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|
| P0-E000 | Untouched weak BM25 starter; latest-message-only SQLite FTS5 retrieval, no questions, state, profile, dense route, reranker, or LLM | 0.125000 | 0.068034 | 9.810000 | 0.119000 | 0.106710 | 0.237500 / 0.126508 / 8.625000 | 0.025000 / 0.004514 / 10.750000 | 0.133333 / 0.104167 / 10.066667 | 0.000000 / 0.000000 / 11.000000 | Keep as reference baseline |
| P1-E001 | Per-session state, deterministic slots/patches/overrides, and full-active-state BM25 query rewriting; retrieval implementation unchanged | 0.140000 | 0.077575 | 9.690000 | 0.131000 | 0.119473 | 0.262500 / 0.133175 / 8.400000 | 0.025000 / 0.004514 / 10.750000 | 0.166667 / 0.150000 / 9.866667 | 0.000000 / 0.000000 / 11.000000 | Keep |
| P2-E001 | Lexical-only control after retrieval refactor | 0.140000 | 0.077575 | 9.690000 | 0.131000 | 0.119473 | 0.262500 / 0.133175 / 8.400000 | 0.025000 / 0.004514 / 10.750000 | 0.166667 / 0.150000 / 9.866667 | 0.000000 / 0.000000 / 11.000000 | Keep as control/fallback |
| P2-E002 | Global lexical+dense Top-100, equal-weight RRF (`k=60`) | 0.095000 | 0.047131 | 10.115000 | 0.088500 | 0.079339 | 0.162500 / 0.075813 / 9.375000 | 0.025000 / 0.004514 / 10.750000 | 0.133333 / 0.100000 / 10.100000 | 0.000000 / 0.000000 / 11.000000 | Rollback |
| P2-E003 | Add safe facet Top-100 to global lexical+dense RRF; weights `1.0/1.0/0.55` | 0.145000 | 0.069159 | 9.615000 | 0.138500 | 0.120948 | 0.250000 / 0.122619 / 8.500000 | 0.050000 / 0.011389 / 10.500000 | 0.133333 / 0.100000 / 10.100000 | 0.100000 / 0.011111 / 10.000000 | Superseded by P2-E005 |
| P2-E004 | Scenario-aware lexical/dense/facet RRF weights | 0.135000 | 0.079333 | 9.715000 | 0.128500 | 0.117000 | 0.262500 / 0.148333 / 8.375000 | 0.025000 / 0.012500 / 10.750000 | 0.133333 / 0.100000 / 10.100000 | 0.000000 / 0.000000 / 11.000000 | Rollback |
| P2-E005 | Lexical + facet Top-100 only; weights `1.0/0.55`, RRF `k=60`; dense excluded | 0.210000 | 0.117331 | 8.995000 | 0.200500 | 0.180299 | 0.337500 / 0.220570 / 7.650000 | 0.100000 / 0.017550 / 10.012500 | 0.166667 / 0.138889 / 9.866667 | 0.200000 / 0.025000 / 9.000000 | Keep/frozen control |
| P2-E006 | Lexical + facet + low-weight dense Top-100; weights `1.0/0.20/0.55`, RRF `k=60` | 0.165000 | 0.100847 | 9.420000 | 0.158000 | 0.144354 | 0.300000 / 0.189583 / 8.000000 | 0.050000 / 0.010972 / 10.512500 | 0.133333 / 0.133333 / 10.100000 | 0.100000 / 0.012500 / 10.000000 | Rollback |
| P3-E001 | Per-session 1,000-ID persistent rank-evidence pool over fresh P2-E005 retrieval | 0.210000 | 0.117456 | 9.005000 | 0.199500 | 0.180137 | 0.337500 / 0.220883 / 7.662500 | 0.100000 / 0.017550 / 10.025000 | 0.166667 / 0.138889 / 9.866667 | 0.200000 / 0.025000 / 9.000000 | Rollback |
| P3-E002 | Deterministic feature reranker over fresh P2-E005 Top-200 candidates; persistence off | 0.215000 | 0.115323 | 8.955000 | 0.204500 | 0.182997 | 0.337500 / 0.223140 / 7.637500 | 0.100000 / 0.017946 / 10.012500 | 0.200000 / 0.117593 / 9.633333 | 0.200000 / 0.025000 / 9.000000 | Keep/default |
| P4-E001 | Catalog-backed coverage/EIG analysis over reranked Top-100; no user-visible policy | 0.215000 | 0.115323 | 8.955000 | 0.204500 | 0.182997 | 0.337500 / 0.223140 / 7.637500 | 0.100000 / 0.017946 / 10.012500 | 0.200000 / 0.117593 / 9.633333 | 0.200000 / 0.025000 / 9.000000 | Keep as diagnostic control |
| P4-E002 | Conservative coverage/EIG clarification policy; Top 10 always retained | 0.325000 | 0.175629 | 8.025000 | 0.297500 | 0.274689 | 0.362500 / 0.248140 / 7.412500 | 0.325000 / 0.142669 / 8.100000 | 0.233333 / 0.112037 / 9.400000 | 0.300000 / 0.050000 / 8.200000 | Keep/default |

## P0-E000 — untouched starter baseline

- Initial run: 2026-08-28
- Revalidated unchanged: 2026-08-29
- Source commit before documentation: `6c5d3d16b319460631b5e684fb72cae9979820d4`
- Dataset: `data/public_set.jsonl`
- Samples: 200
- Catalog: `data/catalog.jsonl`, decompressed from the tracked frozen archive
- Agent: `starter.agent.Agent`
- Output: ignored local artifact `results.json`
- Reported token usage: 0 prompt, 0 completion, 0 total

Commands run:

```bash
python -m unittest discover -s tests -v
python -m evaluator.local_evaluator
```

Test result: 3 passed, 0 failed.

The run exactly matches `docs/baseline_results.json` for sample count, HR@10, MRR, MTTC, Efficiency, and TechnicalScore. Buying is the strongest scenario. Browsing is nearly unsolved and Boundary has no hits, which is consistent with latest-message-only retrieval and a policy that never asks a clarification question.

Decision: keep this result as the immutable comparison baseline. It is not a competitive implementation and no runtime change was made in Phase 0.

## P1-E001 — state and active-query rewrite

- Date: 2026-08-29
- Hypothesis: carrying explicit active intent across turns will improve BM25 retrieval, especially after a disclosed constraint or override, without changing retrieval itself.
- Files created: `starter/state.py`, `starter/understanding.py`, `tests/test_phase1_state.py`.
- Files modified: `starter/agent.py` and the three project documents.
- Retrieval: unchanged SQLite FTS5 schema, tokenizer, column weights, OR query, and Top-K limit.
- Reported token usage: 0 prompt, 0 completion, 0 total.

Commands run:

```bash
python -m py_compile starter/agent.py starter/state.py starter/understanding.py
python -m unittest discover -s tests -v
python -m evaluator.local_evaluator
```

Test result: 15 passed, 0 failed.

### Delta from P0-E000

| Scope | HR@10 delta | MRR delta | MTTC delta |
|---|---:|---:|---:|
| Overall | +0.015000 | +0.009541 | -0.120000 |
| Buying | +0.025000 | +0.006667 | -0.225000 |
| Browsing | +0.000000 | +0.000000 | +0.000000 |
| Intent Override | +0.033334 | +0.045833 | -0.200000 |
| Boundary | +0.000000 | +0.000000 | +0.000000 |

Efficiency improved by `+0.012000`; TechnicalScore improved by `+0.012763`.

Session-level comparison found three new hits (two Buying and one Intent Override), zero lost hits, one better shared-hit rank, zero worse shared-hit ranks, and no turn changes among shared hits. There are no measured regressions. Browsing and Boundary remain unchanged because Phase 1 deliberately retains the no-question policy.

Decision: **Keep.** The state foundation is correct under unit tests and improves the official public metrics without changing BM25 or adding an external dependency.

Known weaknesses: deterministic lexicons have limited recall; older unrecognized free text is retained in history but not indefinitely replayed into queries; scenario and hard/soft detection are heuristic; dependency reset is deliberately narrow; no clarification policy exists.

## P2 offline artifact build

- Algorithm: `catalog_random_indexing_v1`
- Product text schema: `techjam_product_text_v2`
- Dependency: exactly `numpy==2.3.5`
- Products: 50,000
- Vocabulary: 30,000 terms
- Embeddings: normalized float32, `50000 x 96`
- Dense build: `95.407 s`
- Facet build: `5.117 s`
- Total build: `100.935 s`
- Artifact size including manifest: `32,348,189` bytes (`30.85 MiB`)
- Runtime model/network download: none
- Dense artifact/model load: `0.581 s`
- Facet artifact load: `0.152 s`
- Full hybrid Agent startup including FTS5 build: `5.245 s`
- Average dense Top-100 query: `0.840 ms`
- Average facet Top-100 query: `0.311 ms`
- Average three-route RRF: `0.337 ms`
- Average full hybrid search including BM25 Top-100: `35.511 ms`

The first host verification used an unpinned NumPy 2.4.3 and produced different dense tie ordering. Pinning the runtime to the manifest's NumPy 2.3.5 restored exact `P2-E003` metrics. This was treated as a reproducibility bug, not as a tuning experiment.

## P2-E001 — lexical-only equivalence

- Mode: `TECHJAM_RETRIEVAL_MODE=lexical`
- Evaluator wall time: `37.537 s`
- Hypothesis: retrieval modularization must not change the approved Phase 1 lexical results.
- Result: every overall metric, scenario metric, and session outcome exactly matched `P1-E001`.
- Session delta: 0 new hits, 0 lost hits, 0 rank changes, 0 turn changes.
- Decision: **Keep** as the permanent control and failure fallback.

## P2-E002 — lexical + dense + RRF

- Mode: `TECHJAM_RETRIEVAL_MODE=hybrid`
- Routes: lexical Top-100 and dense Top-100
- Weights: lexical `1.0`, dense `1.0`; RRF `k=60`
- Evaluator wall time: `40.600 s`
- Primary outcome: Browsing was unchanged while Buying HR fell by `0.100000` versus Phase 1.
- Session delta versus Phase 1: 1 new hit (Buying), 10 lost hits (9 Buying, 1 Intent Override), 3 improved ranks, 10 worsened ranks, no turn changes.
- Decision: **Rollback.** Equal-weight dense fusion displaced too many strong exact lexical candidates.

## P2-E003 — add safe facet/category route

- Mode: `TECHJAM_RETRIEVAL_MODE=hybrid_facet`
- Routes: lexical, dense, and facet Top-100
- Weights: lexical `1.0`, dense `1.0`, facet `0.55`; RRF `k=60`
- Evaluator wall time: `39.570 s`
- Session delta versus Phase 1: 7 new hits (4 Buying, 2 Browsing, 1 Boundary), 6 lost hits (5 Buying, 1 Intent Override), 9 improved ranks, 8 worsened ranks, no turn changes.

Metric deltas versus Phase 1:

| Scope | HR@10 delta | MRR delta | MTTC delta |
|---|---:|---:|---:|
| Overall | +0.005000 | -0.008416 | -0.075000 |
| Buying | -0.012500 | -0.010556 | +0.100000 |
| Browsing | +0.025000 | +0.006875 | -0.250000 |
| Intent Override | -0.033334 | -0.050000 | +0.233333 |
| Boundary | +0.100000 | +0.011111 | -1.000000 |

Efficiency improved by `+0.007500`; TechnicalScore improved by `+0.001475`.

Decision at the time: **Keep pending the controlled dense ablation.** P2-E005 later established that the facet route, not the dense route, supplied the useful additional signal, so P2-E003 is retained only as an experiment record.

## P2-E004 — scenario-aware route weights

- Mode: `TECHJAM_RETRIEVAL_MODE=scenario`
- Buying weights: lexical `1.30`, dense `0.80`, facet `0.70`
- Browsing weights: lexical `0.65`, dense `1.35`, facet `0.45`
- Intent Override weights: lexical `1.00`, dense `1.20`, facet `0.60`
- General weights: lexical `1.00`, dense `1.00`, facet `0.55`
- Evaluator wall time: `39.708 s`
- Session delta versus Phase 1: 5 new hits (4 Buying, 1 Browsing), 6 lost hits (4 Buying, 1 Browsing, 1 Intent Override), 9 improved ranks (8 Buying, 1 Browsing), 8 worsened ranks (6 Buying, 2 Intent Override), 1 earlier Buying hit, and no later hits.
- Outcome: Buying MRR rose to `0.148333`, but overall HR and TechnicalScore fell below Phase 1; Browsing/Boundary hit gains disappeared.
- Decision: **Rollback.** Keep the mode for reproducibility, but do not make it active.

## P2-E005 — lexical + facet only

- Date: 2026-08-29
- Mode: `TECHJAM_RETRIEVAL_MODE=hybrid_facet`
- Routes: unchanged lexical Top-100 and facet Top-100; the dense route was completely excluded from loading, search, and RRF.
- Weights: lexical `1.0`, dense `0.0`, facet `0.55`; RRF `k=60`.
- Controlled variables: Phase 1 state/parser/query rewrite, catalog, evaluator, lexical/facet implementation, route Top-N values, and RRF constant were unchanged from P2-E003.
- Reported token usage: 0 prompt, 0 completion, 0 total.

Exact evaluator command after setting the listed environment weights:

```bash
TECHJAM_PHASE3_MODE=off python -m evaluator.local_evaluator --output artifacts/evaluation/p2_e005.json
```

With `TECHJAM_PHASE3_MODE=off`, the checked-in retrieval defaults reproduce `p2_e005.json` byte-for-byte under the requirements-pinned runtime. The host's separate global Python 3.13 installation had no NumPy installed, so facet initialization safely disabled and reproduced the P2-E001 lexical control; `python -m pip install -r requirements.txt` is therefore a required setup step for the P2-E005 candidate-generation path.

Metric deltas:

| Comparison | HR@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---|---:|---:|---:|---:|---:|
| versus Phase 1 | +0.070000 | +0.039756 | -0.695000 | +0.069500 | +0.060826 |
| versus P2-E003 | +0.065000 | +0.048172 | -0.620000 | +0.062000 | +0.059351 |

Session delta versus Phase 1: 14 new hits (6 Buying, 6 Browsing, 2 Boundary), 0 lost hits, 11 better ranks (10 Buying, 1 Browsing), 7 worse ranks (5 Buying, 1 Browsing, 1 Intent Override), 1 earlier Buying hit, and 0 later hits.

Session delta versus P2-E003: 15 new hits (7 Buying, 6 Browsing, 1 Intent Override, 1 Boundary), 2 lost hits (2 Browsing), 14 better ranks (10 Buying, 1 Browsing, 2 Intent Override, 1 Boundary), 3 worse ranks (2 Buying, 1 Browsing), and no earlier or later shared hits.

Decision: **Keep as the default Phase 2 retrieval configuration.** It improves every overall metric, improves Buying/Browsing/Boundary HR@10, matches Intent Override HR@10, and loses no Phase 1 hits. The result shows that P2-E003's dense route was suppressing useful lexical/facet candidates.

## P2-E006 — lexical + facet + low-weight dense

- Date: 2026-08-29
- Mode: `TECHJAM_RETRIEVAL_MODE=hybrid_facet`
- Routes: lexical, dense, and facet Top-100.
- Weights: lexical `1.0`, dense `0.20`, facet `0.55`; RRF `k=60`.
- Controlled variables: all other runtime, data, evaluator, query, Top-N, and fusion settings were identical to P2-E005.
- Reported token usage: 0 prompt, 0 completion, 0 total.

Exact evaluator command after setting the listed environment weights:

```bash
TECHJAM_PHASE3_MODE=off python -m evaluator.local_evaluator --output artifacts/evaluation/p2_e006.json
```

Metric deltas:

| Comparison | HR@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---|---:|---:|---:|---:|---:|
| versus Phase 1 | +0.025000 | +0.023272 | -0.270000 | +0.027000 | +0.024881 |
| versus P2-E003 | +0.020000 | +0.031688 | -0.195000 | +0.019500 | +0.023406 |
| versus P2-E005 | -0.045000 | -0.016484 | +0.425000 | -0.042500 | -0.035945 |

Session delta versus Phase 1: 8 new hits (5 Buying, 2 Browsing, 1 Boundary), 3 lost hits (2 Buying, 1 Intent Override), 9 better ranks (8 Buying, 1 Browsing), 7 worse ranks (6 Buying, 1 Browsing), 1 earlier Buying hit, and 0 later hits.

Session delta versus P2-E003: 6 new hits (4 Buying, 2 Browsing), 2 lost hits (2 Browsing), 17 better ranks (13 Buying, 1 Browsing, 2 Intent Override, 1 Boundary), 1 worse Buying rank, and no earlier or later shared hits.

Directly versus P2-E005, low-weight dense produced 0 new hits and 9 lost hits (3 Buying, 4 Browsing, 1 Intent Override, 1 Boundary), with 7 better ranks, 7 worse ranks, and no shared-hit turn changes.

Decision: **Rollback.** Dense at `0.20` remains better than Phase 1 and P2-E003 in aggregate, but it is materially and uniformly worse than the simpler P2-E005 winner. The existing dense implementation remains available behind `TECHJAM_DENSE_WEIGHT`; it is disabled in the default runtime.

## P3-E001 — persistent candidate evidence only

- Date: 2026-08-29
- Control: byte-identical P2-E005 result after the RRF-evidence refactor.
- Mode: `TECHJAM_PHASE3_MODE=persistence`.
- Pool cap: 1,000 candidate IDs per `SessionState`; fresh fused candidate limit 200.
- Fresh retrieval: full-catalog lexical Top-100 + facet Top-100 + P2-E005 RRF on every turn.
- Persistence inputs: current/previous RRF evidence, best rank, repeated support, and recency. Current RRF weight is `1.0`; all persistence additions are modest (`0.20/0.10/0.05/0.05`).
- Override: any explicit override advances the session epoch, clears mixed old candidate evidence, preserves independent Phase 1 slots, and immediately performs fresh retrieval.
- Rejection: only explicit ASIN references and unambiguous ordinal references such as “not the first two” affect product IDs; generic “not those” language does not invent targets.
- Reported token usage: 0 prompt, 0 completion, 0 total.

Exact command:

```powershell
$env:TECHJAM_PHASE3_MODE='persistence'
$env:TECHJAM_ACTIVE_POOL_SIZE='1000'
python -m evaluator.local_evaluator --output artifacts/evaluation/p3_e001.json
```

Delta versus P2-E005:

| HR@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---:|---:|---:|---:|---:|
| +0.000000 | +0.000125 | +0.010000 | -0.001000 | -0.000162 |

Session delta: 0 new hits, 0 lost hits, 1 better Buying rank (`public_0136`), 0 worse ranks, 0 earlier hits, and 2 later hits (`public_0028` Buying and `public_0070` Browsing).

Decision: **Rollback from the default.** The evidence pool is correct under tests and preserves recall, but it does not add a hit, slightly delays two hits, lowers TechnicalScore, and adds runtime/state complexity. Keep the implementation available for future dialogue experiments where later turns actually add clues; do not force it into P3-E002.

## P3-E002 — deterministic feature reranker

- Date: 2026-08-29
- Control: P2-E005 fresh retrieval, with P3-E001 persistence disabled.
- Mode: `TECHJAM_PHASE3_MODE=rerank`.
- Candidate set: fresh lexical/facet RRF Top-200 on every turn; no closed candidate pool.
- Metadata: offset-backed catalog access with a bounded 5,000-product decoded feature cache. Candidate records remain IDs/references rather than full product copies.
- Features: normalized fused-rank signal, route support, category/product type/brand/color/material/use case/style/occasion/feature agreement, known-price compatibility, optional support/recency inputs, explicit rejection, and reliable conflict.
- Missing metadata: neutral. Missing price is neither excluded nor penalized.
- Hard/soft: hard agreement uses full positive strength and reliable hard contradiction contributes the conflict penalty; soft agreement uses `0.65` strength and disagreement remains eligible.
- Reported token usage: 0 prompt, 0 completion, 0 total.

Exact command:

```powershell
$env:TECHJAM_PHASE3_MODE='rerank'
python -m evaluator.local_evaluator --output artifacts/evaluation/p3_e002.json
```

Delta versus P2-E005:

| HR@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---:|---:|---:|---:|---:|
| +0.005000 | -0.002008 | -0.040000 | +0.004000 | +0.002698 |

Session delta versus P2-E005: 3 new hits (2 Buying: `public_0065`, `public_0179`; 1 Intent Override: `public_0125`), 2 lost Buying hits (`public_0090`, `public_0136`), 4 better ranks (3 Buying, 1 Browsing), 1 worse Intent Override rank (`public_0197`, rank 1 to rank 4), and no earlier or later shared hits.

Compared with P3-E001, P3-E002 has the same new/lost/rank changes, makes the two persistence-delayed hits earlier again, and improves TechnicalScore by `+0.002860`.

Regression analysis: the two lost Buying targets had broad/shared metadata—a Mardi Gras scarf previously at rank 7 and active shorts previously at rank 10—so structured agreement promoted substitutes past them. Intent Override HR improves from `0.166667` to `0.200000`, but its MRR falls from `0.138889` to `0.117593`; this and the overall MRR regression are retained explicitly rather than tuned against public labels.

Decision: **Keep as the Phase 3 default.** It wins by the declared priority: highest TechnicalScore, then higher HR@10, with improved MTTC and no turn regressions. Persistence remains off, dense remains off, and the single initial feature-weight set was not grid-searched.

Final verification: `55 passed, 0 failed`. A default pinned-runtime evaluator rerun reproduced `p3_e002.json` byte-for-byte (SHA-256 `104086B1531A4345EB90B91538C5A333FC1857BA5B065D590B9B096461C8A0EC`). The host's unprepared global Python 3.13 lacks NumPy and therefore activates the documented lexical-only safety fallback; install `requirements.txt` before using the plain `python` command.

## P4-E001 — catalog coverage and information gain only

- Date: 2026-08-29
- Control: frozen P3-E002 retrieval and deterministic reranking.
- Mode: `TECHJAM_PHASE4_MODE=analyze`.
- Candidate set: reranked Top-100, configurable through `TECHJAM_QUESTION_CANDIDATE_K`.
- Behavior: records analysis only; response message, `ask_attribute`, and Top-10 order remain unchanged.
- Attributes: category, product type, use case, budget, brand, color, material, size/fit, style, occasion, and feature.
- Coverage: exact fraction of analyzed candidates with actual usable catalog metadata; missing values do not count.
- Information gain: numerically stable candidate-score softmax, weighted candidate entropy before/after the catalog-backed attribute partition, normalized EIG, then coverage/relevance adjustment.
- Budget: three deterministic candidate price bands when at least four reliable prices exist; otherwise utility falls to zero.
- Trace: 1,634 turn analyses, each containing all requested attribute fields plus candidate count, uncertainty, top confidence, and chosen attribute.
- Questions asked: 0.

Exact commands:

```powershell
$env:TECHJAM_PHASE4_MODE='analyze'
python -m evaluator.local_evaluator --output artifacts/evaluation/p4_e001.json
python -m scripts.phase4_diagnostics --output artifacts/evaluation/p4_e001_diagnostic_result.json --diagnostics artifacts/evaluation/p4_e001_diagnostics.json
```

The official artifact and diagnostic-wrapper result are byte-identical to P3-E002: SHA-256 `104086B1531A4345EB90B91538C5A333FC1857BA5B065D590B9B096461C8A0EC`.

Decision: **Keep the machinery, with user-visible behavior disabled for this control.** P4-E001 proves that coverage/EIG tracing can sit above P3 without perturbing ranking or questions.

## P4-E002 — conservative clarification policy

- Date: 2026-08-29
- Mode: `TECHJAM_PHASE4_MODE=ask`.
- Recommendations: the unchanged current Top 10 are returned on every turn, including question turns.
- Score weights: normalized EIG `0.20`, coverage `0.05`, intent relevance `0.50`, category relevance `0.25`.
- Thresholds: turns 1–3 `0.40`, turns 4–6 `0.55`, turns 7–8 `0.72`, turns 9–10 disabled; each known attribute adds `0.06`; Buying adds `0.12`; Browsing subtracts `0.08`.
- Confidence gates: minimum normalized candidate uncertainty `0.45`, maximum top confidence `0.35`, and no question after four known askable attributes.
- Repetition: internal and API attribute histories suppress aliases; a no-preference reply suppresses that internal attribute thereafter.
- Reported token use: 0 prompt, 0 completion, 0 total.

The first uncalibrated diagnostic would have asked on 199/200 first turns and selected brand 194 times. One principled relevance correction reduced brand dominance, followed by one conservative turn-cost calibration to protect Buying. No public-session grid search or target-ID tuning was performed.

Exact commands:

```powershell
$env:TECHJAM_PHASE4_MODE='ask'
python -m evaluator.local_evaluator --output artifacts/evaluation/p4_e002.json
python -m scripts.phase4_diagnostics --output artifacts/evaluation/p4_e002_diagnostic_result.json --diagnostics artifacts/evaluation/p4_e002_diagnostics.json
```

The official and diagnostic-wrapper outputs are byte-identical: SHA-256 `F811F9B1440CA86A31B449CC2D770E4A15C5DE8BC01E685835FF98D936392B36`.

Delta versus P3-E002:

| HR@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---:|---:|---:|---:|---:|
| +0.110000 | +0.060306 | -0.930000 | +0.093000 | +0.091692 |

Session delta: 22 new hits (18 Browsing, 2 Buying, 1 Intent Override, 1 Boundary), 0 lost hits, 1 better shared-hit rank, 1 worse shared-hit rank, 0 earlier shared hits, and 1 later shared hit. The later hit is `public_0070` (turn 2/rank 10 to turn 3/rank 1). `public_0166` remains a turn-4 Intent Override hit but moves from rank 1 to rank 2.

Question statistics:

- 194 questions total; `0.97` per session; average question turn `1.654639`.
- By scenario: Browsing 124, Buying 21, Intent Override 35, Boundary 14.
- By internal attribute: feature 77, product type 37, material 29, use case 17, brand 17, category 16, occasion 1.
- 82 no-preference answers were observed. The excluded raw-best attribute was prevented from repeating on 503 later turn analyses; 321 of those turn-level prevention events concerned an attribute explicitly marked no-preference.

Causal examples:

- Useful Browsing `public_0057`: after a category no-preference response was respected, a turn-3 feature answer added `100% Croslite; Imported` to the next query and produced a new rank-1 hit on turn 4.
- Useful Buying `public_0117`: a turn-1 feature answer supplied podiatrist/orthotic-footbed wording; query rewriting carried it into turn 2 and produced a new rank-1 hit.
- Useful Boundary `public_0169`: material was marked no-preference and not repeated; a later feature answer added `Imported; Pull On closure` and produced a new rank-4 hit on turn 3.
- Useful Intent Override `public_0130`: a leading cotton material answer survived the override, a later feature answer changed the rewritten query, and the target became a new turn-4 rank-3 hit.
- Regression `public_0070`: a travel/water-resistant feature answer delayed the previous turn-2 rank-10 hit to turn 3, although its final rank improved to 1.
- Rank regression `public_0166`: pre-override feature/use-case answers changed ranking after the mandatory override; the target stayed a turn-4 hit but moved from rank 1 to 2.

Clarification parser correction: for brand/color/material/style answers, the parser now prefers the leading explicit value for the attribute just asked. This prevented a composition string from replacing leading `wool` with trailing `polyester`; other messages retain the frozen Phase 1 selection behavior. The full reply also remains available to query rewriting.

Decision: **Keep as the Phase 4 default.** It materially improves every overall metric, adds 22 hits without losing any P3 hit, and produces the strongest gain in the intended weak Browsing scenario. Dense retrieval and persistent evidence remain disabled.

Final verification: `77 passed, 0 failed`. Phase 5 was not started.

## Template for the next evaluated change

| ID | Description | HR@10 | MRR | MTTC | Efficiency | TechnicalScore | Buying | Browsing | Intent Override | Boundary | Decision |
|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|
| P5-E001 | One Phase 5 allocation/tracing/fallback hypothesis | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | Keep / Rollback |

For each new entry also record files changed, commands, tests, reported token use, important failure cases, regressions, and the reason for the decision.

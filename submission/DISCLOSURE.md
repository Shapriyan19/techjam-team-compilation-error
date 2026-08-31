# Disclosure: model, network, latency, token usage, cost

Required by `docs/submission_rules.md`. Every figure below was measured on the submitted
commit `6289a16`, not estimated.

## Model and network

| | |
|---|---|
| Model used at scoring time | **None.** No LLM, no local model, no inference of any kind. |
| Network dependencies | **None.** No socket is opened on any default code path. |
| Credentials required | **None.** No environment variable needs to be set to reproduce the score. |
| External services | **None.** |
| Offline capable | Yes, completely. |

The system is deterministic: the same commit and catalog produce identical output on every run
and machine. Confirmed by reproducing `0.953778` to six decimal places across separate
processes.

An optional LLM shortlist reranker exists in `starter/llm/` (Anthropic, Gemini and NVIDIA
adapters) behind `TECHJAM_PHASE6_MODE`, which defaults to `off`. It is not used for any
reported score. Enabling it would require a credential passed through the environment and
network access; the one measured run cost 0.014 TechnicalScore (P17-E001 in
`docs/EXPERIMENT_LOG.md`).

## Token usage

| | |
|---|---|
| Prompt tokens | **0** |
| Completion tokens | **0** |
| Total | **0** |

As reported by the evaluator in `results.json` under `reported_token_usage`. This is a true
zero, not an omission — no model client is ever constructed on the default path.

## Estimated cost

**$0.00.** No paid API is called at any point. The only resource consumed is local CPU.

## Latency

Measured on the hardware below, running the full public set.

| | |
|---|---|
| Startup (per `Agent` construction) | **19–23 s** |
| Per turn, median | **33 ms** |
| Per turn, mean | **116 ms** |
| Per turn, p95 | **723 ms** |
| Per turn, max observed | **1,144 ms** |
| Full 200-session evaluation | **~54 s** |
| Peak resident memory | **~544 MiB** under load (~307 MiB immediately after construction) |

Startup builds three in-memory indexes over the 50,000-row catalog in a single pass — the
SQLite FTS5 table, the shelf partition, and the phrase index with its document-frequency
table. It is paid once per process, not per session.

The per-turn spread is driven by shelf size: scoring is linear in the number of products on
the shelf, so a session on `Shirts T-Shirts` (1,354 products) costs far more than the median
shelf of 8. The tail also includes turn-1 browsing openers, where no requirement has been
stated yet and the deterministic feature reranker runs instead of the phrase scorer.

The official evaluator imposes no per-response timeout, and there is no organizer-provided
CPU, RAM or startup limit, since teams run the final evaluation in their own environments.

## Fallback behaviour

`starter/agent.py` degrades through a nine-tier ladder — `full`, `semantic_rerank_fallback`,
`allocation_fallback`, `clarification_fallback`, `understanding_fallback`, `retrieval_fused`,
`retrieval_lexical`, `previous_recommendations`, `empty` — rather than losing a turn. Each
stage is wrapped independently, and the worst tier reached is recorded in the per-turn trace.

Candidate generation specifically: when the shelf resolves from the shopper's opening message
the shelf *is* the candidate set; when it does not, the pre-existing BM25 + facet + weighted
RRF pool is used instead. **The fallback did not fire on any of the 800 measured sessions** —
the shelf resolved correctly on 800/800 across the public set and all three synthetic sets.

A missing or corrupt `artifacts/retrieval/` bundle degrades to a lower tier rather than
raising; since the shelf path does not consult it, this costs nothing measurable. Verified by
re-running with `TECHJAM_RETRIEVAL_ARTIFACTS` pointed at a nonexistent path: byte-identical
`0.953778`.

## Measurement environment

| | |
|---|---|
| Interpreter | CPython 3.14.6 (`Python 3.11+` is the declared requirement) |
| OS | Linux 7.0.13 (Fedora 43), x86-64 |
| CPU | 12th Gen Intel Core i5-12450H, 12 threads |
| Third-party packages loaded | none |
| Evaluator | unmodified official `evaluator/local_evaluator.py` |
| Catalog | frozen 50,000-product set, checksums in `data/SHA256SUMS` |

## Scores these figures accompany

| Dataset | HR@10 | MRR | MTTC | TechnicalScore |
|---|---:|---:|---:|---:|
| public (200) | 1.0000 | 0.925595 | 2.195 | **0.953778** |
| synthetic-1 | 0.9500 | 0.812353 | 2.915 | 0.880406 |
| synthetic-2 | 0.9400 | 0.849812 | 2.925 | 0.886444 |
| synthetic-3 | 0.9450 | 0.824020 | 2.950 | 0.880706 |

Synthetic average TechnicalScore **0.882519**. See `REPORT.md` for why that, rather than the
public figure, is the number to carry forward.

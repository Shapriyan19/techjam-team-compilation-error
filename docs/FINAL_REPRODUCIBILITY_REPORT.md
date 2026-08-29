# Final reproducibility report

Phase 7 froze the P6-E001 algorithm and added submission auditing, defensive output validation, failure-matrix tests, a general parser corpus, process-memory measurement, and final documentation. No retrieval, ranking, parsing, or dialogue policy was tuned.

## Final architecture

```text
user message
  -> per-session deterministic state patches and accumulated query rewrite
  -> SQLite FTS5/BM25 Top-100 (weight 1.0)
     + catalog facet Top-100 (weight 0.55)
  -> weighted RRF (k=60), fresh Top-200
  -> deterministic catalog-feature reranker
  -> catalog-backed coverage/information-gain clarification
  -> rank_only Top 10
  -> catalog-valid, unique, order-preserving response validation
```

Active defaults: state, rewriting, BM25, facets, RRF, fresh deterministic reranking, conservative clarification, rank-only Top 10, and safe lexical/recommendations-only fallbacks. Implemented but disabled or rolled back: custom dense retrieval, candidate persistence, semantic shortlist reranking, and rank-10 hedging. RL, ProtoNet, FAISS, external LLMs, and runtime multi-agent orchestration are not implemented.

## Environment and setup

- Audited host: Windows 11 AMD64, CPython 3.12.13.
- Supported requirement: CPython 3.11 or later with SQLite FTS5 enabled.
- Declared dependency: `numpy==2.3.5`.
- Network at evaluation time: not required.
- External services, credentials, downloads, and user-specific directories: not required.
- Runtime writes: the Agent requires none. The public evaluator writes its selected output path (`results.json` by default); diagnostic scripts write under `artifacts/evaluation/`.
- Paths: repository-relative defaults, with commands run from the repository root. No application path contains a Codex runtime or `C:\Users\...` dependency.
- Platform: tested on Windows; the code is standard Python/SQLite/NumPy and should be portable where SQLite includes FTS5. This cross-platform statement is an expectation, not a claim of a completed Linux/macOS audit.

Fresh setup from the repository root:

```bash
python -m pip install -r requirements.txt
python -c "import gzip, shutil; shutil.copyfileobj(gzip.open('data/catalog.jsonl.gz','rb'), open('data/catalog.jsonl','wb'))"
python -m unittest discover -s tests -v
python -m evaluator.local_evaluator
```

The compressed catalog and required facet artifacts are packaged. `python -m scripts.build_retrieval_index` is an optional reproducible rebuild command, not a required evaluator step.

## Runtime artifact inventory

| Artifact | Status/purpose | Bytes | SHA-256 | Missing/corrupt behavior |
|---|---|---:|---|---|
| `data/catalog.jsonl` | Required runtime catalog, extracted from packaged gzip | 60,546,327 | `DA979B05A68AF864CB0DCF9EE6A81C010C7E66A57978AD286C7A2E005FC69A67` | Agent cannot start; restore/extract catalog |
| `artifacts/retrieval/manifest.json` | Required facet schema/checksum root | 2,241 | `6F0DB654D23D7A961E77631D5B1BBA2F8D662726FD0B8A1EA2E050DEFA273A95` | Lexical fallback |
| `artifacts/retrieval/product_ids.json` | Required facet row-to-ID map | 650,001 | `B155FD18119A0C0007C0258A4CE560092FFD7748AEC6FDE607555F4415823D81` | Lexical fallback |
| `artifacts/retrieval/facet_index.npz` | Required compressed facet postings | 788,252 | `9A33EC97ED3727D952B4EEE6C8160AE419A5F7C5F19B5D4AC475023492C313AC` | Lexical fallback |
| `artifacts/retrieval/facet_tokens.json` | Required facet-token map | 408,172 | `F579FAB37B3EEB1AA2C957333AF36B928B7F1398D94BE875BED56C01B2FB1118` | Lexical fallback |
| `dense_embeddings.npy` | Optional rolled-back experiment | 19,200,128 | `8756996079C53D027D304042DBC570578752450ACA77C4EC180BF1E1B88494D9` | Default unaffected |
| `dense_encoder.npz` | Optional rolled-back experiment | 10,893,991 | `E54F70E0A6811BDD28AACA8C111F7678DF8F3634877AB82ACCCBD17477054C2B` | Default unaffected |
| `vocabulary.json` | Optional rolled-back experiment | 405,404 | `BC39ED2209FF843D8383729A1129F7E4EF440CDA9D4069557952509C51C8246E` | Default unaffected |

The three dense artifacts remain packaged only to reproduce rolled-back experiments; the default Agent neither initializes nor requires them. The full machine-readable audit is `artifacts/evaluation/p7_submission_audit.json`.

## Official results and determinism

Three post-hardening evaluator-compatible runs produced byte-identical result files, each with SHA-256 `F811F9B1440CA86A31B449CC2D770E4A15C5DE8BC01E685835FF98D936392B36`. The final exact official command reproduced it again.

| Scope | HR@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---|---:|---:|---:|---:|---:|
| Overall | 0.325000 | 0.175629 | 8.025000 | 0.297500 | 0.274689 |
| Buying | 0.362500 | 0.248140 | 7.412500 | — | — |
| Browsing | 0.325000 | 0.142669 | 8.100000 | — | — |
| Intent Override | 0.233333 | 0.112037 | 9.400000 | — | — |
| Boundary | 0.300000 | 0.050000 | 8.200000 | — | — |

## Performance and memory

The first cold profiled run on the audited host measured startup `21.104710 s`. Its startup breakdown was FTS5/catalog build `15.415345 s`, retrieval/facet artifacts `1.720462 s`, feature store `3.967948 s`, clarification `0.000015 s`, and optional semantic initialization `0.000006 s`. A later warm-filesystem production-default run measured startup `6.991288 s`; both values are reported because filesystem caching materially affects startup.

Production-default full evaluation: wall `97.613771 s` for 1,470 calls; mean `66.297665 ms`, p50 `12.715450 ms`, p95 `340.680100 ms`, max `791.075500 ms`. A trace-enabled cold run measured wall `142.717587 s`, mean `96.897373 ms`, p50 `19.868750 ms`, p95 `458.066700 ms`, max `2828.052600 ms`.

Approximate process RSS in the production-default audit was 23.1 MiB before loading, 214.1 MiB after the evaluator catalog, 355.6 MiB after Agent startup, and 783.1 MiB peak across the complete evaluation with 200 session states retained. The largest measured increments are catalog/evaluator structures, Agent FTS5 + facet/feature structures, then accumulated evaluator/session/cache state. Clarification itself averaged 4.56 ms and reached 18.02 ms in the trace-enabled run; it did not require a separate model. These are host-specific approximate RSS values, not a portable memory guarantee.

## Validation and fallbacks

The final suite passes 118/118 tests. Phase 7 adds 15 tests covering:

- missing facet file, corrupt manifest, and checksum mismatch -> valid lexical-only recommendations;
- clarification exception -> no question, current valid ranking retained;
- Top-K allocator exception -> raw reranker Top 10;
- absent optional dense/semantic files while disabled -> default starts normally;
- tracing off -> no directory or records required (tracing is bounded in memory when enabled, so no writable trace destination exists);
- invalid, empty, duplicate, or non-catalog internal IDs -> filtered without reordering valid IDs;
- exact `reset`/`respond` signatures, first/middle/turn-10/empty input, reset, and session isolation;
- general accumulation, hard/soft strength, negation, overrides, multiple overrides, no-preference, rejection, browsing, boundary, and feature language.

The final evaluator output has 200 sessions and the evaluator normalizer/Phase 7 response validator enforce unique catalog IDs, at most ten recommendations, and stable order. No fabricated IDs are present.

## API, token, and cost disclosure

External LLM/API calls: `0`. Prompt tokens: `0`. Completion tokens: `0`. Total external tokens: `0`. Estimated external cost: `$0`. All inference is deterministic local Python, SQLite, and NumPy.

## Configuration defaults

Lexical weight `1.0`; facet weight `0.55`; dense weight `0.0`; RRF `k=60`; lexical/facet Top-N `100/100`; fresh rerank set `200`; persistence disabled; Phase 4 clarification `ask`; semantic mode `off`; Top-K `rank_only`; tracing off. Environment variables are optional experiment/debug overrides only and are listed in `README.md`.

## Known limitations

- Amazon catalog metadata is sparse and inconsistent; missing metadata is treated neutrally.
- Intent Override (`0.233333` HR@10) remains weaker than Buying (`0.362500`).
- Cold startup is nontrivial because 50,000 products are loaded and the in-memory FTS5 table is rebuilt.
- The deterministic parser is deliberately conservative and leaves unknown language in the rewritten free text rather than hallucinating structured slots. For example, the exact fragment “any color is fine” alone is not a special parser rule; explicit “no preference for color” is recognized.
- Information gain depends on available catalog facet coverage and cannot recover absent metadata.
- The custom 96-dimensional catalog-trained dense representation and the small semantic shortlist experiment harmed official results, so both are off.
- The official output is deterministic for the audited dependency/platform combination; SQLite or NumPy implementation changes on another platform warrant revalidation.

## Reproducibility commands

```bash
python -m scripts.phase7_submission_audit
python -m unittest discover -s tests -v
python -m evaluator.local_evaluator
python -m scripts.phase5_benchmark --output artifacts/evaluation/p7_profile.json --timing-output artifacts/evaluation/p7_profile_timing.json --diagnostic-output artifacts/evaluation/p7_profile_diagnostics.json
python -m scripts.phase7_demo
```

Expected `results.json` SHA-256: `F811F9B1440CA86A31B449CC2D770E4A15C5DE8BC01E685835FF98D936392B36`.

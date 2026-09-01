# Setup and reproduction

Everything needed to reproduce the reported score. There is no install step and no
build step; the only prerequisite is the catalog file.

- **Method and limitations:** [`REPORT.md`](REPORT.md)
- **Latency, token usage, cost:** [`DISCLOSURE.md`](DISCLOSURE.md)
- **Full experiment record:** [`../docs/EXPERIMENT_LOG.md`](../docs/EXPERIMENT_LOG.md)

## Requirements

| | |
|---|---|
| Python | **3.11 or later.** Measured on CPython 3.14.6. |
| Dependencies | **None.** The agent is standard library only. |
| Network | **None.** No socket is opened on any default code path. |
| Credentials | **None.** No API key is read unless you opt into the inactive LLM route. |
| Disk | The 60 MB uncompressed catalog. No index artifacts are built or read. |

`pip install -r requirements.txt` is **optional** — see [`requirements.txt`](requirements.txt)
for what it arms and why the default path does not need it.

## 1. Get the catalog

`data/catalog.jsonl` is the only file the agent reads at startup. If it is already present,
skip this step. Otherwise, place `catalog.jsonl.gz` from the GitHub Release into `data/` and:

```bash
cd data && sha256sum -c SHA256SUMS && gzip -dk catalog.jsonl.gz && cd ..
```

The checksums in `SHA256SUMS` are recorded relative to `data/`, so verification has to run
from inside that directory. It lists two entries: `catalog.jsonl.gz`, which is what matters
here, and `techjam-participant-kit.zip`, which is not part of this checkout and will report as
missing — that is expected.

## 2. Run the agent in the official harness

One command, from the repository root:

```bash
python -m evaluator.local_evaluator
```

That constructs `starter.agent.Agent`, runs all 200 public sessions against the unmodified
official evaluator, prints the aggregate metrics, and writes per-session results to
`results.json`.

Note the module path takes **no** `.py` suffix — `python -m evaluator.local_evaluator.py`
fails with a `ModuleNotFoundError`.

To score a different set:

```bash
python -m evaluator.local_evaluator --dataset data/synthetic_set.jsonl --output artifacts/evaluation/syn1.json
```

## Expected output

```json
{
  "sample_count": 200,
  "hit_rate_at_10": 1.0,
  "mrr": 0.925595,
  "mttc": 2.195,
  "efficiency": 0.8805,
  "recommended_technical_score": 0.953778,
  "reported_token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

The run is deterministic: the same commit and the same catalog produce the same numbers on
every run and on every machine. Verified by reproducing `0.953778` to six decimal places
across separate processes.

**Read `0.953778` with care.** Public HR@10 is 1.000, which is a ceiling that cannot repeat on
unseen sessions, and the public set's targets sit disproportionately near the front of the
catalog file. The estimate to carry forward is **`0.882519`**, the average over three
synthetic sets drawn with uniform targets. `REPORT.md` explains why.

## Tests

```bash
python -m pytest tests/ -q
```

Expect **181 passed, 2 failed**. The two failures are
`tests/test_phase6_llm.py::ProviderSelectionTest` provider-selection cases for the inactive
LLM route; they also fail at `106ae1c`, before any of this work, and touch nothing on the
scored path.

## Environment variables

**None are required.** Every one below has a working default, and the reported score is
produced with all of them unset. They exist so a reviewer can reproduce an earlier
configuration or isolate a component.

| Variable | Default | Effect |
|---|---|---|
| `TECHJAM_USE_SHELF` | `1` | `0` disables shelf restriction and forces the pre-Phase-20 BM25 + facet + RRF retrieval path. |
| `TECHJAM_EMIT_WIDTHS` | `1,1,2,2` | Recommendations emitted, indexed by how many requirements the shopper has stated. `TECHJAM_EMIT_WIDTHS=` (empty) disables the rule. |
| `TECHJAM_EMIT_FULL_TURN` | `4` | Turn from which the full Top-K is always emitted regardless of the table above. |
| `TECHJAM_PRECISION_TURNS` | `0` | Set to `2` (with `TECHJAM_EMIT_WIDTHS=`) to restore the turn-indexed P19 emission rule. |
| `TECHJAM_PHASE4_MODE` | `ask` | `off` gives the ranking-only control with no clarifying questions. |
| `TECHJAM_RETRIEVAL_ARTIFACTS` | `artifacts/retrieval` | Where the optional index artifacts live. Pointing this at a missing path is harmless — see below. |
| `TECHJAM_PHASE6_MODE` | `off` | `shadow` prices LLM calls without applying them; `rerank` applies the ordering. Both require a credential and network access. Off by default and not used for any reported score. |
| `TECHJAM_LLM_PROVIDER` | `anthropic` | `anthropic` / `gemini` / `nvidia`. Only read when `TECHJAM_PHASE6_MODE` is not `off`. |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `NVIDIA_API_KEY` | unset | Credential for the corresponding provider. Pass through the environment; never commit a value. |

## Why there is no build step

`scripts/build_retrieval_index.py` produces a ~30 MB artifact bundle for the dense and facet
retrieval routes. **The shipped configuration does not need it.** Verified directly:

```bash
TECHJAM_RETRIEVAL_ARTIFACTS=/nonexistent python -m evaluator.local_evaluator --output /tmp/check.json
```

produces a byte-identical `0.953778`. The shelf resolves correctly on 800/800 sessions across
the public set and all three synthetic sets, so the artifact-backed fallback routes never
execute. `numpy` is imported lazily inside `starter/retrieval/facets.py`,
`starter/retrieval/dense.py` and `starter/ranking/rarity.py`, so it is never loaded at all
when those artifacts are absent — which is what makes the default path standard-library only.

The artifacts are still read when present, and the fallback tier still exists. It is insurance,
not a dependency.

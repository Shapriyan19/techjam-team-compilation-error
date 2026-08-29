# TechJam Conversational E-Commerce Search Challenge

Build an AI shopping agent that asks useful follow-up questions and recommends the customer's hidden target product within at most 10 turns.

## What You Receive

- A frozen catalog of 50,000 products from the `Clothing_Shoes_and_Jewelry` category of Amazon Reviews 2023.
- 200 labeled public sessions for local development.
- A weak BM25 starter agent and deterministic local evaluator.
- The Agent API contract and scoring rules.

The organizer keeps 800 additional sessions private for final evaluation.

## Task

For each session, your agent receives an anonymized preference profile and a short customer message. Raw user IDs, review text, timestamps, and purchase history are never disclosed. On every turn the agent may:

- ask a natural clarification question in `message` and identify one requested field in `ask_attribute`;
- return a ranked list of up to 10 catalog `parent_asin` values;
- do both in the same response.

The session ends when the target product appears in the scored Top 10 or after turn 10. Sessions cover Buying, Browsing, Intent Override, and Boundary behavior.

## Quick start

Use Python 3.11 or later with SQLite FTS5 enabled. From the repository root:

```bash
python -m pip install -r requirements.txt
python -c "import gzip, shutil; shutil.copyfileobj(gzip.open('data/catalog.jsonl.gz','rb'), open('data/catalog.jsonl','wb'))"
python -m unittest discover -s tests -v
python -m evaluator.local_evaluator
```

The extraction command is needed only when `data/catalog.jsonl` is absent. The compressed 50,000-product catalog and the required facet artifacts (`manifest.json`, `product_ids.json`, `facet_index.npz`, and `facet_tokens.json`) are packaged with the repository. They can be rebuilt with `python -m scripts.build_retrieval_index`, but rebuilding is not part of normal evaluator startup. Evaluation requires no network, model download, API key, or external service.

The frozen runtime is: per-session deterministic state and query rewriting -> BM25 Top-100 (`1.0`) + facet Top-100 (`0.55`) -> weighted RRF (`k=60`) -> deterministic reranking of fresh Top-200 -> catalog-backed information-gain clarification -> `rank_only` Top 10 -> catalog-valid/unique response validation. Dense retrieval, persistence, semantic reranking, and Top-K hedging are implemented experiments but disabled by default.

The final public result is HR@10 `0.325000`, MRR `0.175629`, MTTC `8.025000`, Efficiency `0.297500`, and TechnicalScore `0.274689`. The deterministic `results.json` SHA-256 is `F811F9B1440CA86A31B449CC2D770E4A15C5DE8BC01E685835FF98D936392B36`.

Useful optional diagnostics:

```bash
python -m scripts.phase7_submission_audit
python -m scripts.phase5_benchmark --output artifacts/evaluation/profile.json --timing-output artifacts/evaluation/profile_timing.json --diagnostic-output artifacts/evaluation/profile_diagnostics.json
python -m scripts.phase7_demo
```

Default configuration: lexical/facet weights `1.0/0.55`, dense `0.0`, RRF `k=60`, fresh rerank Top-200, persistence off, semantic off, Top-K `rank_only`, tracing off. `TECHJAM_TRACE_ENABLED=1` enables bounded in-memory component traces. Other `TECHJAM_*` variables in the configuration modules exist only to reproduce isolated experiments; submission evaluation should use the defaults.

Edit `starter/agent.py` to implement your system. Do not edit the evaluator or public labels when reporting your local score.
The command writes per-session results and aggregate metrics to `results.json`.

The included weak BM25 starter scores Hit Rate@10 `0.125`, MRR `0.068034`, and
MTTC `9.81` on the released public set. See `docs/baseline_results.json`.

## Agent Interface

```python
class Agent:
    def reset(self, session_id: str, user_profile: dict) -> None:
        ...

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        return {
            "message": "Do you have a material preference?",
            "ask_attribute": "material",
            "recommendations": [
                {"parent_asin": "B000..."},
                {"parent_asin": "B001..."}
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30}
        }
```

`ask_attribute` is one of `category`, `material`, `color`, `size`, `style`, `brand`, `budget`, `feature`, `use_case`, `other`, or `null`. See `docs/agent_api_contract.json`.

## Technical Metrics

- **Hit Rate@10:** fraction of sessions that find the target within 10 turns.
- **MRR:** mean reciprocal rank of the target; a miss contributes zero.
- **MTTC:** mean first-hit turn; a miss is assigned turn 11.
- **Reported token usage:** prompt and completion tokens returned by the team's model client.

```text
TechnicalScore = 0.50 × HitRate@10 + 0.30 × MRR + 0.20 × Efficiency
Efficiency = clip((11 - MTTC) / 10, 0, 1)
```

`TechnicalScore` is an objective input to the `Technical Execution` assessment. It is not a separate judging criterion and does not represent the entire `Technical Execution` score.

Only exact `parent_asin` equality produces a hit. Core metrics are also reported by scenario.

## Model Choice and Cost

Teams may use any legally accessible LLM API or local model. Teams manage their own credentials and must never commit API keys. Model choice, estimated cost, token usage, and latency must be disclosed. Token usage is a feasibility metric, not part of the core technical score. The organizer does not provide or reimburse model API credits; teams are responsible for any costs incurred through optional external services.

## Files

```text
data/public_set.jsonl             200 labeled development sessions
docs/competition_specification.md participant rules and evaluation protocol
docs/agent_api_contract.json      machine-readable Agent contract
docs/evaluation_config.json       scoring configuration
docs/baseline_results.json        reproducible weak-starter reference score
starter/agent.py                  editable weak starter
evaluator/local_evaluator.py      public-set simulator and scorer
```

## Judging and Submission Policy

- Participant submission requirements: `docs/submission_rules.md`
- Organizer-only final judging controls: `organizer/JUDGING_RUNBOOK.md`
- Organizer private release checklist: `organizer/private_release_checklist.md`
- Judging day operations SOP: `organizer/JUDGING_DAY_SOP.md`

## Data Source

The catalog and sessions are derived from Amazon Reviews 2023 by McAuley Lab, UCSD. See `DATA_ATTRIBUTION.md` before using or redistributing the data.
Sessions are sampled deterministically from the official Clothing 5-core leave-last-out split and joined to the frozen catalog.

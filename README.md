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

## Download the Catalog

Download `catalog.jsonl.gz` from the GitHub Release attached to this repository, then run:

```bash
gzip -dk catalog.jsonl.gz
mv catalog.jsonl data/catalog.jsonl
```

Verify the downloaded file using the published `SHA256SUMS` file.

## Run the Starter

Python 3.11 or later is required. Install the dependency and build the reusable retrieval artifacts once:

```bash
python -m pip install -r requirements.txt
python -m scripts.build_retrieval_index
python -m evaluator.local_evaluator
```

The artifact build includes the catalog-trained dense representation and the facet index, with no model download or runtime network access. Candidate generation is the P2-E005 lexical + facet setup with the Phase 7 facet weight (`1.0/0.95`, RRF `k=60`) and dense disabled, followed by the P3-E002 deterministic feature reranker over fresh Top-200 candidates. P4-E002 then analyzes catalog-backed coverage/information gain over the reranked Top-100 and asks one short deterministic question only when its turn-adjusted utility passes the configured threshold; the current Top 10 are always returned alongside it. Phase 5 added a nine-tier fallback ladder, a per-turn production trace, and a facet tie-break that is reproducible across NumPy builds. Persistence, dense retrieval, the Top-K hedge allocator, and the LLM reranker are all implemented and inactive; any missing artifact or failing component degrades to a lower tier rather than losing the turn.

The selected `P7-E009` runtime scores HR@10 `0.530000`, MRR `0.233736`, MTTC `6.190000`, and recommended TechnicalScore `0.431321` on the public evaluator, with `0` reported tokens and a `136.75 s` run. Set `TECHJAM_PHASE4_MODE=off` for the ranking-only control, `TECHJAM_PHASE5_MODE=allocate` for the rolled-back Top-K allocator, or `TECHJAM_FACET_DETERMINISTIC_TIES=0` for the pre-Phase-5 tie order. `docs/EXPERIMENT_LOG.md` records every evaluated change, including the one where the highest-scoring configuration was deliberately not selected.

### Optional LLM reranking (off by default)

The agent needs no credentials and makes no network call unless `TECHJAM_PHASE6_MODE` is set:

```bash
python -m pip install []
TECHJAM_PHASE6_MODE=shadow python -m evaluator.local_evaluator   # price the calls only
TECHJAM_PHASE6_MODE=rerank python -m evaluator.local_evaluator   # apply the ordering
```

This route uses `claude-opus-5` over a 40-candidate shortlist, at most three calls per session, with a 12-second timeout and a deterministic fallback on every failure path. It has been tested against an injected fake client but **never run against a real model**, so no score is claimed for it.

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
starter/agent.py                  official Agent: staged pipeline, fallbacks, tracing
starter/retrieval/                lexical, facet, dense routes and weighted RRF
starter/ranking/                  candidate evidence and the deterministic feature scorer
starter/clarification.py          coverage/EIG analysis and the question policy
starter/llm/                      optional Claude Opus 5 shortlist reranker (off by default)
scripts/compare_results.py        session-level delta between two evaluator runs
evaluator/local_evaluator.py      public-set simulator and scorer
docs/EXPERIMENT_LOG.md            every evaluated change and its keep/rollback decision
docs/TECHJAM_BUILD_MAP.md         phase status, decisions, known problems, metrics
docs/REPO_OVERVIEW.md             repository map and reproduction commands
```

## Judging and Submission Policy

- Participant submission requirements: `docs/submission_rules.md`
- Organizer-only final judging controls: `organizer/JUDGING_RUNBOOK.md`
- Organizer private release checklist: `organizer/private_release_checklist.md`
- Judging day operations SOP: `organizer/JUDGING_DAY_SOP.md`

## Data Source

The catalog and sessions are derived from Amazon Reviews 2023 by McAuley Lab, UCSD. See `DATA_ATTRIBUTION.md` before using or redistributing the data.
Sessions are sampled deterministically from the official Clothing 5-core leave-last-out split and joined to the frozen catalog.

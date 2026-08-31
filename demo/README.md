# Session viewer

A read-only window onto what the agent does during a session. It exists to serve
the `One demonstrated multi-turn session` deliverable in
`docs/competition_specification.md`, and to make the reported score believable by
showing the reasoning behind it.

**Nothing here is part of the submission.** `demo/` is excluded from the
submission bundle. Nothing in `starter/` or `evaluator/` imports it, no evaluator
file is modified, `requirements.txt` is untouched, and the whole thing is
stdlib-only — the agent stays NumPy-only, offline, and zero-token.

## Run it

```bash
python -m demo.recorder      # bake the curated sessions (once, ~30s)
python -m demo.server        # http://localhost:8000
```

`python -m demo.server --no-live` serves recordings without ever constructing the
agent — useful on a machine without the catalog built.

Keys: `←` `→` step through turns, `space` plays.

`?session=public_0004&turn=3` opens straight on a given turn, so you can bookmark
the moment worth showing rather than clicking to it in front of an audience.

## What you are looking at

The left column is the conversation. The right column is the agent's own record
of that same turn, and it always reflects the **selected** turn — stepping
backward shows the state as it was, not the final state.

| Panel | Comes from |
| --- | --- |
| Query ledger | `phase4_turn_history[].rewritten_query`. Terms added this turn are lit. This is the retrieval story: the conversation accreting into the search. |
| Constraint state | `phase4_turn_history[].slots`; `hard`/`soft` strength from the end-of-session slot map |
| Clarification | `question_score`, `question_threshold`, `decision_reason` from the Phase 4 question policy |
| Runtime | `RuntimeTracer` — fallback tier, state patches, per-stage milliseconds |
| Ranked top 10 | The turn's `recommendations`, normalized exactly as the evaluator normalizes them, joined to catalog titles |

Turn thresholds are `0.0` by default (`starter/clarification_config.py:34-41`) —
asking is free, because a turn is spent either way. So the interesting signal is
the `decision_reason`, not the margin: the agent stays silent when it reports
`top candidate confidence sufficient`.

## Recorded vs live

Recordings load instantly and always work. **Run live** re-runs the same session
through the real `Agent.respond()` — the index is built once at server startup, so
a live run is one session's work, not a cold start. The agent is deterministic, so
a live run reproduces its recording. If a live run fails for any reason the server
falls back to the recording rather than blanking the page.

## Does it cheat?

No. `demo/session_runner.py` reads the ground-truth `parent_asin` to draw the star
and the `found` badge. It is never passed to `Agent.reset` or `Agent.respond`; the
agent sees exactly what the evaluator gives it.

The runner does not reimplement the simulator either — `initial_message`,
`customer_reply`, `materialize_hidden_fields`, `coarse_category`, and
`normalize_recommendations` are imported from `evaluator/local_evaluator.py`, so a
recorded session cannot drift from a scored one. Only the loop body differs, and
only in order to record.

That claim is enforced, not asserted:

```bash
python -m demo.recorder --all
```

records all 200 sessions and compares every `hit` / `first_hit_turn` / `best_rank`
against `results.json`, exiting non-zero on any mismatch. It found a stale
`results.json` the first time it ran.

## Picking sessions

`python -m demo.recorder --sessions public_0004,public_0021` records any subset.
The default set is one per scenario, chosen for what each makes visible:

| Session | Scenario | Shows |
| --- | --- | --- |
| `public_0001` | buying | A hard constraint up front, then three turns of narrowing |
| `public_0004` | intent override | The override lands on turn 3, `material` enters the slot map, the query gains `polyester`, target converts at rank 1 |
| `public_0021` | browsing | A vague opener; the slot map fills from nothing |
| `public_0104` | boundary | The customer has no preference, and the agent stops asking |

For intent-override sessions the override turn is `rng.choice([3, 4])` seeded from
the sample id, so which session demos best is only knowable after a run. Recording
all 30 and letting `_featured_override` rank them is how `public_0004` was chosen.
Worth knowing before you present: in most override sessions the target is already
at rank 1 and the override merely unlocks scoring — true to the rules, but flat to
watch. `public_0004` is one where the new intent actually changes the query.

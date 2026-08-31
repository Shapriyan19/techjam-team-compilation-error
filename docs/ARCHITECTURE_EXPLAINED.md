# Architecture, in plain terms

A conceptual walkthrough of how the agent works, why each piece exists, and what the
measurements say about where it currently falls short.

Companion documents — they answer different questions, so do not merge them:
- `REPO_OVERVIEW.md` — what exists and where (file-level map).
- `TECHJAM_BUILD_MAP.md` — phase status, next task, known problems.
- `EXPERIMENT_LOG.md` — what changed the score, with keep/rollback decisions.

---

## The two retrieval routes

Both search the same 50,000-product catalog, but over **different text**. That is the whole
point: they fail in opposite directions.

### Lexical route — "who mentions these words?"

SQLite FTS5 + BM25 over the **full product text**, defined in one place:
`starter/agent.py::_lexical_search`.

The index (`starter/agent.py::_build_index`) covers title, categories, features, details,
store, description. Per-column BM25 weights:

| Column | Weight |
|---|---|
| `parent_asin` | 0.0 (excluded) |
| `title` | 6.0 |
| `categories` | 4.0 |
| `features` | 2.5 |
| `details` | 2.5 |
| `store` | 1.5 |
| `description` | 1.0 |

- **Strength:** catches anything the shopper says, including phrases buried in a features bullet.
- **Weakness:** it is word-matching. A product whose description merely *mentions* cotton
  scores like one that *is* cotton.

Called from three sites: `mode="lexical"` (BM25 alone, not the default), the `hybrid_facet`
default (as the `lexical` route into RRF), and the degraded fallback tier when the facet
retriever is unavailable.

### Facet route — "who genuinely IS this kind of thing?"

Only **structured catalog fields** — see `starter/retrieval/text.py::facet_terms`: the last
3 categories, `store` (brand), `department`, and a whitelist of `details` keys. It
deliberately **ignores title and description entirely**.

Field weights (`starter/retrieval/facets.py::FIELD_WEIGHTS`), by how much each field says
about product identity:

| Field | Weight |
|---|---|
| `category` | 3.0 |
| `department` | 2.5 |
| `store` | 2.0 |
| `detail` | 1.5 |

- **Strength:** near-immune to marketing prose. If `department` says "Womens" and the
  category path ends in "T-Shirts", that is what the product *is*, not what its copy claims.
- **Weakness:** these fields are sparse — most products have thin `details` — so it misses a lot.

### Why RRF and not score averaging

BM25 returns values like `-8.3`; the facet route returns a weighted IDF sum. **Those scales
are meaningless relative to each other** — adding or averaging them is arithmetic without
semantics.

So the scores are discarded and only the *positions* are kept. Each route's rank-1
contributes `weight / (k + 1)`, rank-2 `weight / (k + 2)`, and so on; a product's
contributions are summed. `k` controls how sharply high ranks dominate — lowered 60 → 20 in
P16-E001 to reduce RRF's structural bias toward multi-route presence over rank quality.

---

## The five stages of a turn

`starter/agent.py::respond` runs these in order.

**1. Understanding** — parse the new message and update the running picture: fill slots
(color, material, category, ...), record rejections ("not leather"), store the literal
phrases used. **This accumulates across turns** — turn 5 knows everything from turns 1-4.

**2. Query rewriting** — turn accumulated state into one search query. Not just the latest
message: everything still believed true, minus anything the shopper has overridden.

**3. Retrieval** — run **both routes over the full 50,000-product catalog, every turn**,
300 results each, fused by RRF into ~600 candidates.

> This is a deliberate rule (CLAUDE.md #5). The tempting alternative is to narrow a
> shortlist turn by turn, but then one bad early inference permanently deletes the target
> with no path back. Instead we re-search everything each turn and let *ranking* carry the
> memory.

**4. Ranking** — score the ~600 candidates on 17 features (retrieval position, slot
agreement, phrase agreement, penalties for rejected and conflicting items), sort, take the
top 10. This is where accumulated evidence is supposed to pay off.

**5. Clarification** — decide whether to ask a question, and which of the 10 allowed
`ask_attribute` values yields the most information. Asking costs a turn, so the policy asks
only when the expected payoff justifies it.

Layered around all of it: **every stage degrades rather than fails.** Facet artifacts
missing → lexical only. Reranker raises → raw fused order. Each fallback still returns a
valid ranked list, because returning nothing scores zero.

---

## Current diagnosis: the ranker echoes the retriever

Measured on `data/synthetic_set3.jsonl`, 200 sessions, via
`scripts/rank_decomposition.py` (writes `decomposition_syn3.json`).

Session outcomes:

| Bucket | Count |
|---|---|
| Target at rank 1 | 82 |
| Target at rank 2-10 | 86 |
| Target beyond rank 10 | 28 |
| Target not in candidate pool | 4 |

For the 86 rank-2-10 sessions, mean score gap to the winner is **0.1331**. Attribution:

| Source | Contribution to the gap |
|---|---|
| `retrieval_rank` | **+0.1393** |
| All 15 other features combined | **-0.0062** |

`retrieval_rank` alone accounts for more than the entire gap. Every other feature ties or
slightly *favours* the target (material -0.031, color -0.008, fragment_agreement -0.003).

Average feature values, winner vs target:

```
category            0.7674  0.7674   <- identical
fragment_agreement  0.7460  0.7484   <- identical
feature_overlap     0.0353  0.0353   <- identical
brand / product_type / price /
style / occasion / persistence       <- 0.0000 for both
```

Two distinct problems:

1. **Half the feature set is dead weight.** `brand`, `product_type`, `price`, `occasion`,
   `persistence`, `recency` score 0.0 for *both* candidates in essentially every session.
   This is the sparse-metadata reality (CLAUDE.md data notes) surfacing in the ranker.
2. **The live features have no resolution.** Among the top 10 of "women's cotton t-shirt",
   all ten share the category, all ten match "cotton", all ten match the stated fragments.
   Coverage-ratio features saturate and tie, leaving retrieval order to break every tie.

**Conclusion: we have a strong retriever and a ranker that currently re-expresses its
output.** Stage 3 gets the target into the top 10 roughly 84-94% of the time; stage 4 is
supposed to pick the winner from those 10 and is not adding information.

Note on wording: `retrieval_rank` is `1/sqrt(fused_rank)` — the **RRF-fused** rank of
lexical + facet, not raw BM25. Lexical dominates that fusion (weight 1.0 vs facet 0.95), so
the effect is close, but the accurate statement is that top-10 ordering is decided by
*retrieval order*, not by BM25 specifically.

### Why this also explains the Phase 6 LLM result

P17-E001 found the LLM reranker actively harmful (0 promotions into the top 10, 9
demotions, and it demoted a correct rank-1 answer in 9 of 15 opportunities). The initial
reading was that the prompt withheld our features. The decomposition shows something
sharper: **at that stage our features carry almost no information either.** There was little
for the model to be given.

### Score headroom (synthetic-3)

| Lever | TechnicalScore headroom |
|---|---|
| Every hit at rank 1 (MRR 0.532 → 0.840) | **+0.092** |
| Fix all 32 misses (HR 0.840 → 1.0) | +0.080 |
| Every hit on turn 1 | +0.030 |

MRR is both the largest lever and the most tractable: 86 targets are already in the top 10
and only need to move up, and 25 of them sit at rank 2 (converting those alone is ~+0.019 TS).

### Proposed direction: rarity-weighted (IDF) matching

Ten cotton t-shirts cannot be separated while every matched term counts equally. Matching a
word every product has is worth nothing; matching a rare word is close to a fingerprint.

The IDF table already exists as a frozen offline artifact — `artifacts/retrieval/dense_encoder.npz`
carries a 30,000-term `idf` array aligned to `vocabulary.json`:

```
croslite   7.19      cotton   2.66
navy       4.95      imported 2.19
moisture   4.62      women    1.47
```

BM25 already uses IDF internally, but that knowledge stays locked inside FTS5 — it emits a
rank, and the ranker then reduces it to `1/sqrt(rank)`, discarding all term-level detail.

Ranked plan:

1. **IDF-weight `fragment_agreement`** (`starter/ranking/features.py::_fragment_agreement`
   currently uses `len(matched_tokens) / len(tokens)`). Directly attacks the tie that
   retrieval order is currently breaking; largest expected MRR move.
2. **IDF-weight `material` / `color` slot agreement** — same mechanism, second order.
3. **Retire the dead features** — no direct score gain, but stops them diluting and makes
   future decomposition legible.
4. **Then revisit `retrieval_rank`** — lowering it further should finally help *once real
   signal exists*. It does not help today: with everything else tied, dropping it just hands
   ordering to the same retrieval order through the `fresh_rank` tiebreak.

Re-run `scripts/rank_decomposition.py` after each change and confirm the gap attribution
actually shifts — that, not the aggregate score alone, is the evidence the change worked.

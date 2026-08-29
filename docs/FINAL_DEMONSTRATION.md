# Final demonstration session

This is a constructed legal example for explanation only. It does not use a public or hidden target label. The machine-readable trace is `artifacts/evaluation/p7_demo.json` and can be regenerated with `python -m scripts.phase7_demo`.

## Turn 1

**User:** “I need shoes for an upcoming trip.”

```text
Parsed patch       SET category=shoes (hard, confidence 0.92)
Active state       category=shoes
Rewritten query    shoes I need shoes for an upcoming trip
Retrieval          BM25 Top-100 (1.0) + facet Top-100 (0.55)
Fusion             weighted RRF, k=60, fresh Top-200
Rerank             deterministic catalog-feature scorer
EIG decision       feature coverage=0.83, EIG=4.90244346,
                   score=0.66064885 > turn-1 threshold 0.40
Top-K              rank_only, 10 unique catalog IDs
```

The Agent returns ten recommendations and asks: “Are there any particular features you want?” (`ask_attribute=feature`). The first three recommendations are `B07HHYDKK7`, `B071JH5HSS`, and `B07VXCWFD2`.

## Turn 2

**User:** “Comfortable and waterproof, under $80.”

```text
Parsed patches     SET budget_max=80 (hard)
                   UPDATE features=comfortable (soft)
                   UPDATE features=waterproof (soft)
Active state       category=shoes; budget_max=80;
                   features=[comfortable, waterproof]
Rewritten query    shoes comfortable waterproof under 80
                   Comfortable and waterproof, under $80
Retrieval          BM25 Top-100 (1.0) + facet Top-100 (0.55)
Fusion             weighted RRF, k=60, fresh Top-200
Rerank             deterministic catalog-feature scorer
EIG decision       product_type coverage=1.0, EIG=4.75782247,
                   score=0.65496006 > turn-2 threshold 0.52
Top-K              rank_only, 10 unique catalog IDs
```

The category from turn 1 survives, the new hard and soft constraints accumulate, and retrieval/ranking runs again from the full active state. The new first three recommendations are `B07TKRQ98Y`, `B07VDXF4NP`, and `B076LY513R`. Recommendations remain present alongside the next clarification.


# Phase 8 High-Score Analysis

Phase 8 tested protocol-aware mechanisms from the reference implementation on
the untouched official evaluator. No evaluator, label, or public session data
was changed.

## Results

| Experiment | HR@10 | MRR | MTTC | TechnicalScore |
|---|---:|---:|---:|---:|
| P8-E001 first `other` | 0.945 | 0.615244 | 2.610 | 0.824873 |
| P8-E002 second `other` | 0.945 | 0.615244 | 2.610 | 0.824873 |
| P8-E003 evidence ablation | 0.930 | 0.614196 | 2.715 | 0.814959 |
| P8-E004 precision Top-1 (2 turns) | 0.935 | 0.752238 | 3.130 | 0.850571 |
| P8-E006 static post-`other` | 0.940 | 0.728341 | 3.375 | 0.841002 |

The best tested configuration is first `other`, ShopPilot-style evidence
bonuses, and two precision turns. A second `other` and static post-`other`
ordering were rolled back. Dense retrieval was not used in this phase.

The reported reference score (~0.909 TechnicalScore) was not reproducible;
the reference repository's README and machine-readable evaluation artifact
also disagree.

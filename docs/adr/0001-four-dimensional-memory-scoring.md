# Four-dimensional memory scoring

Memory entries carry four independent scores instead of a single "relevance" number. Each score answers a different question:

| Score | Question | When computed | Range |
|-------|----------|--------------|-------|
| Confidence | Do we believe this is correct? | Updated on application (success/failure) and Decay | [0, 1] |
| Utility | Does applying this improve outcomes? | EMA of reward signals after each application | (-∞, +∞) |
| Cognitive Weight | Should we retrieve this now? | Computed dynamically at query time (not stored) | [0, 1] |
| Strength | Should we keep this alive? | ACT-R activation: `ln(Σ tᵢ⁻⁰·⁵)` | [0, ∞) |

We chose this over a single composite score because:

1. **Different consumers need different signals.** The Prompt Compiler filters by Confidence (don't inject untrustworthy rules). The consolidation system uses Strength (what to archive). The agent loop uses Utility (what to follow). The retrieval engine uses Cognitive Weight (what to surface).

2. **A single score hides trade-offs.** A rule can be high-confidence but low-utility (correct but unhelpful), or high-utility but decaying (useful when it was fresh, now stale). Collapsing these into one number forces premature decisions.

3. **Research supports separation.** ACT-R separates base-level activation from spreading activation. Generative Agents use `α·recency + β·importance + γ·relevance` as independent factors. ExpeL tracks success rate independently of retrieval frequency.

The cost is complexity: four scores to maintain, explain, and keep consistent. The alternative — a single relevance score — would be simpler but would require recomputing it differently for each consumer, effectively hiding the four dimensions inside one getter with mode flags.

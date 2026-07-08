# Memory tier promotion is one-directional and gated

Memory entries flow upward through tiers but never downward:

```
Episode → Case (always, on Run completion)
Case → Skill (on verified success + generalizability check)
Case → Rule (via Reflection from Correction)
Repeated Episodes → Rule (via Consolidation, requires 3+ matching patterns)
```

Demotion does not exist. A Rule can be Retired or Decayed, but it never becomes a Case again. A Skill can fall into disuse, but it stays a Skill until explicitly deleted.

We chose gated promotion over automatic promotion because:

1. **Premature promotion poisons the prompt.** If a single successful Case automatically becomes a Skill, one lucky run can install a fragile trajectory that breaks on the next variation. The Voyager paper found that gating skill creation behind execution verification prevents this.

2. **Rules need human signal.** Rules are extracted from Corrections, which are inherently human-initiated. The system doesn't invent rules from pure observation (Consolidation is the exception, but it requires 3+ pattern matches before proposing).

3. **Rollback is harder than retention.** If we auto-promoted aggressively and then needed to roll back, we'd need to track which downstream decisions were influenced by the bad promotion. Keeping entries at their current tier until explicitly promoted is simpler.

The cost: the system may be slow to generalize. A Case that succeeds 5 times in a row won't become a Skill until something triggers promotion (currently: manual or consolidation maintenance). Future work may add an auto-promotion threshold (e.g., "3 successes on the same app_type without modification").

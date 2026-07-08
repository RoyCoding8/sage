# Contradiction resolution uses scope-narrowing, not deletion

When two Rules conflict (e.g., "open port 8080 for web apps" vs. "open port 80 for Docker apps"), the system narrows their scope rather than deleting one.

Resolution procedure:
1. **Detection**: High context-similarity + low action-similarity between rules signals conflict. The consolidation module's `detect_contradiction` identifies these via embedding proximity + action divergence.
2. **Resolution**: Add a `precondition` field to scope each rule more precisely. "Open port 8080" gets precondition "when app_type in [node, python, java]". "Open port 80" gets precondition "when app_type in [docker, static]".
3. **Fallback**: If scope-narrowing fails (truly contradictory rules for the same context), the more recent rule wins — but both are preserved with the older one's confidence reduced.

We chose this over deletion because:
- Deletion loses information. The old rule may be correct for cases we haven't seen yet.
- Most "contradictions" are actually context-sensitivity (the rule is right in one context, wrong in another). Narrowing captures this precisely.
- Research (ACT-R, Voyager) shows that atomic, context-specific rules compose better than broad generalizations.

The cost: rules accumulate. A port-assignment question that started as 1 rule ("open port 8080") may become 5 scoped rules. This is acceptable because the Prompt Compiler already filters by relevance — only rules whose precondition matches the current task get injected.

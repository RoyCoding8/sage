# Rule scaling uses topic-based pre-filtering

When the rule store exceeds ~100 rules, pure embedding similarity degrades. We use a two-stage retrieval strategy:

**Stage 1: Topic filter (O(1))**
Categorize rules by app_type and action_domain at write time. At query time, filter to the matching category before semantic search. This reduces the candidate set from N to ~N/5.

**Stage 2: BM25 + embedding ranking (over filtered set)**
Apply MemoryRetrieval scoring (BM25 + cosine + decay + cognitive weight + tier boost) over the pre-filtered candidates.

We chose this over:
- **Pure embedding search** (degrades at scale, no topic awareness)
- **Hierarchical memory** (complex, needs a taxonomy that changes as rules accumulate)
- **Hard token budget + random sampling** (loses the best rules when budget is tight)

Implementation: Rules receive `topic_tags` at creation time. MemoryRetrieval ranks the cross-tier candidate set once, and PromptBlockCompiler consumes those ranked hits instead of issuing independent store queries. This keeps the prompt focused and avoids repeated ranking work as the Rule set grows.

Future: if rules exceed 1000, add a lightweight learned classifier that predicts "which topic tags are relevant for this task" — but that's not needed until we hit the scale.

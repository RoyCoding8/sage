# Agent decomposition uses constructor injection, not events

When splitting the Agent God Object into focused modules, we use constructor injection rather than an event-driven architecture.

Considered alternatives:
- **Event bus** (Agent emits TaskCompleted, modules subscribe): Elegant but introduces indirection that makes debugging harder. "Why did this rule get tracked?" requires tracing event subscriptions. The modules have strict ordering requirements (record case BEFORE updating provenance) that events obscure.
- **Functional pipeline** (Agent is a pipeline of transforms): Clean conceptually but awkward for stateful modules like MetricsRecorder that own persistence. Would need to thread state through the pipeline or use closures over mutable state.
- **Constructor injection** (Agent receives pre-built modules): Direct, testable, debuggable. Each module has a clear interface. Tests inject fakes. Production wires the real implementations.

We chose injection because:
1. The modules have dependencies on each other (MemoryLifecycleManager needs ProceduralMemory, Consolidator, HybridRetrieval). An event bus would need to manage these same dependencies anyway.
2. Ordering matters. Post-task, we need: record case → update provenance → bump metrics → maybe run maintenance. A sequential call chain is clearer than event ordering guarantees.
3. The 20:5 construct-to-inject ratio is the problem, not the injection pattern itself. Moving from "Agent constructs everything" to "Agent receives everything" solves testability without adding architectural complexity.

# H1 Actual Source Closure

## Context

- `plan.md` H1 requires ContextPlan to cover every real provider input source.
- Existing source snapshots scan a fixed directory list, while actual writer assembly records only coarse source types.
- Provider payloads can therefore contain prompt, config, tool schema, model assignment, tool result, or context fragments that are not tied to a verifiable source descriptor.
- Existing H0 working-tree changes must be preserved and remain logically separate.

## Selected design

1. Keep `SourceDescriptor` and `SourceRegistry` in `app/context_engine/source_snapshot.py` as the single registry implementation.
2. Store planned source descriptors in `ContextPlanV2`; initialize one actual registry per `TurnScope` when the plan is activated.
3. Register semantic sources and exact provider-facing message/tool fragments during assembly and agentic execution.
4. Before provider I/O, verify mutable actual sources and reject any message/tool fragment absent from the registry.
5. Persist machine-readable planned/actual/unexpected/not-selected reconciliation in the turn trace.

## Execution steps

1. Implement stable descriptors, content/path capture, payload fragment enumeration, registry reconciliation, and mutable verification.
2. Replace fixed directory snapshot planning with explicit planned descriptors and TurnScope-owned actual source state.
3. Register model assignment, prompt/config/user/draft, tool schema, provider response replay, and tool-result fragments.
4. Add contract tests for source mutation, unregistered payload rejection, stable fingerprints, and reconciliation.
5. Run targeted tests, Ruff, full pytest, pip check, error contract audit, architecture profile, and scale profile.
6. Update `plan.md` and `CLAUDE.md`, then archive this record after review.

## Success criteria

- Every real provider payload fragment is registered before egress.
- Mutable actual sources fail verification after content or control revision changes.
- Planned/actual/unexpected/not-selected is machine-readable.
- Equal source content yields a stable fingerprint.
- There is no second source registry implementation.

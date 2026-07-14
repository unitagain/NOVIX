# H2 Token, Assembly, and Compaction Unification

## Context

- Session compaction currently uses message counts and `len(content) // 4`.
- Writer packing uses a fast mixed-language estimator.
- Gateway payload enforcement uses provider-aware `TokenAccounting`.
- Agentic execution and gateway payload accounting both own tool-result folding.
- Compact artifacts do not record selected turns, preserved tail boundary, or estimator evidence.

## Selected design

1. Make `app/context_engine/token_accounting.py` the single accounting contract for provider payloads, text sections, writer packing, and session compaction.
2. Group session messages into complete turns; preserve a recent tail constrained by both turn count and token budget without splitting a turn.
3. Keep previous compact projection, newly selected raw source turns, and recent raw tail as separate layers.
4. Extend compact artifacts with turn selection, tail boundary, source/summary accounting, and lineage verification.
5. Record provider-reported prompt usage as local estimator-error evidence without changing runtime policy thresholds.
6. Keep gateway payload accounting as the sole runtime folding owner and fold only explicitly recoverable tool results.

## Success criteria

- No compact operation splits a user/assistant/tool turn.
- Recent tail selection is deterministic and reports both count and token constraints.
- Every compacted raw event remains recoverable from the append-only event archive.
- Compact lineage has no missing parent, cycle, epoch inversion, or source-hash mismatch.
- Oversized nonrecoverable provider content is rejected; only recoverable tool results may fold.
- Token accounting includes estimator identity and observed P95 relative-error evidence.

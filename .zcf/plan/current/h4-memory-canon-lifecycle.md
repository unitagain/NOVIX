# H4 Memory and Canon Lifecycle Hardening

## Context

- Legacy memory parsing currently defaults missing or invalid status to `active`, trusted confidence to `1.0`, and unknown canon status to `confirmed`.
- Model-declared confidence plus type/scope is sufficient for automatic memory activation.
- Recall performs TTL mutations on the read path and duplicates conflict/supersession eligibility logic.
- Memory lifecycle service delegates validation back to public storage mutation methods, so it is not yet the effective owner.
- Canon retrieval and full-canon injection may include `needs_review` facts as if they were established truth.
- Memory packs do not bind the source revisions or context epoch from which they were derived.

## Selected design

1. Make `MemoryRecordV2` own conservative normalization, provenance/trust eligibility, transition rules, and graph validation.
2. Make `MemoryLifecycleService` the only lifecycle/relationship decision owner; public storage compatibility methods delegate to it, while private storage methods only persist approved changes.
3. Require verified deterministic source, provenance refs, allowed type/scope, low impact, reversibility, and no conflicts for automatic activation. Model confidence is supporting evidence only.
4. Keep recall as a pure selector over a validated graph snapshot; TTL transition and legacy migration remain explicit, idempotent lifecycle operations.
5. Normalize invalid/missing canon lifecycle data to `needs_review`, add explicit confirmed-fact eligibility, and use only eligible facts in retrieval/full-context injection.
6. Bind every memory pack to a deterministic source-revision fingerprint and context epoch; stale or legacy-unbound packs are visible but never reused as fresh context.

## Success criteria

- Missing/invalid legacy memory or canon status never becomes active/confirmed automatically.
- Non-active, expired, untrusted, conflicting, superseded, rejected, or provenance-free memory never enters recall.
- Confidence alone cannot activate model-derived memory.
- Conflict refs and supersede refs must exist, cannot self-reference, and supersession cannot form a cycle.
- Recall does not mutate persisted lifecycle state.
- TTL transition, legacy migration, and index rebuild are idempotent.
- Deleting the derived memory index permits a complete deterministic rebuild from records.
- Any bound source revision or context-epoch change marks a memory pack stale before reuse.

## Review result

- Status: completed at 2026-07-14 09:25:16 +08:00.
- Full backend suite: 511 passed with warnings treated as errors.
- Ruff, pip check, requirements sync, error contract audit, architecture profile, scale profile, and `git diff --check`: passed.
- Contract coverage includes conservative legacy normalization, confidence-only activation rejection, graph integrity, pure recall, lifecycle ownership, TTL/migration/index idempotency, confirmed-only canon context, explicit review promotion, and source/context-epoch memory-pack staleness.
- Git staging and commits remain owned by the project maintainer.

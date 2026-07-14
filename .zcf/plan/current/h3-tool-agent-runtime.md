# H3 Tool Result Artifact and Agent Runtime Contract

## Context

- Agentic tool execution currently returns untyped strings and uses logical source refs that do not resolve to persisted output.
- Gateway payload accounting folds recoverable tool messages, but it cannot prove that the original result remains recoverable.
- Turn cancellation is checked at provider egress, while tool execution and isolated workers do not share the same deadline/cancellation guard.
- Reaching `max_iterations` currently triggers one extra provider request and can be reported as a completed run.
- Provider, tool, worker, and agent terminal states use unrelated result shapes.

## Selected design

1. Define `ToolExecutionResult` and `AgentRunResult` as the typed runtime boundary while preserving the existing mapping-style response access used by callers.
2. Persist full tool output in a bounded, retention-controlled artifact store under the writable data directory; expose only an opaque artifact ref and a bounded preview in replay/telemetry.
3. Keep gateway payload accounting as the sole folding owner, and permit folding only when a valid, existing, hash-verifiable tool artifact ref is present.
4. Extend `TurnRuntime` with a monotonic deadline and cancellation signal; guard every provider/tool request and isolated worker result publication with the same runtime.
5. Return `incomplete` immediately at the iteration limit without an extra provider request, and map success, incomplete, failure, and cancellation explicitly.
6. Treat event callback failures as recorded degradations only; they must never replace the primary run result.

## Success criteria

- Every folded tool result contains a resolvable artifact ref; broken or expired refs are never folded.
- Full tool output has a hash-bound artifact with per-artifact size, total capacity, retention, and deterministic cleanup limits.
- A timed-out or cancelled tool cannot publish an artifact or replay result after the terminal signal.
- No provider or tool request starts after turn cancellation/deadline exhaustion.
- `max_iterations`, tool failure, provider failure, cancellation, worker mapping, and success have contract tests.
- OpenAI and Anthropic replay carry equivalent preview, status, recoverability, artifact ref, and output hash semantics.

## Review result

- Status: completed on 2026-07-13.
- Full backend suite: 501 passed with warnings treated as errors.
- Ruff, pip check, requirements sync, error contract audit, architecture profile, scale profile, and `git diff --check`: passed.
- Contract coverage includes artifact hash/retention/capacity, broken-ref folding, OpenAI/Anthropic symmetry, max-iteration incomplete, provider/tool failure, cancellation, deadline, callback degradation, and worker terminal mapping.
- Git staging and commits remain owned by the project maintainer.

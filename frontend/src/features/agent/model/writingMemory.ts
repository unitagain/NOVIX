export interface WritingMemoryTurn {
  chapter?: string | number | null;
  status?: Record<string, unknown> | null;
}

export interface WritingMemoryVisibilityInput {
  turn?: WritingMemoryTurn | null;
  isGenerating?: boolean;
  isStreaming?: boolean;
}

export function shouldShowWritingMemory({
  turn,
  isGenerating = false,
  isStreaming = false,
}: WritingMemoryVisibilityInput): boolean {
  return Boolean(turn?.chapter && !isGenerating && !isStreaming);
}

export function mergeWritingMemoryStatus(
  turnStatus?: Record<string, unknown> | null,
  persistedStatus?: Record<string, unknown> | null,
): Record<string, unknown> | null {
  if (!turnStatus && !persistedStatus) return null;

  const merged = { ...(turnStatus || {}), ...(persistedStatus || {}) };
  if (turnStatus?.turn_context) {
    merged.turn_context = turnStatus.turn_context;
  }
  return merged;
}

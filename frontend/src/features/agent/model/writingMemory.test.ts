import { describe, expect, it } from 'vitest';
import { mergeWritingMemoryStatus, shouldShowWritingMemory } from './writingMemory';

describe('writingMemory', () => {
  it('仅在最新轮次停止生成和流式输出后显示', () => {
    const turn = { chapter: 'V1C001', status: { exists: false } };

    expect(shouldShowWritingMemory({ turn, isGenerating: true })).toBe(false);
    expect(shouldShowWritingMemory({ turn, isStreaming: true })).toBe(false);
    expect(shouldShowWritingMemory({ turn })).toBe(true);
    expect(shouldShowWritingMemory({ turn: null })).toBe(false);
  });

  it('轮询持久化状态时保留本轮真实上下文', () => {
    const turnContext = { used: ['draft', 'canon'] };
    const merged = mergeWritingMemoryStatus(
      { exists: false, turn_context: turnContext },
      { exists: true, stale: false, evidence_stats: { total: 3 } },
    );

    expect(merged).toMatchObject({ exists: true, stale: false, turn_context: turnContext });
  });
});

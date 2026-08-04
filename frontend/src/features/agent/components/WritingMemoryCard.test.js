import { describe, expect, it } from 'vitest';
import { getWritingMemoryState } from './WritingMemoryCard';

describe('getWritingMemoryState', () => {
  it('区分加载、本轮上下文、缺失、过期和就绪状态', () => {
    expect(getWritingMemoryState(null, true)).toBe('loading');
    expect(getWritingMemoryState({ exists: false, turn_context: { used: ['draft'] } })).toBe('context_only');
    expect(getWritingMemoryState({ exists: false })).toBe('missing');
    expect(getWritingMemoryState({ exists: true, stale: true })).toBe('stale');
    expect(getWritingMemoryState({ exists: true, stale: false })).toBe('ready');
  });
});

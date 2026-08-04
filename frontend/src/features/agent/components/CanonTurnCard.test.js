import { describe, expect, it } from 'vitest';
import { canonTurnVisualState } from './CanonTurnCard';

describe('canonTurnVisualState', () => {
  it('映射事实收尾状态', () => {
    expect(canonTurnVisualState('saving')).toBe('syncing');
    expect(canonTurnVisualState('syncing')).toBe('syncing');
    expect(canonTurnVisualState('pending_acceptance')).toBe('pending');
    expect(canonTurnVisualState('applied')).toBe('applied');
    expect(canonTurnVisualState('failed')).toBe('failed');
    expect(canonTurnVisualState('skipped')).toBe('skipped');
  });
});

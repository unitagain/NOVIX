import { describe, expect, it } from 'vitest';
import { normalizeChatTurnResponse, terminalStateMessage } from './agentProtocol';

describe('agentProtocol', () => {
  it('normalizes backend-owned approval', () => {
    const view = normalizeChatTurnResponse({
      terminal_state: 'requires_approval',
      pending_action: { id: 'a1', token: 'once', operation: 'write_content', status: 'pending' },
      compatibility: { backend_authoritative: true },
    });
    expect(view.pendingAction).toMatchObject({ id: 'a1', token: 'once', operation: 'write_content' });
    expect(view.isBackendAuthoritative).toBe(true);
  });

  it('rejects unusable pending actions', () => {
    expect(normalizeChatTurnResponse({ pending_action: { id: 'a1' } }).pendingAction).toBeNull();
  });

  it('maps terminal states to concise copy', () => {
    expect(terminalStateMessage(normalizeChatTurnResponse({ terminal_state: 'incomplete', reason: 'iteration_limit' }))).toContain('iteration_limit');
  });
});

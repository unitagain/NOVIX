import { describe, expect, it } from 'vitest';
import { normalizeChatTurnResponse, shouldRecoverChangedTurn, terminalStateMessage } from './agentProtocol';

describe('agentProtocol', () => {
  it('maps terminal states to concise copy', () => {
    expect(terminalStateMessage(normalizeChatTurnResponse({ terminal_state: 'incomplete', reason: 'iteration_limit' }))).toContain('iteration_limit');
  });

  it('recovers changed turns when the websocket stream is missing or incomplete', () => {
    const response = { changed: true, content: '修改后的正文' };

    expect(shouldRecoverChangedTurn(response, false, false)).toBe(true);
    expect(shouldRecoverChangedTurn(response, true, true)).toBe(true);
    expect(shouldRecoverChangedTurn(response, true, false)).toBe(false);
    expect(shouldRecoverChangedTurn({ changed: true }, false, false)).toBe(false);
  });
});

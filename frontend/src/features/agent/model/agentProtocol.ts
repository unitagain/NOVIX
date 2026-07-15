export type AgentTerminalState =
  | 'completed'
  | 'requires_input'
  | 'requires_approval'
  | 'incomplete'
  | 'failed'
  | 'cancelled';

export interface PendingAgentAction {
  id: string;
  operation: string;
  status: string;
  token: string;
  decisionFingerprint?: string;
  expiresAt?: string;
}

export interface ChatTurnRequest {
  chapter?: string;
  message: string;
  has_selection?: boolean;
  has_draft?: boolean;
  target_word_count?: number;
  auto_execute_plan?: boolean;
  thinking?: boolean;
  fallback_approval_action_id?: string;
  fallback_approval_token?: string;
}

export interface ChatTurnResponse {
  success?: boolean;
  action?: string;
  message?: string;
  changed?: boolean;
  cancelled?: boolean;
  incomplete?: boolean;
  reason?: string;
  terminal_state?: AgentTerminalState;
  pending_action?: Record<string, unknown>;
  plan?: Record<string, unknown>;
  context_plan?: Record<string, unknown>;
  runtime?: Record<string, unknown>;
  fallback_decision?: Record<string, unknown>;
  fallback_execution?: Record<string, unknown>;
  compatibility?: Record<string, unknown>;
}

export interface AgentTurnView {
  terminalState: AgentTerminalState | null;
  pendingAction: PendingAgentAction | null;
  contextPlan: Record<string, unknown> | null;
  runtime: Record<string, unknown> | null;
  reason: string;
  isBackendAuthoritative: boolean;
}

const text = (value: unknown): string => (typeof value === 'string' ? value : '');

export function normalizeChatTurnResponse(data: ChatTurnResponse | null | undefined): AgentTurnView {
  const rawAction = data?.pending_action;
  const pendingAction = rawAction
    ? {
        id: text(rawAction.id),
        operation: text(rawAction.operation),
        status: text(rawAction.status) || 'pending',
        token: text(rawAction.token),
        decisionFingerprint: text(rawAction.decision_fingerprint) || undefined,
        expiresAt: text(rawAction.expires_at) || undefined,
      }
    : null;

  return {
    terminalState: data?.terminal_state || null,
    pendingAction: pendingAction?.id && pendingAction.token ? pendingAction : null,
    contextPlan: data?.context_plan || null,
    runtime: data?.runtime || null,
    reason: text(data?.reason),
    isBackendAuthoritative: data?.compatibility?.backend_authoritative === true,
  };
}

export function terminalStateMessage(view: AgentTurnView): string {
  switch (view.terminalState) {
    case 'requires_input':
      return '需要补充信息后才能继续。';
    case 'incomplete':
      return `本轮未完整完成${view.reason ? `：${view.reason}` : '。'}`;
    case 'failed':
      return `本轮执行失败${view.reason ? `：${view.reason}` : '。'}`;
    case 'cancelled':
      return '本轮已取消。';
    default:
      return '';
  }
}

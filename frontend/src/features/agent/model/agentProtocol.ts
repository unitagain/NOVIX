export type AgentTerminalState =
  | 'completed'
  | 'requires_input'
  | 'incomplete'
  | 'failed'
  | 'cancelled';

export interface ChatTurnRequest {
  chapter?: string;
  message: string;
  has_selection?: boolean;
  /** 当前选区的有限文本，随本轮 Writer 上下文注入，用于确定修改范围。 */
  selection_text?: string;
  has_draft?: boolean;
  target_word_count?: number;
  auto_execute_plan?: boolean;
  thinking?: boolean;
  reasoning_level?: 'auto' | 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';
}

export interface ChatTurnResponse {
  success?: boolean;
  action?: string;
  message?: string;
  summary?: string;
  changed?: boolean;
  partial?: boolean;
  content?: string;
  questions?: Array<{
    type?: string;
    key?: string;
    text?: string;
    reason?: string;
    impact?: string;
    impact_score?: number;
    options?: string[];
    default?: string;
  }>;
  clarification?: {
    decision?: 'ask' | 'proceed';
    reason?: string;
    question_count?: number;
    questions?: Array<Record<string, unknown>>;
    tool?: 'ask_clarification' | string;
  };
  /** Compatibility aliases; the active contract is the Writer tool payload above. */
  clarify_decision?: 'ask' | 'proceed';
  clarify_mode?: 'always' | 'auto' | 'off';
  cancelled?: boolean;
  incomplete?: boolean;
  reason?: string;
  terminal_state?: AgentTerminalState;
  plan?: Record<string, unknown>;
  context_plan?: Record<string, unknown>;
  runtime?: Record<string, unknown>;
  writing_memory?: Record<string, unknown>;
  turn_effect?: Record<string, unknown>;
  chapter_target?: {
    chapter?: string;
    title?: string;
    create?: boolean;
  };
  auto_commit?: {
    committed?: boolean;
    chapter?: string;
    title?: string;
    word_count?: number;
    reason?: string;
    canon_sync?: Record<string, unknown>;
  };
}

export function shouldRecoverChangedTurn(data: ChatTurnResponse, streamUsed: boolean, streamActive: boolean): boolean {
  return data.changed === true && typeof data.content === 'string' && (!streamUsed || streamActive);
}

export interface AgentTurnView {
  terminalState: AgentTerminalState | null;
  contextPlan: Record<string, unknown> | null;
  runtime: Record<string, unknown> | null;
  reason: string;
}

const text = (value: unknown): string => (typeof value === 'string' ? value : '');

export function normalizeChatTurnResponse(data: ChatTurnResponse | null | undefined): AgentTurnView {
  return {
    terminalState: data?.terminal_state || null,
    contextPlan: data?.context_plan || null,
    runtime: data?.runtime || null,
    reason: text(data?.reason),
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

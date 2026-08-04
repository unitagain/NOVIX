const normalizeText = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();

export const mergeStreamingText = (previous, incoming) => {
  const current = String(previous || '');
  const next = String(incoming || '');
  if (!next || current === next || current.endsWith(next)) return current;
  if (!current || next.startsWith(current)) return next;

  const maxOverlap = Math.min(current.length, next.length);
  for (let size = maxOverlap; size > 0; size -= 1) {
    if (current.slice(-size) === next.slice(0, size)) {
      return `${current}${next.slice(size)}`;
    }
  }
  return `${current}${next}`;
};

// 工具事件的身份由 toolCallId 唯一确定：同一工具连续多次调用（参数不同）不得因
// 文案相同而被折叠为一条（U5：hook 不再产出 message，签名必须落在结构化 id 上）。
const eventSignature = (event) =>
  [event?.stage, normalizeText(event?.message), normalizeText(event?.note), event?.toolName, event?.toolCallId]
    .filter(Boolean)
    .join(':');

/**
 * 将实时 Agent 过程事件追加到当前项目时间线。
 * 仅合并相邻的流式思考片段；工具调用等事件保持独立，以保留真实执行顺序。
 */
export function appendAgentProgressEvent(existing = [], partial = {}, createMeta = () => ({})) {
  const events = Array.isArray(existing) ? existing : [];
  const stage = String(partial?.stage || '');
  const last = events[events.length - 1];

  if (partial?.id && events.some((event) => event?.id === partial.id)) {
    return events;
  }

  if (stage === 'thinking' && last?.stage === 'thinking') {
    const incoming = String(partial.note ?? partial.message ?? '');
    const previous = String(last.note ?? last.message ?? '');
    const merged = mergeStreamingText(previous, incoming);
    if (merged === previous) return events;
    const now = Date.now();
    return [
      ...events.slice(0, -1),
      {
        ...last,
        ...partial,
        message: merged,
        note: merged,
        // 思考耗时由前端自算：首个片段的 startedAt 必须写在展开之后，
        // 否则会被 partial 覆盖成本次片段时间（U5 · plan.md §9.4 Step 4）。
        startedAt: last.startedAt || last.timestamp || now,
        endedAt: now,
      },
    ];
  }

  if (last && eventSignature(last) === eventSignature(partial)) {
    return events;
  }

  const now = Date.now();
  const event = {
    id: `${now}-${Math.random().toString(36).slice(2, 8)}`,
    timestamp: now,
    ...createMeta(),
    ...partial,
  };
  if (stage === 'thinking') {
    event.startedAt = event.startedAt || event.timestamp || now;
    event.endedAt = event.endedAt || event.startedAt;
  }
  return [...events.slice(-199), event];
}

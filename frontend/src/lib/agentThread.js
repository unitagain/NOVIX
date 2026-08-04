/**
 * agentThread —— chat 对话流的数据模型（part 模型唯一 owner）
 *
 * 纯函数两层：
 *   buildAgentThread  把扁平 messages + progressEvents 归约为「对话轮(turn)」列表；
 *   buildAgentParts   把单轮时间线归一为稳定的 typed parts，供渲染层分层展示。
 *
 * part 类型（5 类，见 plan.md §9.3；`tool-group` 是渲染层编组结果，不是 part 类型）：
 *   reasoning   思考块，保留时序位置，不聚合到轮顶
 *   tool        一次工具调用，含状态/耗时/结构化参数/结果预览
 *   narration   工具循环中的旁白（assistant_text）
 *   answer      本轮最终答复（HTTP 返回的 assistant message）
 *   meta        system / error 小行
 *
 * 无 React、无副作用 → 可单元测试。
 */
const normalizeVisibleText = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();

/**
 * 去除同一轮内由 HTTP/WS 双通道造成的重复事件。
 *
 * 去重键为「event id」与「stage + toolCallId」复合键，不对措辞相近的文本做模糊比对
 * （旁白与最终答复措辞相近时会误删，且无法解释删了什么 —— U5 · plan.md §9.2.6）。
 * 唯一保留的文本兜底在 buildAgentParts：narration 与 answer 完全相等时丢弃 narration。
 * 原始 messages/progressEvents 仍完整保留，只压缩最终展示时间线。
 */
export function compactAgentTimeline(timeline = []) {
  const seenEventIds = new Set();
  const seenToolKeys = new Set();
  const seenStageText = new Set();

  return (timeline || []).filter((item) => {
    if (item?.kind !== 'progress') return true;
    const event = item.event || {};
    if (event.stage === 'connection') return false;
    if (event.id && seenEventIds.has(event.id)) return false;
    if (event.id) seenEventIds.add(event.id);

    // 工具事件：同一 (stage, toolCallId) 只保留一条；无 id 的老事件退化为 stage+toolName。
    if (event.stage === 'tool_call' || event.stage === 'tool_result') {
      const toolKey = `${event.stage}:${event.toolCallId || ''}:${event.toolName || ''}`;
      if (seenToolKeys.has(toolKey)) return false;
      seenToolKeys.add(toolKey);
      return true;
    }

    // 非工具状态事件：同 stage 同文案的重复推送（HTTP/WS 双通道）压缩为一条。
    const stageKey = `${event.stage || ''}:${normalizeVisibleText(event.message)}:${normalizeVisibleText(event.note)}`;
    if (seenStageText.has(stageKey)) return false;
    seenStageText.add(stageKey);
    return true;
  });
}

const mergeProcessBlocks = (values = []) => {
  const blocks = [];
  values.forEach((value) => {
    const text = String(value || '').trim();
    if (!text || blocks.includes(text)) return;
    const last = blocks[blocks.length - 1] || '';
    if (last && text.startsWith(last)) {
      blocks[blocks.length - 1] = text;
      return;
    }
    if (last && last.startsWith(text)) return;
    blocks.push(text);
  });
  return blocks.join('\n\n');
};

const reasoningPart = (item) => {
  const event = item.event || {};
  const text = String(event.note || event.message || '');
  const startedAt = event.startedAt || event.timestamp || 0;
  const endedAt = event.endedAt || startedAt;
  return {
    kind: 'reasoning',
    id: item.id || `part-reasoning-${startedAt}`,
    text,
    startedAt,
    endedAt,
    streaming: false,
  };
};

/**
 * 把 tool_call / tool_result 事件对折叠为单个 tool part。
 * 只有 tool_result 的（重连丢了 call）或只有 tool_call 的（仍在执行）都要能渲染。
 */
const toolPartsFrom = (items) => {
  const order = [];
  const byKey = new Map();
  items.forEach((item) => {
    const event = item.event || {};
    const key = event.toolCallId || `${event.toolName || ''}:${item.id}`;
    if (!byKey.has(key)) {
      const part = {
        kind: 'tool',
        id: `part-tool-${key}`,
        name: event.toolName || '',
        args: null,
        status: 'running',
        elapsedMs: 0,
        preview: '',
        errorCode: null,
        recoverable: false,
      };
      byKey.set(key, part);
      order.push(part);
    }
    const part = byKey.get(key);
    if (event.toolName) part.name = event.toolName;
    if (event.stage === 'tool_call') {
      part.args = event.toolArgs && typeof event.toolArgs === 'object' ? event.toolArgs : part.args;
    } else if (event.stage === 'tool_result') {
      part.status = event.toolStatus || 'succeeded';
      part.elapsedMs = Number(event.toolElapsedMs) || 0;
      part.preview = typeof event.toolPreview === 'string' ? event.toolPreview : '';
      part.errorCode = event.toolErrorCode || null;
      part.recoverable = Boolean(event.toolRecoverable);
    }
  });
  return order;
};

/**
 * 把单轮扁平时间线归一为 typed parts。
 *
 * 时序即语义：thinking 就地产出 reasoning part（连续片段已在 agentProgress 合并层
 * 归并，被工具打断即自然分块），不再聚合到轮顶——轮顶聚合会抹平真实执行顺序。
 */
export function buildAgentParts(timeline = []) {
  const compacted = compactAgentTimeline(timeline);
  const answerTexts = new Set(
    compacted
      .filter((item) => item?.kind === 'message' && item.type === 'assistant')
      .map((item) => normalizeVisibleText(item.content)),
  );

  const parts = [];
  let narrationBuf = [];
  let toolBuf = [];

  const flushNarration = () => {
    if (!narrationBuf.length) return;
    // 唯一保留的文本兜底：与最终答复完全相同的旁白是 HTTP/WS 的真实重复。
    const merged = mergeProcessBlocks(
      narrationBuf.filter((text) => !answerTexts.has(normalizeVisibleText(text))),
    );
    if (merged.trim()) {
      parts.push({ kind: 'narration', id: `part-narration-${parts.length}`, text: merged });
    }
    narrationBuf = [];
  };
  const flushTools = () => {
    if (!toolBuf.length) return;
    parts.push(...toolPartsFrom(toolBuf));
    toolBuf = [];
  };
  const flushAll = () => {
    flushNarration();
    flushTools();
  };

  compacted.forEach((item) => {
    if (item?.kind === 'progress') {
      const stage = item.event?.stage;
      if (stage === 'thinking') {
        flushAll();
        parts.push(reasoningPart(item));
        return;
      }
      if (stage === 'tool_call' || stage === 'tool_result') {
        flushNarration();
        toolBuf.push(item);
        return;
      }
      if (stage === 'assistant_text') {
        flushTools();
        narrationBuf.push(String(item.event?.message || ''));
        return;
      }
      // 旧式 stage 事件（read_previous / memory_pack / plan_step 等）→ meta 小行。
      flushAll();
      parts.push({
        kind: 'meta',
        id: item.id || `part-meta-${parts.length}`,
        type: stage || 'system',
        text: String(item.event?.message || ''),
        event: item.event,
      });
      return;
    }
    flushAll();
    if (item?.type === 'assistant') {
      parts.push({ kind: 'answer', id: item.id || `part-answer-${parts.length}`, text: String(item.content || '') });
      return;
    }
    parts.push({
      kind: 'meta',
      id: item?.id || `part-meta-${parts.length}`,
      type: item?.type || 'system',
      text: String(item?.content || ''),
    });
  });
  flushAll();

  return parts;
}

export function buildAgentThread(messages = [], progressEvents = []) {
  const combined = [];
  (messages || []).forEach((msg, index) => {
    combined.push({ kind: 'message', id: `msg-${index}`, ts: msg?.time?.getTime?.() || 0, msg });
  });
  (progressEvents || []).forEach((event) => {
    combined.push({ kind: 'progress', id: event?.id, ts: event?.timestamp || 0, event });
  });
  combined.sort((a, b) => a.ts - b.ts);

  const result = [];
  let current = null;
  let runSeq = 0;

  const ensureRun = (startedAt = 0) => {
    if (current) return current;
    current = { id: `run-${runSeq++}`, startedAt, userContent: '', messages: [], progressEvents: [], timeline: [] };
    return current;
  };

  const finishCurrentRun = () => {
    if (!current) return;
    current.timeline = compactAgentTimeline(current.timeline);
    result.push(current);
    current = null;
  };

  combined.forEach((item) => {
    if (item.kind === 'message' && item.msg?.type === 'user') {
      finishCurrentRun();
      current = {
        id: `run-${runSeq++}`,
        startedAt: item.ts,
        userContent: String(item.msg.content || '').trim(),
        messages: [],
        progressEvents: [],
        timeline: [],
      };
      return;
    }

    const run = ensureRun(item.ts);
    if (item.kind === 'message') {
      const entry = { id: item.id, type: item.msg.type, content: item.msg.content, time: item.msg.time };
      run.messages.push(entry);
      run.timeline.push({ kind: 'message', id: item.id, ts: item.ts, ...entry });
    } else if (item.kind === 'progress') {
      run.progressEvents.push(item.event);
      run.timeline.push({ kind: 'progress', id: item.id, ts: item.ts, event: item.event });
    }
  });

  finishCurrentRun();
  return result;
}

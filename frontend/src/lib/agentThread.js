/**
 * agentThread —— chat 对话脊柱的数据模型（Phase F · slice 1）
 *
 * 纯函数：把扁平的 messages + progressEvents 归约成"对话轮(turn)"列表。
 * 规则：每条 user 消息开启新的一轮；其后的助手/系统消息与过程事件（思考/工具/检索）
 * 归入当前轮；user 之前到达的过程事件归入一个匿名首轮。
 *
 * 每一轮额外产出 `timeline`：messages 与 progressEvents 按到达顺序交织的有序列表，
 * 供 Trae 式「过程内联到 Agent 流」的渲染使用（思考/工具步骤穿插在助手正文之间）。
 *
 * 无 React、无副作用 → 可单元测试，是新脊柱(useAgentSession/useAgentStream)的基石。
 *
 * @param {Array<{type,content,time?}>} messages
 * @param {Array<{id,timestamp?,stage?,message?,note?}>} progressEvents
 * @returns {Array<{id,startedAt,userContent,messages,progressEvents,timeline}>}
 */
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

  combined.forEach((item) => {
    if (item.kind === 'message' && item.msg?.type === 'user') {
      if (current) result.push(current);
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

  if (current) result.push(current);
  return result;
}

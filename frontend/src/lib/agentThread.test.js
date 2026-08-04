import { describe, it, expect } from 'vitest';
import { buildAgentParts, buildAgentThread, compactAgentTimeline } from './agentThread';

// Phase F · slice 1：对话脊柱数据模型的回归网。
const userMsg = (content, ms = 0) => ({ type: 'user', content, time: { getTime: () => ms } });
const asstMsg = (content, ms = 0) => ({ type: 'assistant', content, time: { getTime: () => ms } });
const ev = (id, ts, stage = 'thinking', message = '') => ({ id, timestamp: ts, stage, message });

describe('agentThread.buildAgentThread', () => {
  it('空输入 → 空线程', () => {
    expect(buildAgentThread([], [])).toEqual([]);
    expect(buildAgentThread()).toEqual([]);
  });

  it('单条用户消息 → 一轮，含 userContent', () => {
    const thread = buildAgentThread([userMsg('写第一章', 1)], []);
    expect(thread).toHaveLength(1);
    expect(thread[0].userContent).toBe('写第一章');
    expect(thread[0].messages).toEqual([]);
    expect(thread[0].progressEvents).toEqual([]);
  });

  it('用户消息后的助手消息与过程事件归入同一轮', () => {
    const thread = buildAgentThread(
      [userMsg('写', 1), asstMsg('已生成草稿', 5)],
      [ev('e1', 2, 'thinking', '先查设定'), ev('e2', 3, 'tool_call', 'lookup_card')],
    );
    expect(thread).toHaveLength(1);
    expect(thread[0].userContent).toBe('写');
    expect(thread[0].messages.map((m) => m.content)).toEqual(['已生成草稿']);
    expect(thread[0].progressEvents.map((e) => e.id)).toEqual(['e1', 'e2']);
  });

  it('按时间戳排序混合事件', () => {
    const thread = buildAgentThread([userMsg('a', 10), asstMsg('done', 30)], [ev('mid', 20, 'writing', '撰写中')]);
    expect(thread[0].progressEvents).toHaveLength(1);
    expect(thread[0].messages).toHaveLength(1);
  });

  it('多轮：每条用户消息开启新一轮', () => {
    const thread = buildAgentThread(
      [userMsg('第一轮', 1), asstMsg('回应1', 2), userMsg('第二轮', 3), asstMsg('回应2', 4)],
      [],
    );
    expect(thread).toHaveLength(2);
    expect(thread[0].userContent).toBe('第一轮');
    expect(thread[1].userContent).toBe('第二轮');
    expect(thread[1].messages.map((m) => m.content)).toEqual(['回应2']);
  });

  it('用户消息之前到达的过程事件 → 匿名首轮(userContent 空)', () => {
    const thread = buildAgentThread([], [ev('pre', 1, 'thinking', '准备上下文')]);
    expect(thread).toHaveLength(1);
    expect(thread[0].userContent).toBe('');
    expect(thread[0].progressEvents.map((e) => e.id)).toEqual(['pre']);
  });

  it('timeline 按到达顺序交织 message 与 progress', () => {
    const thread = buildAgentThread(
      [userMsg('写', 1), asstMsg('草稿', 4)],
      [ev('t1', 2, 'thinking', '想'), ev('t2', 3, 'tool_call', '查卡')],
    );
    expect(thread[0].timeline.map((x) => x.kind)).toEqual(['progress', 'progress', 'message']);
    expect(thread[0].timeline.map((x) => x.id)).toEqual(['t1', 't2', 'msg-1']);
    expect(thread[0].timeline[2].content).toBe('草稿');
  });

  it('HTTP 最终答复与 WS 过程旁白内容相同时只展示一次', () => {
    const thread = buildAgentThread(
      [userMsg('检查问题', 1), asstMsg('我来检查并修复。', 3)],
      [ev('narration', 2, 'assistant_text', '我来检查并修复。')],
    );

    // timeline 保留原始事件（可追溯）；重复的兜底剔除发生在 part 层（U5 · §9.2.6）。
    expect(thread[0].timeline).toHaveLength(2);
    const parts = buildAgentParts(thread[0].timeline);
    expect(parts.map((part) => part.kind)).toEqual(['answer']);
    expect(parts[0].text).toBe('我来检查并修复。');
  });

  it('过滤重复状态事件但保留不同阶段', () => {
    const timeline = compactAgentTimeline([
      { kind: 'progress', id: 'a', event: ev('a', 1, 'read_previous', '读取第一章') },
      { kind: 'progress', id: 'b', event: ev('b', 2, 'read_previous', '读取第一章') },
      { kind: 'progress', id: 'c', event: ev('c', 3, 'thinking', '分析内容') },
    ]);

    expect(timeline.map((item) => item.id)).toEqual(['a', 'c']);
  });
});

// U5 · part 模型（plan.md §9.3）：5 类 part、思考保留时序、工具状态透传。
const toolCall = (id, toolCallId, name, args) => ({
  kind: 'progress',
  id,
  event: { stage: 'tool_call', toolCallId, toolName: name, toolArgs: args },
});
const toolResult = (id, toolCallId, name, extra = {}) => ({
  kind: 'progress',
  id,
  event: {
    stage: 'tool_result',
    toolCallId,
    toolName: name,
    toolStatus: 'succeeded',
    toolElapsedMs: 120,
    toolPreview: '结果',
    toolErrorCode: null,
    toolRecoverable: false,
    ...extra,
  },
});

describe('agentThread.buildAgentParts', () => {
  it('思考保留时序位置，不聚合到轮顶', () => {
    const parts = buildAgentParts([
      { kind: 'progress', id: 'th1', event: { stage: 'thinking', message: '想一下', startedAt: 1, endedAt: 3 } },
      toolCall('t1', 'c1', 'query_canon', { query: '林清越' }),
      toolResult('t1r', 'c1', 'query_canon'),
      { kind: 'progress', id: 'th2', event: { stage: 'thinking', message: '再想想', startedAt: 5, endedAt: 9 } },
    ]);

    // 两个独立 reasoning，且第二个在 tool 之后 —— 轮顶聚合会抹平这一顺序。
    expect(parts.map((part) => part.kind)).toEqual(['reasoning', 'tool', 'reasoning']);
    expect(parts[0].text).toBe('想一下');
    expect(parts[2].text).toBe('再想想');
  });

  it('reasoning 携带起止时间供渲染层计算思考耗时', () => {
    const [part] = buildAgentParts([
      { kind: 'progress', id: 'th1', event: { stage: 'thinking', note: '推理', startedAt: 100, endedAt: 2600 } },
    ]);

    expect(part.kind).toBe('reasoning');
    expect(part.endedAt - part.startedAt).toBe(2500);
  });

  it('tool_call 与 tool_result 折叠为单个 tool part，含参数与状态', () => {
    const parts = buildAgentParts([
      toolCall('t1', 'c1', 'query_canon', { query: '林清越' }),
      toolResult('t1r', 'c1', 'query_canon', { toolElapsedMs: 412, toolRecoverable: true }),
    ]);

    expect(parts).toHaveLength(1);
    expect(parts[0]).toMatchObject({
      kind: 'tool',
      name: 'query_canon',
      args: { query: '林清越' },
      status: 'succeeded',
      elapsedMs: 412,
      preview: '结果',
      recoverable: true,
    });
  });

  it('只有 tool_call（仍在执行）时状态为 running', () => {
    const [part] = buildAgentParts([toolCall('t1', 'c1', 'search_prose', { query: '雨夜' })]);
    expect(part.status).toBe('running');
    expect(part.elapsedMs).toBe(0);
  });

  it('失败工具透传 status 与 error_code', () => {
    const [part] = buildAgentParts([
      toolCall('t1', 'c1', 'read_chapter', { chapter_id: 'V1C999' }),
      toolResult('t1r', 'c1', 'read_chapter', { toolStatus: 'failed', toolErrorCode: 'chapter_not_found' }),
    ]);

    expect(part.status).toBe('failed');
    expect(part.errorCode).toBe('chapter_not_found');
  });

  it('同名工具的多次调用产出多个独立 tool part', () => {
    const parts = buildAgentParts([
      toolCall('t1', 'c1', 'query_canon', { query: 'A' }),
      toolResult('t1r', 'c1', 'query_canon'),
      toolCall('t2', 'c2', 'query_canon', { query: 'B' }),
      toolResult('t2r', 'c2', 'query_canon'),
    ]);

    expect(parts.filter((part) => part.kind === 'tool')).toHaveLength(2);
    expect(parts.map((part) => part.args.query)).toEqual(['A', 'B']);
  });

  it('assistant_text → narration，HTTP 最终答复 → answer', () => {
    const parts = buildAgentParts([
      { kind: 'progress', id: 'p1', event: { stage: 'assistant_text', message: '我先检查现有章节。' } },
      { kind: 'message', id: 'final', type: 'assistant', content: '已经修改完成。' },
    ]);

    expect(parts.map((part) => part.kind)).toEqual(['narration', 'answer']);
    expect(parts[0].text).toBe('我先检查现有章节。');
    expect(parts[1].text).toBe('已经修改完成。');
  });

  it('与最终答复完全相同的旁白被丢弃（HTTP/WS 双通道重复）', () => {
    const parts = buildAgentParts([
      { kind: 'progress', id: 'p1', event: { stage: 'assistant_text', message: '我先检查现有章节。' } },
      { kind: 'progress', id: 'p2', event: { stage: 'assistant_text', message: '已经修改完成。' } },
      { kind: 'message', id: 'final', type: 'assistant', content: '已经修改完成。' },
    ]);

    expect(parts.filter((part) => part.kind === 'narration')).toHaveLength(1);
    expect(parts.find((part) => part.kind === 'narration').text).toBe('我先检查现有章节。');
  });

  it('措辞相近但不相同的旁白不做模糊去重', () => {
    const parts = buildAgentParts([
      { kind: 'progress', id: 'p1', event: { stage: 'assistant_text', message: '我来检查并修复这个问题。' } },
      { kind: 'message', id: 'final', type: 'assistant', content: '我来检查并修复这些问题。' },
    ]);

    expect(parts.map((part) => part.kind)).toEqual(['narration', 'answer']);
  });

  it('旁白与工具按时序交织，互相打断', () => {
    const parts = buildAgentParts([
      { kind: 'progress', id: 'p1', event: { stage: 'assistant_text', message: '先看看设定。' } },
      toolCall('t1', 'c1', 'lookup_card', { name: '林清越' }),
      toolResult('t1r', 'c1', 'lookup_card'),
      { kind: 'progress', id: 'p2', event: { stage: 'assistant_text', message: '现在动笔。' } },
      toolCall('t2', 'c2', 'write_content', { mode: 'replace', content_chars: 1240 }),
    ]);

    expect(parts.map((part) => part.kind)).toEqual(['narration', 'tool', 'narration', 'tool']);
    expect(parts[3].args).toEqual({ mode: 'replace', content_chars: 1240 });
  });

  it('系统/错误消息与旧式 stage 事件 → meta', () => {
    const parts = buildAgentParts([
      { kind: 'progress', id: 's1', event: { stage: 'memory_pack', message: '构建工作记忆' } },
      { kind: 'message', id: 'e1', type: 'error', content: '生成失败' },
    ]);

    expect(parts.map((part) => part.kind)).toEqual(['meta', 'meta']);
    expect(parts[1].type).toBe('error');
  });

  it('不再产出 tool-group / process / reasoning 聚合块等旧 part 类型', () => {
    const parts = buildAgentParts([
      { kind: 'progress', id: 'th1', event: { stage: 'thinking', message: '想' } },
      toolCall('t1', 'c1', 'query_canon', { query: 'A' }),
      { kind: 'progress', id: 'p1', event: { stage: 'assistant_text', message: '旁白' } },
    ]);

    const kinds = new Set(parts.map((part) => part.kind));
    expect(kinds.has('tool-group')).toBe(false);
    expect(kinds.has('process')).toBe(false);
    expect([...kinds].every((kind) => ['reasoning', 'tool', 'narration', 'answer', 'meta'].includes(kind))).toBe(true);
  });
});

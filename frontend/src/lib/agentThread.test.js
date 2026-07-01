import { describe, it, expect } from 'vitest';
import { buildAgentThread } from './agentThread';

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
    const thread = buildAgentThread([], [ev('pre', 1, 'scene_brief', '场景简报')]);
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
});

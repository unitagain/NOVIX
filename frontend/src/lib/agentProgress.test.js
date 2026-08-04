import { describe, expect, it } from 'vitest';
import { appendAgentProgressEvent } from './agentProgress';

const meta = (id, timestamp) => () => ({ id, timestamp });

describe('agentProgress.appendAgentProgressEvent', () => {
  it('合并相邻的流式思考片段并保留完整文本', () => {
    const first = appendAgentProgressEvent(
      [],
      { stage: 'thinking', message: '先读取', note: '先读取' },
      meta('thinking-1', 1),
    );
    const second = appendAgentProgressEvent(
      first,
      { stage: 'thinking', message: '章节内容', note: '章节内容' },
      meta('thinking-2', 2),
    );

    expect(second).toHaveLength(1);
    expect(second[0].message).toBe('先读取章节内容');
    expect(second[0].note).toBe('先读取章节内容');
  });

  it('工具调用打断后创建新的独立思考块', () => {
    const events = [
      { id: 'thinking-1', timestamp: 1, stage: 'thinking', message: '第一段', note: '第一段' },
      { id: 'tool-1', timestamp: 2, stage: 'tool_call', message: '读取章节' },
    ];
    const next = appendAgentProgressEvent(
      events,
      { stage: 'thinking', message: '第二段', note: '第二段' },
      meta('thinking-2', 3),
    );

    expect(next).toHaveLength(3);
    expect(next[2].note).toBe('第二段');
  });

  it('忽略重复事件和重复的累计思考内容', () => {
    const events = [{ id: 'thinking-1', timestamp: 1, stage: 'thinking', message: '完整思考', note: '完整思考' }];

    expect(
      appendAgentProgressEvent(
        events,
        { stage: 'thinking', message: '完整思考', note: '完整思考' },
        meta('thinking-2', 2),
      ),
    ).toBe(events);
    expect(appendAgentProgressEvent(events, { id: 'thinking-1', stage: 'thinking' }, meta('ignored', 2))).toBe(
      events,
    );
  });

  it('思考块记录 startedAt/endedAt，合并时保留首次开始时间', () => {
    const first = appendAgentProgressEvent([], { stage: 'thinking', note: '先读取' }, meta('thinking-1', 1));
    expect(first[0].startedAt).toBe(1);
    expect(first[0].endedAt).toBe(1);

    const second = appendAgentProgressEvent(first, { stage: 'thinking', note: '章节内容' }, meta('thinking-2', 2));
    // startedAt 必须写在 ...partial 展开之后，否则会被本次片段时间覆盖。
    expect(second[0].startedAt).toBe(1);
    expect(second[0].endedAt).toBeGreaterThanOrEqual(second[0].startedAt);
  });

  it('同名工具的连续调用按 toolCallId 各自独立，不被折叠', () => {
    const first = appendAgentProgressEvent(
      [],
      { stage: 'tool_call', toolName: 'query_canon', toolCallId: 'c1', toolArgs: { query: '林清越' } },
      meta('tool-1', 1),
    );
    const second = appendAgentProgressEvent(
      first,
      { stage: 'tool_call', toolName: 'query_canon', toolCallId: 'c2', toolArgs: { query: '林清河' } },
      meta('tool-2', 2),
    );

    expect(second).toHaveLength(2);
    expect(second.map((event) => event.toolCallId)).toEqual(['c1', 'c2']);
  });
});


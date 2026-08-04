/**
 * U5 · 相邻 tool part 的渲染层编组规则。
 *
 * 编组是渲染结果而非 part 类型（plan.md §9.3）：单次调用不编组，
 * 非工具 part 打断编组并保持时序。
 */
import { describe, expect, it } from 'vitest';
import { groupToolParts } from './AgentTranscript';

const tool = (id) => ({ kind: 'tool', id, name: 'query_canon', status: 'succeeded' });
const narration = (id) => ({ kind: 'narration', id, text: '旁白' });

describe('AgentTranscript.groupToolParts', () => {
  it('单次工具调用不编组', () => {
    expect(groupToolParts([tool('a')]).map((part) => part.kind)).toEqual(['tool']);
  });

  it('连续多次调用编为一组', () => {
    const grouped = groupToolParts([tool('a'), tool('b'), tool('c')]);
    expect(grouped).toHaveLength(1);
    expect(grouped[0].kind).toBe('tool-group');
    expect(grouped[0].parts.map((part) => part.id)).toEqual(['a', 'b', 'c']);
  });

  it('非工具 part 打断编组并保持时序', () => {
    const grouped = groupToolParts([tool('a'), tool('b'), narration('n1'), tool('c'), tool('d')]);
    expect(grouped.map((part) => part.kind)).toEqual(['tool-group', 'narration', 'tool-group']);
  });

  it('思考打断后的单次调用不被并入前一组', () => {
    const grouped = groupToolParts([
      tool('a'),
      { kind: 'reasoning', id: 'r1', text: '想', startedAt: 0, endedAt: 0 },
      tool('b'),
    ]);
    expect(grouped.map((part) => part.kind)).toEqual(['tool', 'reasoning', 'tool']);
  });

  it('空输入返回空列表', () => {
    expect(groupToolParts([])).toEqual([]);
    expect(groupToolParts()).toEqual([]);
  });
});

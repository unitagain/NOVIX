/**
 * U5 · 对话流渲染决策回归网。
 *
 * 本仓无 jsdom / testing-library（既有组件测试均测导出的纯函数），因此这里覆盖
 * 展示决策本身：思考耗时文案、工具参数取值、失败默认展开、编组阈值与摘要。
 * DOM 交互不在此断言，由 §9.7 人工金路径覆盖。
 */
import { describe, expect, it } from 'vitest';
import { durationLabel } from './ReasoningPart';
import { defaultToolOpen } from './ToolPart';
import { defaultGroupOpen, groupSummary, TOOL_GROUP_COLLAPSE_THRESHOLD } from './ToolGroup';
import { toolArgEntries, toolArgSummary, toolDisplayName, toolStatusLabel } from './toolLabels';

// 直通式 t：返回 key 本身表示「未命中」，与 useLocale 的兜底约定一致。
const LOCALE = {
  'agentPanel.thinking': '思考中…',
  'agentPanel.thinkingDone': '思考完成',
  'agentPanel.thinkingSeconds': '思考 {n} 秒',
  'agentPanel.toolArgChars': '{n} 字',
  'agentPanel.toolArgQuestions': '{n} 个问题',
  'agentPanel.toolNames.query_canon': '查事实',
  'agentPanel.toolStatus.failed': '失败',
  'agentPanel.toolGroupAllOk': '{n} 次调用 · 全部成功',
  'agentPanel.toolGroupWithFailures': '{n} 次调用 · {failed} 失败',
  'agentPanel.toolGroupSummary': '{n} 次调用',
};
const t = (key) => LOCALE[key] ?? key;

const tool = (status, id = 'a') => ({ kind: 'tool', id, name: 'query_canon', status });

describe('ReasoningPart.durationLabel', () => {
  it('流式中显示进行态', () => {
    expect(durationLabel({ streaming: true, startedAt: 0, endedAt: 5000 }, t)).toBe('思考中…');
  });

  it('完成后显示秒数', () => {
    expect(durationLabel({ streaming: false, startedAt: 1000, endedAt: 3600 }, t)).toBe('思考 3 秒');
  });

  it('不足 1 秒不显示 0 秒', () => {
    expect(durationLabel({ streaming: false, startedAt: 1000, endedAt: 1200 }, t)).toBe('思考完成');
  });
});

describe('ToolPart 展示决策', () => {
  it('失败与超时默认展开，成功与取消默认折叠', () => {
    expect(defaultToolOpen(tool('failed'))).toBe(true);
    expect(defaultToolOpen(tool('timed_out'))).toBe(true);
    expect(defaultToolOpen(tool('succeeded'))).toBe(false);
    expect(defaultToolOpen(tool('cancelled'))).toBe(false);
    expect(defaultToolOpen(tool('running'))).toBe(false);
  });

  it('工具名走 i18n，未命中回落原始名', () => {
    expect(toolDisplayName('query_canon', t)).toBe('查事实');
    expect(toolDisplayName('unknown_tool', t)).toBe('unknown_tool');
  });

  it('状态文案未命中时回落状态值本身', () => {
    expect(toolStatusLabel('failed', t)).toBe('失败');
    expect(toolStatusLabel('succeeded', t)).toBe('succeeded');
  });
});

describe('toolLabels.toolArgSummary', () => {
  it('检索类取查询关键词', () => {
    expect(toolArgSummary('query_canon', { query: '林清越的身世' }, t)).toBe('林清越的身世');
    expect(toolArgSummary('lookup_card', { name: '林清河' }, t)).toBe('林清河');
  });

  it('撰写类取字数（后端已脱敏为长度，绝不显示正文）', () => {
    expect(toolArgSummary('write_content', { mode: 'replace', content_chars: 1240 }, t)).toBe('replace · 1,240 字');
    expect(toolArgSummary('edit_lines', { new_text_chars: 88 }, t)).toBe('88 字');
  });

  it('反问取题数', () => {
    expect(toolArgSummary('ask_clarification', { question_count: 2 }, t)).toBe('2 个问题');
  });

  it('新建章节取章节号与标题', () => {
    expect(toolArgSummary('create_chapter', { chapter_id: 'V1C002', title: '雨夜' }, t)).toBe('V1C002 · 雨夜');
  });

  it('取不到关键参数时返回空串，不臆造', () => {
    expect(toolArgSummary('finish_turn', {}, t)).toBe('');
    expect(toolArgSummary('query_canon', null, t)).toBe('');
  });

  it('过长参数截断', () => {
    expect(toolArgSummary('query_canon', { query: '越'.repeat(80) }, t)).toHaveLength(41);
  });
});

describe('toolLabels.toolArgEntries', () => {
  it('产出结构化键值对，剔除空值与内部键', () => {
    expect(toolArgEntries({ query: '林清越', limit: 5, empty: '', missing: null, _summary: 'x' })).toEqual([
      ['query', '林清越'],
      ['limit', '5'],
    ]);
  });

  it('非对象参数返回空列表', () => {
    expect(toolArgEntries(null)).toEqual([]);
    expect(toolArgEntries('raw')).toEqual([]);
  });
});

describe('ToolGroup 编组规则', () => {
  it('少量调用默认展开', () => {
    expect(defaultGroupOpen([tool('succeeded', 'a'), tool('succeeded', 'b')])).toBe(true);
  });

  it('≥6 次且全部成功才默认折叠', () => {
    const parts = Array.from({ length: TOOL_GROUP_COLLAPSE_THRESHOLD }, (_, i) => tool('succeeded', `s${i}`));
    expect(defaultGroupOpen(parts)).toBe(false);
  });

  it('组内有失败时即使数量多也默认展开', () => {
    const parts = Array.from({ length: TOOL_GROUP_COLLAPSE_THRESHOLD }, (_, i) => tool('succeeded', `s${i}`));
    parts[0] = tool('failed', 'bad');
    expect(defaultGroupOpen(parts)).toBe(true);
  });

  it('组头为语义摘要而非次数图标', () => {
    expect(groupSummary([tool('succeeded', 'a'), tool('succeeded', 'b')], t)).toBe('2 次调用 · 全部成功');
    expect(groupSummary([tool('succeeded', 'a'), tool('failed', 'b')], t)).toBe('2 次调用 · 1 失败');
    expect(groupSummary([tool('succeeded', 'a'), tool('running', 'b')], t)).toBe('2 次调用');
  });
});

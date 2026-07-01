import { describe, it, expect } from 'vitest';
import {
  countWords,
  countChars,
  escapeRegExp,
  getSelectionStats,
  normalizeStars,
  parseListInput,
  formatListInput,
  formatRulesInput,
  hasDeletionIntent,
} from './writingSessionHelpers';

// 前端自检起步：纯逻辑回归网（对标后端纯函数测试）。
describe('writingSessionHelpers · 纯逻辑', () => {
  it('countWords：中文去空白计字数，英文按词', () => {
    expect(countWords('你好 世界')).toBe(4);
    expect(countWords('hello world', 'en')).toBe(2);
    expect(countWords('')).toBe(0);
    expect(countWords('  \n ')).toBe(0);
  });

  it('countChars = 中文字数', () => {
    expect(countChars('a b c')).toBe(3);
  });

  it('escapeRegExp 转义正则特殊字符', () => {
    expect(escapeRegExp('a.b*c')).toBe('a\\.b\\*c');
    expect(escapeRegExp('')).toBe('');
  });

  it('normalizeStars 夹取到 1-3', () => {
    expect(normalizeStars('2')).toBe(2);
    expect(normalizeStars('9')).toBe(3);
    expect(normalizeStars('0')).toBe(1);
    expect(normalizeStars('x')).toBe(1);
  });

  it('parseListInput 多分隔符拆分去空', () => {
    expect(parseListInput('a, b；c\nd;;')).toEqual(['a', 'b', 'c', 'd']);
    expect(parseListInput('')).toEqual([]);
  });

  it('formatListInput / formatRulesInput', () => {
    expect(formatListInput(['a', '', 'b'])).toBe('a，b');
    expect(formatListInput('x')).toBe('x');
    expect(formatRulesInput(['r1', 'r2'])).toBe('r1\nr2');
  });

  it('hasDeletionIntent 识别删除意图', () => {
    expect(hasDeletionIntent('把这段删掉')).toBe(true);
    expect(hasDeletionIntent('精简一下')).toBe(true);
    expect(hasDeletionIntent('继续往下写')).toBe(false);
  });

  it('getSelectionStats 计算选区', () => {
    const s = getSelectionStats('hello', 1, 3);
    expect(s.selectionText).toBe('el');
    expect(s.selectionStart).toBe(1);
    expect(s.selectionEnd).toBe(3);
    // 越界夹取
    expect(getSelectionStats('ab', -5, 99).selectionText).toBe('ab');
  });
});

/**
 * WritingSession 工具函数
 *
 * 从 WritingSession.jsx 提取的纯工具函数，不含 React 状态逻辑。
 */
import { draftsAPI } from '../api';
import logger from '../utils/logger';

/** 异步获取章节内容（SWR fetcher） */
export const fetchChapterContent = async ([_, projectId, chapter]) => {
  try {
    const resp = await draftsAPI.getFinal(projectId, chapter);
    return resp.data?.content || '';
  } catch (e) {
    if (e?.response?.status !== 404) throw e;
    try {
      const versionsResp = await draftsAPI.listVersions(projectId, chapter);
      const versions = versionsResp.data || [];
      if (versions.length > 0) {
        const latestVer = versions[versions.length - 1];
        const draftResp = await draftsAPI.getDraft(projectId, chapter, latestVer);
        return draftResp.data?.content || '';
      }
    } catch (versionError) {
      if (versionError?.response?.status !== 404) throw versionError;
      logger.debug('No drafts found, starting fresh.');
    }
  }
  return '';
};

/** 计算字数/词数（中文按字符，英文按单词） */
export const countWords = (text, language = 'zh') => {
  const clean = String(text || '').trim();
  if (!clean) return 0;
  if (String(language || 'zh').toLowerCase() === 'en') {
    return clean.split(/\s+/).filter(Boolean).length;
  }
  return clean.replace(/\s/g, '').length;
};

/** 向后兼容：中文字符计数 */
export const countChars = (text) => countWords(text, 'zh');

/** 转义正则表达式特殊字符 */
export const escapeRegExp = (value) => String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** 计算选区统计信息 */
export const getSelectionStats = (text, start, end, language = 'zh') => {
  const safeText = text || '';
  const safeStart = Math.max(0, Math.min(start || 0, safeText.length));
  const safeEnd = Math.max(0, Math.min(end || 0, safeText.length));
  const selection = safeText.slice(Math.min(safeStart, safeEnd), Math.max(safeStart, safeEnd));
  return {
    selectionCount: countWords(selection, language),
    cursorText: safeText.slice(0, safeStart),
    selectionText: selection,
    selectionStart: Math.min(safeStart, safeEnd),
    selectionEnd: Math.max(safeStart, safeEnd),
  };
};

/** 规范化星级值（1-3） */
export const normalizeStars = (value) => {
  const parsed = parseInt(value, 10);
  if (Number.isNaN(parsed)) return 1;
  return Math.max(1, Math.min(parsed, 3));
};

/** 解析列表输入（逗号/分号/换行分隔） */
export const parseListInput = (value) => {
  return String(value || '')
    .split(/[,，;；\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
};

/** 格式化列表为中文逗号分隔 */
export const formatListInput = (value) => {
  if (Array.isArray(value)) return value.filter(Boolean).join('，');
  return value || '';
};

/** 格式化规则输入为换行分隔 */
export const formatRulesInput = (value) => {
  if (Array.isArray(value)) return value.filter(Boolean).join('\n');
  return value || '';
};

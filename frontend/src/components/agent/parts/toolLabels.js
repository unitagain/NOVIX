/**
 * 工具展示文案与关键参数取值 —— 渲染层单一 owner（U5 · plan.md §9.5 Step 3）。
 *
 * 事件管道保持结构化到底，摘要只在这一层生成。后端已在 `_safe_tool_call_arguments`
 * 完成脱敏（正文只剩长度、反问只剩题数），此处不做二次安全处理，只做展示取值。
 */

// 展开区隐藏的内部键：已由 header 表达或对作者无意义。
const HIDDEN_ARG_KEYS = new Set(['_summary']);

export const toolDisplayName = (name, t) => {
  const key = `agentPanel.toolNames.${name}`;
  const label = t(key);
  return label === key ? name || t('agentPanel.stageLabels.tool_call') : label;
};

const clip = (value, max = 40) => {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim();
  return text.length > max ? `${text.slice(0, max)}…` : text;
};

const formatCount = (value) => Number(value || 0).toLocaleString();

/**
 * header 上的关键参数：一眼看出这次调用在做什么，取不到就返回空串（不臆造）。
 */
export const toolArgSummary = (name, args, t) => {
  const parsed = args && typeof args === 'object' ? args : {};

  if (name === 'write_content' || name === 'edit_lines') {
    const chars = parsed.content_chars ?? parsed.new_text_chars;
    if (chars === undefined || chars === null) return parsed.mode ? String(parsed.mode) : '';
    const size = t('agentPanel.toolArgChars').replace('{n}', formatCount(chars));
    return parsed.mode ? `${parsed.mode} · ${size}` : size;
  }
  if (name === 'create_chapter') {
    return clip([parsed.chapter_id, parsed.title].filter(Boolean).join(' · '));
  }
  if (name === 'ask_clarification') {
    if (parsed.question_count === undefined || parsed.question_count === null) return '';
    return t('agentPanel.toolArgQuestions').replace('{n}', formatCount(parsed.question_count));
  }

  const hint =
    parsed.query ?? parsed.name ?? parsed.card_name ?? parsed.chapter_id ?? parsed.keyword ?? parsed.subject ?? '';
  return clip(hint);
};

/** 展开区的结构化键值对（非 raw JSON 字符串）。 */
export const toolArgEntries = (args) => {
  if (!args || typeof args !== 'object') return [];
  return Object.entries(args)
    .filter(([key, value]) => !HIDDEN_ARG_KEYS.has(key) && value !== undefined && value !== null && value !== '')
    .map(([key, value]) => [key, typeof value === 'object' ? JSON.stringify(value) : String(value)]);
};

export const toolStatusLabel = (status, t) => {
  const key = `agentPanel.toolStatus.${status || 'running'}`;
  const label = t(key);
  return label === key ? String(status || '') : label;
};

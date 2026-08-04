/**
 * 编辑器标签模型 —— 会话内有效，退出不保留。
 *
 * 标签直接绑定 IDEContext 的 `activeDocument` 单一真相源（chapter / character / world / outline），
 * 不引入第二套「当前打开的是什么」状态；纯函数便于回归测试。
 *
 * 不持久化是有意为之：恢复标签就必须同时恢复正文快照，而正文可能已被 AI、Git 或另一端改过，
 * 「标签里的内容」与磁盘真相源会分叉。会话级缓存没有这个问题。
 */

/** 标签数量上限；超出后按 LRU 淘汰最久未访问的标签，避免正文副本无界堆积。 */
export const MAX_OPEN_TABS = 12;

const TAB_TYPES = new Set(['chapter', 'character', 'world', 'outline']);

/** 文档 → 标签键；类型不支持或缺 id（如尚未命名的新卡片）时返回 null，即不进标签栏。 */
export function tabKeyOf(doc) {
  if (!doc || !TAB_TYPES.has(doc.type)) return null;
  const id = String(doc.id ?? '').trim();
  return id ? `${doc.type}:${id}` : null;
}

function titleOf(doc) {
  return String(
    doc.title || doc.chapter_title || doc.data?.title || doc.data?.chapter_title || doc.data?.name || '',
  ).trim();
}

/** 标签 → 可用于 SET_ACTIVE_DOCUMENT 的文档描述。 */
export function documentOf(tab) {
  return tab ? { type: tab.type, id: tab.id, title: tab.title || '' } : null;
}

/**
 * 打开或激活一个文档标签。
 *
 * 已存在的标签只更新标题与访问序、保持原位置，避免每次切换标签都跳位。
 * `seq` 是单调递增的访问序（不用时间戳，便于确定性测试）。
 */
export function upsertTab(tabs, doc, seq) {
  const key = tabKeyOf(doc);
  if (!key) return tabs;
  const title = titleOf(doc);
  const index = tabs.findIndex((tab) => tab.key === key);
  if (index >= 0) {
    const next = tabs.slice();
    next[index] = { ...next[index], title: title || next[index].title, touchedAt: seq };
    return next;
  }

  const opened = [...tabs, { key, type: doc.type, id: String(doc.id), title, touchedAt: seq }];
  if (opened.length <= MAX_OPEN_TABS) return opened;
  // 刚打开的这个 touchedAt 最大，不会被选中淘汰。
  const victim = opened.reduce((oldest, tab) => (tab.touchedAt < oldest.touchedAt ? tab : oldest));
  return opened.filter((tab) => tab.key !== victim.key);
}

/**
 * 重命名标签的显示标题。
 *
 * 只改标签栏的显示；标题真相源仍是章节 summary，由编辑区的自动保存写回。
 * 允许改成空串——此时标签回退显示章节 id，与「从未设置过标题」表现一致。
 */
export function renameTab(tabs, key, title) {
  const index = tabs.findIndex((tab) => tab.key === key);
  if (index < 0) return tabs;
  const next = String(title ?? '').trim();
  if (next === tabs[index].title) return tabs;
  const updated = tabs.slice();
  updated[index] = { ...updated[index], title: next };
  return updated;
}

/**
 * 关闭标签。
 *
 * @returns {{tabs: Array, nextActive: Object|null|undefined}}
 *   `nextActive === undefined` 表示当前文档不受影响；`null` 表示应回到空态。
 *   关掉的若是当前标签，接管顺序为「右邻 → 左邻」，与主流 IDE 一致。
 */
export function closeTab(tabs, key, activeKey) {
  const index = tabs.findIndex((tab) => tab.key === key);
  if (index < 0) return { tabs, nextActive: undefined };
  const remaining = tabs.filter((tab) => tab.key !== key);
  if (key !== activeKey) return { tabs: remaining, nextActive: undefined };
  return { tabs: remaining, nextActive: documentOf(remaining[index] || remaining[index - 1] || null) };
}

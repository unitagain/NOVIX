import { describe, it, expect } from 'vitest';

import { MAX_OPEN_TABS, closeTab, documentOf, renameTab, tabKeyOf, upsertTab } from './editorTabs';

const chapter = (id, title = '') => ({ type: 'chapter', id, title });

/** 依次打开一串章节，返回最终标签表（seq 从 1 开始单调递增）。 */
const openAll = (docs) => docs.reduce((tabs, doc, index) => upsertTab(tabs, doc, index + 1), []);

describe('editorTabs.tabKeyOf', () => {
  it('支持的四类文档都能生成标签键', () => {
    expect(tabKeyOf(chapter('chapter_001'))).toBe('chapter:chapter_001');
    expect(tabKeyOf({ type: 'outline', id: 'outline' })).toBe('outline:outline');
    expect(tabKeyOf({ type: 'character', id: '千羽' })).toBe('character:千羽');
    expect(tabKeyOf({ type: 'world', id: '青云宗' })).toBe('world:青云宗');
  });

  it('未知类型、空文档、无 id 的新卡片不进标签栏', () => {
    expect(tabKeyOf(null)).toBeNull();
    expect(tabKeyOf({ type: 'wiki', id: 'x' })).toBeNull();
    expect(tabKeyOf({ type: 'character', id: '' })).toBeNull();
    expect(tabKeyOf({ type: 'character', id: '  ' })).toBeNull();
  });
});

describe('editorTabs.upsertTab', () => {
  it('重复打开同一文档不新增标签，且保持原有位置', () => {
    const tabs = openAll([chapter('c1'), chapter('c2'), chapter('c3')]);
    const reopened = upsertTab(tabs, chapter('c1'), 10);
    expect(reopened).toHaveLength(3);
    expect(reopened.map((tab) => tab.key)).toEqual(['chapter:c1', 'chapter:c2', 'chapter:c3']);
    expect(reopened[0].touchedAt).toBe(10); // 只更新访问序（LRU）
  });

  it('重新打开时补齐标题，但不会用空标题覆盖已有标题', () => {
    const tabs = upsertTab([], chapter('c1'), 1);
    expect(tabs[0].title).toBe('');
    const titled = upsertTab(tabs, chapter('c1', '开端'), 2);
    expect(titled[0].title).toBe('开端');
    expect(upsertTab(titled, chapter('c1'), 3)[0].title).toBe('开端');
  });

  it('超出上限时淘汰最久未访问的标签，而不是最早打开的', () => {
    const docs = Array.from({ length: MAX_OPEN_TABS }, (_, index) => chapter(`c${index}`));
    let tabs = openAll(docs);
    expect(tabs).toHaveLength(MAX_OPEN_TABS);

    // 重新访问最早打开的 c0，使 c1 成为最久未访问者
    tabs = upsertTab(tabs, chapter('c0'), 100);
    tabs = upsertTab(tabs, chapter('new'), 101);

    expect(tabs).toHaveLength(MAX_OPEN_TABS);
    expect(tabs.map((tab) => tab.key)).toContain('chapter:c0');
    expect(tabs.map((tab) => tab.key)).toContain('chapter:new');
    expect(tabs.map((tab) => tab.key)).not.toContain('chapter:c1');
  });

  it('不支持的文档不产生标签', () => {
    expect(upsertTab([], { type: 'wiki', id: 'x' }, 1)).toEqual([]);
  });
});

describe('editorTabs.closeTab', () => {
  it('关闭非当前标签时不改变当前文档', () => {
    const tabs = openAll([chapter('c1'), chapter('c2'), chapter('c3')]);
    const result = closeTab(tabs, 'chapter:c1', 'chapter:c3');
    expect(result.tabs.map((tab) => tab.key)).toEqual(['chapter:c2', 'chapter:c3']);
    expect(result.nextActive).toBeUndefined(); // undefined = 保持当前文档
  });

  it('关闭当前标签时接管右邻', () => {
    const tabs = openAll([chapter('c1'), chapter('c2', '第二章'), chapter('c3')]);
    const result = closeTab(tabs, 'chapter:c1', 'chapter:c1');
    expect(result.nextActive).toEqual({ type: 'chapter', id: 'c2', title: '第二章' });
  });

  it('关闭最右侧的当前标签时退回左邻', () => {
    const tabs = openAll([chapter('c1'), chapter('c2')]);
    const result = closeTab(tabs, 'chapter:c2', 'chapter:c2');
    expect(result.nextActive).toEqual({ type: 'chapter', id: 'c1', title: '' });
  });

  it('关闭最后一个标签回到空态', () => {
    const tabs = openAll([chapter('c1')]);
    const result = closeTab(tabs, 'chapter:c1', 'chapter:c1');
    expect(result.tabs).toEqual([]);
    expect(result.nextActive).toBeNull(); // null = 显式清空
  });

  it('关闭不存在的标签是无操作（如删除一个没打开的章节）', () => {
    const tabs = openAll([chapter('c1')]);
    const result = closeTab(tabs, 'chapter:missing', 'chapter:c1');
    expect(result.tabs).toBe(tabs);
    expect(result.nextActive).toBeUndefined();
  });
});

describe('editorTabs.renameTab', () => {
  it('只改目标标签的标题，位置与访问序不变', () => {
    const tabs = openAll([chapter('c1', '旧标题'), chapter('c2')]);
    const renamed = renameTab(tabs, 'chapter:c1', '  新标题  ');
    expect(renamed[0]).toEqual({ ...tabs[0], title: '新标题' });
    expect(renamed[1]).toBe(tabs[1]);
  });

  it('允许清空标题（回退显示章节 id）', () => {
    const tabs = openAll([chapter('c1', '旧标题')]);
    expect(renameTab(tabs, 'chapter:c1', '   ')[0].title).toBe('');
  });

  it('标题未变或标签不存在时返回原数组，避免无意义重渲染', () => {
    const tabs = openAll([chapter('c1', '旧标题')]);
    expect(renameTab(tabs, 'chapter:c1', '旧标题')).toBe(tabs);
    expect(renameTab(tabs, 'chapter:missing', 'x')).toBe(tabs);
  });
});

describe('editorTabs.documentOf', () => {
  it('标签可还原为 SET_ACTIVE_DOCUMENT 可用的文档描述', () => {
    expect(documentOf({ key: 'outline:outline', type: 'outline', id: 'outline', title: '' })).toEqual({
      type: 'outline',
      id: 'outline',
      title: '',
    });
    expect(documentOf(null)).toBeNull();
  });
});

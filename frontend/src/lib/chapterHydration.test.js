import { describe, expect, it } from 'vitest';
import {
  canAutosaveLoadedChapter,
  resolveRestoredChapter,
  shouldApplyLoadedChapter,
} from './chapterHydration';

describe('chapterHydration', () => {
  it('优先恢复有效的上次章节，否则选择首章', () => {
    expect(resolveRestoredChapter(['V1C1', 'V1C2'], 'V1C2')).toBe('V1C2');
    expect(resolveRestoredChapter(['V1C1', 'V1C2'], 'missing')).toBe('V1C1');
  });

  it('初始加载允许覆盖空占位，但已加载后的未保存编辑不被 SWR 覆盖', () => {
    expect(
      shouldApplyLoadedChapter({
        selectedChapter: 'V1C1',
        loadState: { chapter: 'V1C1', ready: false },
        loadedContent: '正文',
        hasUnsavedChanges: true,
      }),
    ).toBe(true);
    expect(
      shouldApplyLoadedChapter({
        selectedChapter: 'V1C1',
        loadState: { chapter: 'V1C1', ready: true },
        loadedContent: '旧正文',
        hasUnsavedChanges: true,
      }),
    ).toBe(false);
  });

  it('章节完成水合前禁止自动保存空占位', () => {
    expect(
      canAutosaveLoadedChapter({
        projectId: 'p1',
        chapter: 'V1C1',
        loadState: { chapter: 'V1C1', ready: false },
        hasUnsavedChanges: true,
        blocked: false,
      }),
    ).toBe(false);
    expect(
      canAutosaveLoadedChapter({
        projectId: 'p1',
        chapter: 'V1C1',
        loadState: { chapter: 'V1C1', ready: true },
        hasUnsavedChanges: true,
        blocked: false,
      }),
    ).toBe(true);
  });
});


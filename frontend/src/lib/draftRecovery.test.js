import { describe, expect, it } from 'vitest';
import {
  canSendKeepaliveDraft,
  clearDraftRecovery,
  readDraftRecovery,
  resolveDraftRecovery,
  writeDraftRecovery,
} from './draftRecovery';

const memoryStorage = () => {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
};

describe('draftRecovery', () => {
  it('按项目和章节保存、读取并清理恢复快照', () => {
    const storage = memoryStorage();
    expect(
      writeDraftRecovery(storage, {
        projectId: 'p1',
        chapter: 'V1C001',
        content: '未保存正文',
        savedContent: '旧正文',
        turnEffect: { change_type: 'plot_edit' },
      }),
    ).toBe(true);
    expect(readDraftRecovery(storage, 'p1', 'V1C001')).toMatchObject({
      content: '未保存正文',
      needsCanonSync: true,
      turnEffect: { change_type: 'plot_edit' },
    });
    clearDraftRecovery(storage, 'p1', 'V1C001');
    expect(readDraftRecovery(storage, 'p1', 'V1C001')).toBeNull();
  });

  it('恢复与服务端不同的本地正文，并保留待同步事实标记', () => {
    expect(resolveDraftRecovery({ content: '', needsCanonSync: true, turnEffect: { change_type: 'plot_edit' } }, '服务端正文')).toEqual({
      action: 'restore',
      content: '',
      title: null,
      needsCanonSync: true,
      turnEffect: { change_type: 'plot_edit' },
    });
    expect(resolveDraftRecovery({ content: '正文', needsCanonSync: true }, '正文')).toEqual({
      action: 'clear',
      content: '正文',
      needsCanonSync: false,
      turnEffect: null,
    });
  });

  it('正文已保存但事实收尾未完成时，仅重试对应的 turn effect', () => {
    expect(
      resolveDraftRecovery(
        {
          content: '正文',
          needsCanonSync: true,
          turnEffect: { change_type: 'chapter_write', fact_operation: 'replace_chapter' },
        },
        '正文',
      ),
    ).toEqual({
      action: 'sync_canon',
      content: '正文',
      needsCanonSync: true,
      turnEffect: { change_type: 'chapter_write', fact_operation: 'replace_chapter' },
    });
  });

  it('仅对 keepalive 安全体积发送退出保存', () => {
    expect(canSendKeepaliveDraft({ projectId: 'p1', chapter: 'V1C001', content: '正文' })).toBe(true);
    expect(canSendKeepaliveDraft({ projectId: 'p1', chapter: 'V1C001', content: 'x'.repeat(70000) })).toBe(false);
  });
});

import { beforeEach, describe, expect, it, vi } from 'vitest';

const draftsAPI = vi.hoisted(() => ({
  getFinal: vi.fn(),
  getDraft: vi.fn(),
  listVersions: vi.fn(),
}));

vi.mock('../api', () => ({ draftsAPI }));

import { fetchChapterContent } from './writingSessionHelpers';

describe('fetchChapterContent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('优先读取当前成稿', async () => {
    draftsAPI.getFinal.mockResolvedValue({ data: { content: '已保存正文' } });

    await expect(fetchChapterContent(['chapter', 'p1', 'V1C1'])).resolves.toBe('已保存正文');
    expect(draftsAPI.listVersions).not.toHaveBeenCalled();
  });

  it('成稿不存在时回退到最新历史草稿', async () => {
    draftsAPI.getFinal.mockRejectedValue({ response: { status: 404 } });
    draftsAPI.listVersions.mockResolvedValue({ data: ['v1', 'v2'] });
    draftsAPI.getDraft.mockResolvedValue({ data: { content: '历史正文' } });

    await expect(fetchChapterContent(['chapter', 'p1', 'V1C1'])).resolves.toBe('历史正文');
    expect(draftsAPI.getDraft).toHaveBeenCalledWith('p1', 'V1C1', 'v2');
  });

  it('网络错误向上传递，不伪装成空章节', async () => {
    const error = new Error('network down');
    draftsAPI.getFinal.mockRejectedValue(error);

    await expect(fetchChapterContent(['chapter', 'p1', 'V1C1'])).rejects.toBe(error);
    expect(draftsAPI.listVersions).not.toHaveBeenCalled();
  });
});

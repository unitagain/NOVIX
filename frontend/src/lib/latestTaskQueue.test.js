import { describe, expect, it, vi } from 'vitest';
import { createLatestTaskQueue } from './latestTaskQueue';

describe('createLatestTaskQueue', () => {
  it('保存进行中产生的新任务不会丢失，并只执行最新等待版本', async () => {
    let releaseFirst;
    const firstGate = new Promise((resolve) => {
      releaseFirst = resolve;
    });
    const saved = [];
    const worker = vi.fn(async (task) => {
      saved.push(task.content);
      if (task.content === 'v1') await firstGate;
    });
    const queue = createLatestTaskQueue(worker);

    queue.replace({ content: 'v1' });
    const running = queue.flush();
    queue.replace({ content: 'v2' });
    queue.replace({ content: 'v3' });
    releaseFirst();
    await running;

    expect(saved).toEqual(['v1', 'v3']);
  });

  it('失败任务会保留，允许稍后重试', async () => {
    let attempts = 0;
    const queue = createLatestTaskQueue(async () => {
      attempts += 1;
      if (attempts === 1) throw new Error('offline');
    });

    queue.replace({ content: 'latest' });
    await expect(queue.flush()).rejects.toThrow('offline');
    expect(queue.peek()).toEqual({ content: 'latest' });
    await queue.flush();
    expect(attempts).toBe(2);
  });
});

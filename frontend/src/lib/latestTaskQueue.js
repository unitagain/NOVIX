export function createLatestTaskQueue(worker) {
  let pending = null;
  let running = null;
  let stopped = false;

  const drain = () => {
    if (stopped) return Promise.resolve();
    if (running) return running;

    let failed = false;
    running = (async () => {
      while (!stopped && pending) {
        const task = pending;
        pending = null;
        try {
          await worker(task);
        } catch (error) {
          failed = true;
          if (!pending) pending = task;
          throw error;
        }
      }
    })().finally(() => {
      running = null;
      if (!failed && !stopped && pending) queueMicrotask(() => drain().catch(() => {}));
    });

    return running;
  };

  return {
    replace(task) {
      if (!stopped) pending = task;
    },
    flush: drain,
    peek() {
      return pending;
    },
    stop() {
      stopped = true;
      pending = null;
    },
  };
}

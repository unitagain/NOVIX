import { defineConfig } from 'vitest/config';

// 前端单元测试（Vitest）——对标后端 pytest 的回归网。
// 优先覆盖纯逻辑（helpers / 归约器 / intent 路由）；组件级 RTL 测试后续按需再加（届时切 jsdom）。
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.{test,spec}.{js,jsx,ts,tsx}'],
    globals: false,
  },
});

import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const parsePort = (value, fallback) => {
  const port = Number.parseInt(String(value ?? ''), 10);
  return Number.isFinite(port) && port > 0 ? port : fallback;
};

const normalizeBaseUrl = (raw) => String(raw || '').trim().replace(/\/+$/, '');

const toWsUrl = (httpUrl) => {
  if (!httpUrl) return '';
  if (httpUrl.startsWith('https://')) return httpUrl.replace(/^https:\/\//, 'wss://');
  if (httpUrl.startsWith('http://')) return httpUrl.replace(/^http:\/\//, 'ws://');
  return httpUrl;
};

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');

  const frontendPort = parsePort(env.VITE_DEV_PORT || env.WENSHAPE_FRONTEND_PORT, 3000);
  const backendPort = parsePort(env.VITE_BACKEND_PORT || env.WENSHAPE_BACKEND_PORT, 8000);
  const backendBaseUrl = normalizeBaseUrl(env.VITE_BACKEND_URL) || `http://localhost:${backendPort}`;
  const backendWsUrl = normalizeBaseUrl(env.VITE_BACKEND_WS_URL) || toWsUrl(backendBaseUrl);

  return {
    plugins: [react()],
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes('node_modules')) return undefined;
            // 关系图谱画布依赖（React Flow + d3/zustand）只被懒加载组件引用，必须最先判定：
            // `@xyflow/react` 的路径含 `/react/`，若排在 react 规则之后会被并进首屏 chunk。
            if (
              id.includes('@xyflow') ||
              id.includes('/d3-') ||
              id.includes('/zustand/') ||
              id.includes('/classcat/')
            ) {
              return 'vendor-flow';
            }
            // Keep router packages together to avoid circular chunk deps with React core.
            // 将路由相关包单独聚合，避免与 React 核心切块后形成循环依赖。
            if (
              id.includes('/react-router-dom/') ||
              id.includes('/react-router/') ||
              id.includes('/@remix-run/router/') ||
              id.includes('/use-sync-external-store/')
            ) {
              return 'vendor-router';
            }
            if (
              id.includes('/react/') ||
              id.includes('/react-dom/') ||
              id.includes('/scheduler/')
            ) {
              return 'vendor-react';
            }
            if (id.includes('framer-motion')) return 'vendor-motion';
            if (id.includes('lucide-react')) return 'vendor-icons';
            if (id.includes('axios') || id.includes('swr')) return 'vendor-data';
            if (id.includes('tailwind-merge') || id.includes('clsx')) return 'vendor-ui';
            return 'vendor-misc';
          },
        },
      },
    },
    server: {
      port: frontendPort,
      strictPort: false,
      proxy: {
        '/api': {
          target: backendBaseUrl,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, '')
        },
        '/ws': {
          target: backendWsUrl,
          ws: true
        }
      }
    }
  };
});

/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   前端 API 调用层，统一管理所有与后端的 HTTP 和 WebSocket 通信。
 *   支持多个 API 客户端、超时配置和 WebSocket 重连机制。
 */

import axios, { AxiosResponse } from 'axios';
import logger from './utils/logger';
import type {
  Project,
  CharacterCard,
  WorldCard,
  StyleCard,
  Draft,
  ChapterSummary,
  Volume,
  Fact,
  SessionStatus,
  EditSuggestResult,
  LLMProfile,
} from './types';

// ============================================================================
// API 配置 / API Configuration
// ============================================================================
const API_BASE = '/api';

// 普通 API 调用超时时间（30 秒）
const DEFAULT_TIMEOUT = 30000;

// LLM 相关操作的超时时间（5 分钟）
const LLM_TIMEOUT = 300000;

// 批量操作的超时时间（30 分钟）
const LLM_SYNC_TIMEOUT = 1800000;

// ============================================================================
// Axios 实例 / Axios Instances
// ============================================================================

/**
 * 普通 API 实例 - Default timeout
 */
const api = axios.create({
  timeout: DEFAULT_TIMEOUT,
});

/**
 * LLM 操作专用实例 - Extended timeout for LLM operations
 */
const llmApi = axios.create({
  timeout: LLM_TIMEOUT,
});

// ============================================================================
// 项目 API / Projects API
// ============================================================================
export const projectsAPI = {
  list: (): Promise<AxiosResponse<Project[]>> => api.get(`${API_BASE}/projects`),
  get: (id: string): Promise<AxiosResponse<Project>> => api.get(`${API_BASE}/projects/${id}`),
  create: (data: Partial<Project>): Promise<AxiosResponse> => api.post(`${API_BASE}/projects`, data),
  rename: (id: string, data: { name: string }): Promise<AxiosResponse> => api.patch(`${API_BASE}/projects/${id}`, data),
  delete: (id: string): Promise<AxiosResponse> => api.delete(`${API_BASE}/projects/${id}`),
};

// ============================================================================
// 卡片 API / Cards API
// ============================================================================
export const cardsAPI = {
  // 角色卡片操作 / Character cards
  listCharactersIndex: (projectId: string): Promise<AxiosResponse<CharacterCard[]>> =>
    api.get(`${API_BASE}/projects/${projectId}/cards/characters/index`),
  getCharacter: (projectId: string, name: string): Promise<AxiosResponse<CharacterCard>> =>
    api.get(`${API_BASE}/projects/${projectId}/cards/characters/${name}`),
  createCharacter: (projectId: string, data: CharacterCard): Promise<AxiosResponse> =>
    api.post(`${API_BASE}/projects/${projectId}/cards/characters`, data),
  updateCharacter: (projectId: string, name: string, data: CharacterCard): Promise<AxiosResponse> =>
    api.put(`${API_BASE}/projects/${projectId}/cards/characters/${name}`, data),
  deleteCharacter: (projectId: string, name: string): Promise<AxiosResponse> =>
    api.delete(`${API_BASE}/projects/${projectId}/cards/characters/${name}`),

  // 世界观卡片操作 / World cards
  listWorldIndex: (projectId: string): Promise<AxiosResponse<WorldCard[]>> =>
    api.get(`${API_BASE}/projects/${projectId}/cards/world/index`),
  getWorld: (projectId: string, name: string): Promise<AxiosResponse<WorldCard>> =>
    api.get(`${API_BASE}/projects/${projectId}/cards/world/${name}`),
  createWorld: (projectId: string, data: WorldCard): Promise<AxiosResponse> =>
    api.post(`${API_BASE}/projects/${projectId}/cards/world`, data),
  updateWorld: (projectId: string, name: string, data: WorldCard): Promise<AxiosResponse> =>
    api.put(`${API_BASE}/projects/${projectId}/cards/world/${name}`, data),
  deleteWorld: (projectId: string, name: string): Promise<AxiosResponse> =>
    api.delete(`${API_BASE}/projects/${projectId}/cards/world/${name}`),

  // 文风卡片操作 / Style cards
  getStyle: (projectId: string): Promise<AxiosResponse<StyleCard>> =>
    api.get(`${API_BASE}/projects/${projectId}/cards/style`),
  updateStyle: (projectId: string, data: StyleCard): Promise<AxiosResponse> =>
    api.put(`${API_BASE}/projects/${projectId}/cards/style`, data),
  extractStyle: (
    projectId: string,
    data: { content: string; language?: string },
  ): Promise<AxiosResponse<{ style: string }>> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/cards/style/extract`, data),
};

// ============================================================================
// 会话 API / Session API（LLM 操作使用扩展超时）
// ============================================================================
export const sessionAPI = {
  start: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/start`, data, { timeout: LLM_SYNC_TIMEOUT }),
  getStatus: (projectId: string): Promise<AxiosResponse<SessionStatus>> =>
    api.get(`${API_BASE}/projects/${projectId}/session/status`),
  submitFeedback: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/feedback`, data),
  suggestEdit: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse<EditSuggestResult>> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/edit-suggest`, data),
  // Phase 5（vibe writing）：让后端自判本轮意图（write/edit），前端据此单输入框路由。
  classifyIntent: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    api.post(`${API_BASE}/projects/${projectId}/session/classify-intent`, data),
  // Phase 12（单 Writer 主循环）：统一对话入口——一句话 → 后端自判 write/edit/continue/plan 并执行。
  chat: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/chat`, data, { timeout: LLM_SYNC_TIMEOUT }),
  // Phase 11（Plan 编排）：把复杂指令拆成串行 todo。
  plan: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/plan`, data, { timeout: LLM_SYNC_TIMEOUT }),
  executePlan: (projectId: string, planId: string): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/plan/${planId}/execute`, {}, { timeout: LLM_SYNC_TIMEOUT }),
  // Phase 6（按需评审）：对照 canon 给本章一致性评审（只报告不改写）。
  review: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/review`, data),
  // 对话记忆层（Git-Native 持久化 + compact）：开项目加载历史、每条消息追加、长对话压缩。
  getHistory: (projectId: string, limit = 0): Promise<AxiosResponse> =>
    api.get(`${API_BASE}/projects/${projectId}/session/history`, { params: { limit } }),
  appendHistory: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    api.post(`${API_BASE}/projects/${projectId}/session/history`, data),
  compactHistory: (projectId: string): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/history/compact`, {}, { timeout: LLM_SYNC_TIMEOUT }),
  answerQuestions: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/answer-questions`, data, { timeout: LLM_SYNC_TIMEOUT }),
  cancel: (projectId: string): Promise<AxiosResponse> => api.post(`${API_BASE}/projects/${projectId}/session/cancel`),
  analyze: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/analyze`, data),
  saveAnalysis: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/save-analysis`, data),
  analyzeSync: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/analyze-sync`, data, { timeout: LLM_SYNC_TIMEOUT }),
  analyzeBatch: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/analyze-batch`, data, { timeout: LLM_SYNC_TIMEOUT }),
  saveAnalysisBatch: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/session/save-analysis-batch`, data, { timeout: LLM_SYNC_TIMEOUT }),
};

// ============================================================================
// 记忆包 API / Memory Pack API
// ============================================================================
export const memoryPackAPI = {
  getStatus: (projectId: string, chapter: string): Promise<AxiosResponse> =>
    api.get(`${API_BASE}/projects/${projectId}/memory-pack/${chapter}`),
};

// ============================================================================
// 草稿 API / Drafts API
// ============================================================================
export const draftsAPI = {
  listChapters: (projectId: string): Promise<AxiosResponse<string[]>> =>
    api.get(`${API_BASE}/projects/${projectId}/drafts`),
  listSummaries: (projectId: string, volumeId?: string): Promise<AxiosResponse<ChapterSummary[]>> =>
    api.get(`${API_BASE}/projects/${projectId}/drafts/summaries`, {
      params: volumeId ? { volume_id: volumeId } : undefined,
    }),
  listVersions: (projectId: string, chapter: string): Promise<AxiosResponse<string[]>> =>
    api.get(`${API_BASE}/projects/${projectId}/drafts/${chapter}/versions`),
  getDraft: (projectId: string, chapter: string, version: string): Promise<AxiosResponse<Draft>> =>
    api.get(`${API_BASE}/projects/${projectId}/drafts/${chapter}/${version}`),
  getFinal: (projectId: string, chapter: string): Promise<AxiosResponse<{ content: string }>> =>
    api.get(`${API_BASE}/projects/${projectId}/drafts/${chapter}/final`),
  getSummary: (projectId: string, chapter: string): Promise<AxiosResponse<ChapterSummary>> =>
    api.get(`${API_BASE}/projects/${projectId}/drafts/${chapter}/summary`),
  saveSummary: (projectId: string, chapter: string, data: Partial<ChapterSummary>): Promise<AxiosResponse> =>
    api.post(`${API_BASE}/projects/${projectId}/drafts/${chapter}/summary`, data),
  deleteChapter: (projectId: string, chapter: string): Promise<AxiosResponse> =>
    api.delete(`${API_BASE}/projects/${projectId}/drafts/${chapter}`),
  reorderChapters: (projectId: string, data: { chapters: string[] }): Promise<AxiosResponse> =>
    api.post(`${API_BASE}/projects/${projectId}/drafts/reorder`, data),
  updateContent: (projectId: string, chapter: string, data: { content: string }): Promise<AxiosResponse> =>
    api.put(`${API_BASE}/projects/${projectId}/drafts/${chapter}/content`, data),
  autosaveContent: (projectId: string, chapter: string, data: { content: string }): Promise<AxiosResponse> =>
    api.put(`${API_BASE}/projects/${projectId}/drafts/${chapter}/autosave`, data),
};

// ============================================================================
// 卷 API / Volumes API
// ============================================================================
export const volumesAPI = {
  list: (projectId: string): Promise<AxiosResponse<Volume[]>> => api.get(`${API_BASE}/projects/${projectId}/volumes`),
  create: (projectId: string, data: Partial<Volume>): Promise<AxiosResponse<Volume>> =>
    api.post(`${API_BASE}/projects/${projectId}/volumes`, data),
  update: (projectId: string, volumeId: string, data: Partial<Volume>): Promise<AxiosResponse<Volume>> =>
    api.put(`${API_BASE}/projects/${projectId}/volumes/${volumeId}`, data),
  delete: (projectId: string, volumeId: string): Promise<AxiosResponse> =>
    api.delete(`${API_BASE}/projects/${projectId}/volumes/${volumeId}`),
  getSummary: (projectId: string, volumeId: string): Promise<AxiosResponse> =>
    api.get(`${API_BASE}/projects/${projectId}/volumes/${volumeId}/summary`),
  saveSummary: (projectId: string, volumeId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    api.put(`${API_BASE}/projects/${projectId}/volumes/${volumeId}/summary`, data),
  refreshSummaries: (projectId: string, volumeIds: string[]): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/volumes/refresh-summaries`, { volume_ids: volumeIds }),
};

// ============================================================================
// 事实表 / Canon API（事实管理）
// ============================================================================
export const canonAPI = {
  createManual: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    api.post(`${API_BASE}/projects/${projectId}/canon/facts/manual`, data),
  update: (projectId: string, factId: string, data: Partial<Fact>): Promise<AxiosResponse> =>
    api.put(`${API_BASE}/projects/${projectId}/canon/facts/by-id/${factId}`, data),
  delete: (projectId: string, factId: string): Promise<AxiosResponse> =>
    api.delete(`${API_BASE}/projects/${projectId}/canon/facts/by-id/${factId}`),
  getTree: (projectId: string): Promise<AxiosResponse> => api.get(`${API_BASE}/projects/${projectId}/facts/tree`),
};

// ============================================================================
// 证据 API / Evidence API
// ============================================================================
export const evidenceAPI = {
  search: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    api.post(`${API_BASE}/projects/${projectId}/evidence/search`, data),
  rebuild: (projectId: string): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/evidence/rebuild`),
};

// ============================================================================
// 文本分块 API / Text Chunks API
// ============================================================================
export const textChunksAPI = {
  rebuild: (projectId: string): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/text-chunks/rebuild`),
};

// ============================================================================
// 绑定 API / Bindings API
// ============================================================================
export const bindingsAPI = {
  get: (projectId: string, chapter: string): Promise<AxiosResponse> =>
    api.get(`${API_BASE}/projects/${projectId}/bindings/${chapter}`),
  rebuildBatch: (projectId: string, data: Record<string, unknown>): Promise<AxiosResponse> =>
    llmApi.post(`${API_BASE}/projects/${projectId}/bindings/rebuild-batch`, data),
};

// ============================================================================
// 导出 API / Export API
// ============================================================================
export const exportAPI = {
  download: (
    projectId: string,
    data: {
      chapter_ids: string[];
      format: 'txt' | 'md' | 'docx';
      include_chapter_titles?: boolean;
    },
  ): Promise<AxiosResponse<Blob>> =>
    api.post(`${API_BASE}/projects/${projectId}/export`, data, { responseType: 'blob' }),
};

// ============================================================================
// 配置 API / Config API
// ============================================================================
export const configAPI = {
  getProfiles: (): Promise<AxiosResponse<LLMProfile[]>> => api.get(`${API_BASE}/config/llm/profiles`),
  saveProfile: (data: Partial<LLMProfile>): Promise<AxiosResponse> => api.post(`${API_BASE}/config/llm/profiles`, data),
  fetchModels: (data: Record<string, unknown>): Promise<AxiosResponse> =>
    api.post(`${API_BASE}/proxy/fetch-models`, data),
  testModel: (data: Record<string, unknown>): Promise<AxiosResponse> => api.post(`${API_BASE}/proxy/test-model`, data),
  deleteProfile: (id: string): Promise<AxiosResponse> => api.delete(`${API_BASE}/config/llm/profiles/${id}`),
  getAssignments: (): Promise<AxiosResponse> => api.get(`${API_BASE}/config/llm/assignments`),
  updateAssignments: (data: Record<string, unknown>): Promise<AxiosResponse> =>
    api.post(`${API_BASE}/config/llm/assignments`, data),
};

// ============================================================================
// WebSocket 配置和类型定义 / WebSocket Configuration
// ============================================================================

/**
 * WebSocket 连接状态类型
 */
type WSStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

/**
 * WebSocket 选项配置
 */
interface WebSocketOptions {
  onStatus?: (status: WSStatus) => void; // 状态变更回调
  maxRetries?: number; // 最大重连次数
  retryDelay?: number; // 初始重连延迟（毫秒）
  maxDelay?: number; // 最大重连延迟
  heartbeatInterval?: number; // 心跳间隔
}

/**
 * WebSocket 连接句柄
 */
interface WebSocketHandle {
  readonly socket: WebSocket | null;
  close: () => void;
}

// ============================================================================
// WebSocket 工厂函数 / WebSocket Factory
// ============================================================================

/**
 * 创建实时会话 WebSocket 连接
 *
 * 与后端建立实时会话 WebSocket 连接，支持自动重连和心跳检测。
 * 自动处理连接断裂、网络波动等场景。
 *
 * @param {string} projectId - 项目 ID
 * @param {Function} onMessage - 消息接收回调，接收解析后的 JSON 数据
 * @param {WebSocketOptions} [options={}] - 连接选项
 * @returns {WebSocketHandle} WebSocket 连接句柄，包含 socket 对象和 close 方法
 *
 * @example
 * const ws = createWebSocket('project-123', (data) => {
 *   console.log('Received:', data);
 * }, { maxRetries: 6, onStatus: (status) => {
 *   console.log('Connection status:', status);
 * }});
 *
 * // 使用完毕后关闭连接
 * ws.close();
 */
export const createWebSocket = (
  projectId: string,
  onMessage: (data: Record<string, unknown>) => void,
  options: WebSocketOptions = {},
): WebSocketHandle => {
  // 根据当前协议确定 WebSocket 协议（ws 或 wss）
  const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const wsHost = window.location.host;

  const { onStatus, maxRetries = 6, retryDelay = 800, maxDelay = 8000, heartbeatInterval = 20000 } = options;

  let ws: WebSocket | null = null;
  let heartbeatTimer: number | null = null;
  let reconnectTimer: number | null = null;
  let shouldReconnect = true;
  let retryCount = 0;

  /**
   * 通知连接状态变更
   */
  const notifyStatus = (status: WSStatus): void => {
    onStatus?.(status);
  };

  /**
   * 启动心跳检测
   * 定期向服务器发送心跳信息以检测连接是否仍然活跃
   */
  const startHeartbeat = (): void => {
    if (heartbeatTimer) return;
    heartbeatTimer = window.setInterval(() => {
      try {
        ws?.send(String(Date.now()));
      } catch {
        // 忽略心跳发送失败，连接已断开会在 onclose 中处理
      }
    }, heartbeatInterval);
  };

  /**
   * 停止心跳检测
   */
  const stopHeartbeat = (): void => {
    if (heartbeatTimer) {
      window.clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  };

  /**
   * 建立 WebSocket 连接
   * 首次连接或重连时调用
   */
  const connect = (): void => {
    notifyStatus(retryCount > 0 ? 'reconnecting' : 'connecting');
    ws = new WebSocket(`${wsProtocol}://${wsHost}/ws/${projectId}/session`);

    ws.onopen = () => {
      retryCount = 0;
      notifyStatus('connected');
      startHeartbeat();
    };

    ws.onmessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data);
        onMessage(data);
      } catch (e) {
        logger.error('Failed to parse WebSocket message:', e);
      }
    };

    ws.onerror = (error: Event) => {
      logger.error('WebSocket error:', error);
    };

    ws.onclose = () => {
      stopHeartbeat();
      // 如果允许重连且未超过最大重连次数，则进行重连
      if (shouldReconnect && retryCount < maxRetries) {
        // 使用指数退避策略计算重连延迟，避免频繁重连
        const delay = Math.min(maxDelay, retryDelay * Math.pow(1.5, retryCount));
        retryCount += 1;
        reconnectTimer = window.setTimeout(connect, delay);
      } else {
        notifyStatus('disconnected');
      }
    };
  };

  // 立即建立连接
  connect();

  return {
    get socket() {
      return ws;
    },
    /**
     * 关闭 WebSocket 连接
     * 调用此方法后不会再进行自动重连
     */
    close: () => {
      shouldReconnect = false;
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      stopHeartbeat();
      ws?.close();
    },
  };
};

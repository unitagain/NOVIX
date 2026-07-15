/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   IDE 全局状态管理上下文，使用 useReducer 模式维护复杂的 UI 状态。
 *   管理面板布局、编辑状态、连接状态等。
 */

import React, { createContext, useContext, useReducer, useMemo } from 'react';

const IDEContext = createContext(null);

/**
 * IDE 初始状态 / IDE Initial State
 *
 * 包含面板控制、编辑器状态、连接状态等多个维度的 UI 状态。
 */
const initialState = {
  // ========================================================================
  // 面板控制 / Panel Control
  // ========================================================================
  activeActivity: 'explorer', // 'explorer' | 'cards' | 'search' | 'settings' | 'drafts'
  sidePanelVisible: true, // 左侧面板是否可见
  rightPanelVisible: true, // 右侧面板是否可见
  sidePanelWidth: 248, // 左侧面板宽度（像素，悬浮卡片设计下更窄更紧凑）
  rightPanelWidth: 460, // 右侧 Agent Dock：AI IDE 默认宽度

  // ========================================================================
  // 编辑器状态 / Editor State
  // ========================================================================
  activeProjectId: null, // 当前活跃项目 ID
  activeChapter: null, // 当前编辑的章节
  activeDocument: null, // { type: 'chapter' | 'card' | 'wiki', id: string, data: any }

  // 编辑器光标和选择状态 / Cursor and Selection
  cursorPosition: { line: 1, column: 1 }, // 光标位置
  wordCount: 0, // 总字数
  selectionCount: 0, // 选中字数
  lastSavedAt: null, // 上次保存时间
  lastAutosavedAt: null, // 上次自动保存时间
  unsavedChanges: false, // 是否有未保存的更改

  // ========================================================================
  // 连接状态 / Connection State
  // ========================================================================
  connectionStatus: 'connected', // 'connected' | 'disconnected' | 'syncing'

  // ========================================================================
  // UI 主题与模式 / UI Theme & Mode
  // ========================================================================
  theme: 'light', // 'light' | 'dark'
  zenMode: false, // 禅模式：隐藏所有 UI 元素

  // ========================================================================
  // 对话框状态 / Dialog State
  // ========================================================================
  createChapterDialogOpen: false,
  selectedVolumeId: null,
};

/**
 * IDE 状态缩减器 / IDE State Reducer
 *
 * 处理所有 IDE 状态变更的中央位置。
 *
 * @param {Object} state - 当前状态
 * @param {Object} action - 状态变更动作
 * @returns {Object} 新的状态
 */
function ideReducer(state, action) {
  switch (action.type) {
    // 面板控制动作 / Panel Control Actions
    case 'SET_ACTIVE_PANEL':
      // 如果点击相同的面板，切换可见性 / If clicking the same panel, toggle visibility
      if (state.activeActivity === action.payload) {
        return { ...state, sidePanelVisible: !state.sidePanelVisible };
      }
      return { ...state, activeActivity: action.payload, sidePanelVisible: true };

    case 'TOGGLE_LEFT_PANEL':
      return { ...state, sidePanelVisible: !state.sidePanelVisible };

    case 'TOGGLE_RIGHT_PANEL':
      return { ...state, rightPanelVisible: !state.rightPanelVisible };

    case 'SET_PANEL_WIDTH':
      return { ...state, [action.panel === 'left' ? 'sidePanelWidth' : 'rightPanelWidth']: action.width };

    // 文档和项目状态 / Document and Project State
    case 'SET_ACTIVE_DOCUMENT':
      return { ...state, activeDocument: action.payload };

    case 'SET_PROJECT_ID':
      return { ...state, activeProjectId: action.payload };

    // 编辑器光标和文本状态 / Editor Cursor and Text State
    case 'SET_CURSOR_POSITION':
      return { ...state, cursorPosition: action.payload };

    case 'SET_WORD_COUNT':
      return { ...state, wordCount: action.payload };

    case 'SET_SELECTION_COUNT':
      return { ...state, selectionCount: action.payload };

    // 保存状态管理 / Save State Management
    case 'SET_SAVED':
      return { ...state, lastSavedAt: new Date(), lastAutosavedAt: null, unsavedChanges: false };

    case 'SET_AUTOSAVED':
      return { ...state, lastAutosavedAt: new Date(), unsavedChanges: false };

    case 'SET_UNSAVED':
      return { ...state, unsavedChanges: true };

    // 连接状态 / Connection Status
    case 'SET_CONNECTION_STATUS':
      return { ...state, connectionStatus: action.payload };

    // 对话框管理 / Dialog Management
    case 'OPEN_CREATE_CHAPTER_DIALOG':
      return {
        ...state,
        createChapterDialogOpen: true,
        selectedVolumeId: action.payload?.volumeId || state.selectedVolumeId,
      };

    case 'CLOSE_CREATE_CHAPTER_DIALOG':
      return { ...state, createChapterDialogOpen: false };

    case 'SET_SELECTED_VOLUME_ID':
      return { ...state, selectedVolumeId: action.payload };

    // 禅模式 / Zen Mode
    case 'TOGGLE_ZEN_MODE':
      return {
        ...state,
        zenMode: !state.zenMode,
        sidePanelVisible: state.zenMode, // Exit zen -> restore
        rightPanelVisible: state.zenMode,
      };

    default:
      return state;
  }
}

/**
 * IDEProvider - IDE 上下文提供者组件
 *
 * 为所有子组件提供 IDE 状态和分发函数。
 *
 * @component
 * @param {JSX.Element} children - 子组件
 * @param {string} [projectId] - 项目 ID
 * @returns {JSX.Element} 提供上下文的包装组件
 *
 * @example
 * <IDEProvider projectId="project-123">
 *   <App />
 * </IDEProvider>
 */
export function IDEProvider({ children, projectId }) {
  const [state, dispatch] = useReducer(ideReducer, {
    ...initialState,
    activeProjectId: projectId,
  });

  // 使用 useMemo 优化性能，避免不必要的上下文更新
  const value = useMemo(() => ({ state, dispatch }), [state]);

  return <IDEContext.Provider value={value}>{children}</IDEContext.Provider>;
}

/**
 * useIDE - IDE 上下文 Hook
 *
 * 在组件中访问 IDE 全局状态和分发函数。
 *
 * @returns {Object} { state, dispatch }
 * @throws {Error} 如果在 IDEProvider 外使用会抛出错误
 *
 * @example
 * const { state, dispatch } = useIDE();
 * dispatch({ type: 'TOGGLE_LEFT_PANEL' });
 */
export function useIDE() {
  const context = useContext(IDEContext);
  if (!context) throw new Error('useIDE must be used within IDEProvider');
  return context;
}

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

import React, { createContext, useContext, useReducer, useMemo, useEffect } from 'react';

import { closeTab, documentOf, renameTab, tabKeyOf, upsertTab } from '../lib/editorTabs';

const IDEContext = createContext(null);
const PANEL_LAYOUT_KEY = 'wenshape.workbench.layout.v1';

/**
 * 正文字号（像素）。与标签不同，字号是显示偏好而非会话内容，持久化到与面板布局同一个键，
 * 不另建第二套存储。
 */
export const EDITOR_FONT_SIZE = { min: 13, max: 26, step: 1, default: 16 };

export function clampEditorFontSize(value) {
  const size = Math.round(Number(value));
  if (!Number.isFinite(size)) return EDITOR_FONT_SIZE.default;
  return Math.min(EDITOR_FONT_SIZE.max, Math.max(EDITOR_FONT_SIZE.min, size));
}

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

  // 编辑器标签（会话内有效，刻意不持久化；模型见 lib/editorTabs.js）
  openTabs: [], // [{ key, type, id, title, touchedAt }]
  tabSeq: 0, // 标签访问序，单调递增，供 LRU 淘汰使用

  // 编辑器光标和选择状态 / Cursor and Selection
  cursorPosition: { line: 1, column: 1 }, // 光标位置
  editorFontSize: EDITOR_FONT_SIZE.default, // 正文字号（像素），持久化
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
    case 'SET_ACTIVE_DOCUMENT': {
      // 打开文档即打开标签：标签栏与 activeDocument 共用同一个真相源。
      if (!action.payload) return { ...state, activeDocument: null };
      const tabSeq = state.tabSeq + 1;
      return {
        ...state,
        activeDocument: action.payload,
        openTabs: upsertTab(state.openTabs, action.payload, tabSeq),
        tabSeq,
      };
    }

    // 只清空编辑区，保留标签（如卡片编辑器关闭）
    case 'CLEAR_ACTIVE_DOCUMENT':
      return { ...state, activeDocument: null };

    case 'CLOSE_TAB': {
      const { tabs, nextActive } = closeTab(state.openTabs, action.payload, tabKeyOf(state.activeDocument));
      if (tabs === state.openTabs) return state;
      return {
        ...state,
        openTabs: tabs,
        activeDocument: nextActive === undefined ? state.activeDocument : nextActive,
      };
    }

    case 'CLOSE_OTHER_TABS': {
      const kept = state.openTabs.filter((tab) => tab.key === action.payload);
      if (!kept.length || kept.length === state.openTabs.length) return state;
      const activeKey = tabKeyOf(state.activeDocument);
      return {
        ...state,
        openTabs: kept,
        activeDocument: activeKey === kept[0].key ? state.activeDocument : documentOf(kept[0]),
      };
    }

    case 'CLOSE_ALL_TABS':
      return { ...state, openTabs: [], activeDocument: null };

    // 重命名标签：只改标签栏显示，刻意不改 activeDocument。
    // activeDocument 变化会重新进入章节加载路径，重命名不该触发正文重载。
    case 'RENAME_TAB': {
      const tabs = renameTab(state.openTabs, action.payload?.key, action.payload?.title);
      return tabs === state.openTabs ? state : { ...state, openTabs: tabs };
    }

    case 'SET_PROJECT_ID':
      return { ...state, activeProjectId: action.payload };

    // 编辑器光标和文本状态 / Editor Cursor and Text State
    case 'SET_CURSOR_POSITION':
      return { ...state, cursorPosition: action.payload };

    case 'SET_EDITOR_FONT_SIZE': {
      const editorFontSize = clampEditorFontSize(action.payload);
      return editorFontSize === state.editorFontSize ? state : { ...state, editorFontSize };
    }

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
    ...readPersistedPanelLayout(),
    activeProjectId: projectId,
  });

  useEffect(() => {
    try {
      localStorage.setItem(
        PANEL_LAYOUT_KEY,
        JSON.stringify({
          sidePanelWidth: state.sidePanelWidth,
          rightPanelWidth: state.rightPanelWidth,
          sidePanelVisible: state.sidePanelVisible,
          rightPanelVisible: state.rightPanelVisible,
          editorFontSize: state.editorFontSize,
        }),
      );
    } catch (_error) {
      // Electron 隐私模式或禁用存储时保持内存状态，不阻断工作台。
    }
  }, [
    state.sidePanelWidth,
    state.rightPanelWidth,
    state.sidePanelVisible,
    state.rightPanelVisible,
    state.editorFontSize,
  ]);

  // 使用 useMemo 优化性能，避免不必要的上下文更新
  const value = useMemo(() => ({ state, dispatch }), [state]);

  return <IDEContext.Provider value={value}>{children}</IDEContext.Provider>;
}

function readPersistedPanelLayout() {
  if (typeof localStorage === 'undefined') return {};
  try {
    const value = JSON.parse(localStorage.getItem(PANEL_LAYOUT_KEY) || '{}');
    return {
      sidePanelWidth: Math.max(180, Math.min(420, Number(value.sidePanelWidth) || initialState.sidePanelWidth)),
      rightPanelWidth: Math.max(280, Math.min(620, Number(value.rightPanelWidth) || initialState.rightPanelWidth)),
      sidePanelVisible: value.sidePanelVisible !== false,
      rightPanelVisible: value.rightPanelVisible !== false,
      editorFontSize: clampEditorFontSize(value.editorFontSize ?? initialState.editorFontSize),
    };
  } catch (_error) {
    return {};
  }
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

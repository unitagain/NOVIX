/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 */

import React, { useEffect } from 'react';
import { useIDE } from '../../context/IDEContext';
import { ActivityBar } from './ActivityBar';
import { SidePanel } from './SidePanel';
import { StatusBar } from './StatusBar';
import { TitleBar } from './TitleBar';

/**
 * IDELayout - 写作 IDE 主布局组件
 *
 * 提供类似 VS Code 的三段式布局：顶部标题栏、中央编辑区+左侧面板+右侧面板、底部状态栏。
 * 支持禅模式（Zen Mode）隐藏 UI，支持键盘快捷键切换面板。
 *
 * @component
 * @param {JSX.Element} children - 中央编辑区内容
 * @param {JSX.Element} [rightPanelContent] - 右侧面板内容（可选）
 * @param {Object} [titleBarProps={}] - 传递给顶部栏的属性
 * @returns {JSX.Element} 完整的 IDE 布局
 *
 * 快捷键：
 * - Ctrl/Cmd + B: 切换左侧面板可见性
 * - Ctrl/Cmd + \\: 切换禅模式
 */
export function IDELayout({ children, rightPanelContent, titleBarProps = {} }) {
  const { state, dispatch } = useIDE();

  // ========================================================================
  // 键盘快捷键处理 / Keyboard Shortcuts
  // ========================================================================
  // Ctrl/Cmd+B: 切换左侧面板 / Toggle left panel
  // Ctrl/Cmd+\\: 切换禅模式 / Toggle zen mode
  useEffect(() => {
    const handleKeyDown = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'b') {
        e.preventDefault();
        dispatch({ type: 'TOGGLE_LEFT_PANEL' });
      }
      if ((e.metaKey || e.ctrlKey) && e.key === '\\') {
        e.preventDefault();
        dispatch({ type: 'TOGGLE_ZEN_MODE' });
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [dispatch]);

  return (
    <div className="anti-theme h-screen w-full flex flex-col bg-[var(--vscode-desktop)] text-[var(--vscode-fg)] overflow-hidden">
      {/* ========================================================================
                顶部标题栏 / Title Bar
                ======================================================================== */}
      {!state.zenMode && <TitleBar {...titleBarProps} />}

      {/* ========================================================================
                主体编辑区 / Main Editor Area
                ======================================================================== */}
      <div className="flex-1 flex overflow-hidden min-h-0">
        {/* 活动栏（固定左侧，属于底层边框、不做卡片） - Activity Bar (frame, flush) */}
        {!state.zenMode && <ActivityBar />}

        {/* 卡片区：三栏作为圆角卡片浮于底色之上，间隙露出底色作沟槽 */}
        <div className="flex-1 flex overflow-hidden min-w-0 gap-1 p-1">
          {/* 左侧面板（资源管理器卡片，可调整大小/可折叠） - Left Panel card */}
          {!state.zenMode && state.sidePanelVisible && <SidePanel />}

          {/* 中央编辑器卡片 - Central Editor card */}
          <main className="flex-1 overflow-y-auto min-w-0 bg-[var(--vscode-bg)] rounded-[8px] shadow-[0_1px_3px_rgba(2,6,23,0.07)]">
            {children}
          </main>

          {/* 右侧 AI 对话卡片 - Right Panel (AI Sidebar) card */}
          {!state.zenMode && state.rightPanelVisible && (
            <div
              className="flex-shrink-0 bg-[var(--vscode-bg)] overflow-hidden flex flex-col rounded-[8px] shadow-[0_1px_3px_rgba(2,6,23,0.07)]"
              style={{ width: state.rightPanelWidth }}
            >
              {rightPanelContent}
            </div>
          )}
        </div>
      </div>

      {/* ========================================================================
                底部状态栏 / Status Bar
                ======================================================================== */}
      <StatusBar />
    </div>
  );
}

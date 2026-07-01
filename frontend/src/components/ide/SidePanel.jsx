/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 */

import React from 'react';
import { useIDE } from '../../context/IDEContext';
import ExplorerPanel from './panels/ExplorerPanel';
import CardsPanel from './panels/CardsPanel';
import AgentsPanel from './panels/AgentsPanel';
import FanfictionPanel from './panels/FanfictionPanel';
import FactsEncyclopedia from './FactsEncyclopedia';

/**
 * SidePanel - 左侧侧栏容器
 *
 * 根据 IDE 状态显示不同的功能面板：资源管理器、卡片管理、智能体等。
 * 支持宽度拖拽调整、面板切换和滚动。
 *
 * @component
 * @returns {JSX.Element|null} 侧栏容器或 null（如果隐藏）
 *
 * 特性：
 * - 根据活跃活动类型动态加载面板
 * - 可拖拽边界调整宽度
 * - 响应式宽度约束（最小 160px，最大 600px）
 */
export const SidePanel = () => {
  const { state, dispatch } = useIDE();
  const { sidePanelVisible, activeActivity, sidePanelWidth } = state;

  if (!sidePanelVisible) return null;

  return (
    <div
      className="h-full rounded-[8px] overflow-hidden shadow-[0_1px_3px_rgba(2,6,23,0.07)] bg-[var(--vscode-sidebar-bg)] flex flex-col relative group"
      style={{ width: sidePanelWidth, minWidth: 160, maxWidth: 600 }}
    >
      {/* ========================================================================
          面板内容容器 / Panel Content Container
          ======================================================================== */}
      <div className="flex-1 overflow-hidden h-full flex flex-col">
        <div className="flex-1 overflow-hidden min-h-0 relative">
          {/* 资源管理器面板 - Explorer Panel */}
          {activeActivity === 'explorer' && <ExplorerPanel />}

          {/* 事实全典面板 - Facts Encyclopedia Panel */}
          {activeActivity === 'facts' && (
            <div className="h-full overflow-hidden">
              <FactsEncyclopedia />
            </div>
          )}

          {/* 卡片管理面板 - Cards Panel */}
          {activeActivity === 'cards' && <CardsPanel />}

          {/* 智能体配置面板 - Agents Configuration Panel */}
          {activeActivity === 'agents' && <AgentsPanel mode="config" />}

          {/* 同人导入面板 - Fanfiction Import Panel */}
          {activeActivity === 'fanfiction' && <FanfictionPanel />}
        </div>
      </div>

      {/* ========================================================================
          宽度调整拖拽条 / Width Resize Handle
          ======================================================================== */}
      <div
        className="absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-primary/30 transition-colors z-50"
        onMouseDown={(e) => {
          e.preventDefault();
          const startX = e.pageX;
          const startWidth = sidePanelWidth;

          // 鼠标移动时调整宽度 / Handle mouse move to resize
          const handleMouseMove = (moveEvent) => {
            const newWidth = Math.max(160, Math.min(600, startWidth + (moveEvent.pageX - startX)));
            dispatch({ type: 'SET_PANEL_WIDTH', payload: newWidth });
          };

          // 鼠标抬起时停止调整 / Handle mouse up to stop resizing
          const handleMouseUp = () => {
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
          };

          document.addEventListener('mousemove', handleMouseMove);
          document.addEventListener('mouseup', handleMouseUp);
        }}
      />
    </div>
  );
};

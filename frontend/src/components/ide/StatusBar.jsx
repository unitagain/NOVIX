/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 */

import React from 'react';
import { useIDE } from '../../context/IDEContext';
import { Folder, Save, AlertCircle, FileText, Bell } from 'lucide-react';
import { useLocale } from '../../i18n';

/**
 * StatusBar - IDE 底部状态栏
 *
 * 显示当前项目信息、文件保存状态、字数统计和光标位置。
 * 在禅模式下隐藏。
 *
 * @component
 * @returns {JSX.Element|null} 状态栏或 null（禅模式下）
 *
 * 显示内容：
 * - 左侧：项目 ID、保存状态（已保存/未保存/自动保存）
 * - 右侧：总字数、选中字数、光标位置（行:列）
 */
export function StatusBar() {
  const { t } = useLocale();
  const { state } = useIDE();
  const {
    activeProjectId,
    wordCount,
    selectionCount,
    cursorPosition,
    lastSavedAt,
    lastAutosavedAt,
    unsavedChanges,
    zenMode,
  } = state;

  /**
   * 格式化时间显示
   * Format time for display (HH:MM)
   *
   * @param {Date|null} date - 日期对象
   * @returns {string} 格式化的时间字符串
   */
  const formatTime = (date) => {
    if (!date) return '--:--';
    return new Date(date).toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  if (zenMode) return null;

  return (
    <div className="h-7 min-h-[28px] bg-[var(--vscode-desktop)] text-[var(--vscode-fg-subtle)] flex items-center justify-between px-2 text-[11px] select-none flex-shrink-0 z-50">
      {/* ========================================================================
                左侧：项目和保存状态 / Left Section: Project & Save Status
                ======================================================================== */}
      <div className="flex items-center gap-1">
        {activeProjectId && (
          <button className="flex items-center gap-1.5 px-2 h-full hover:bg-[var(--vscode-list-hover)] rounded-[6px] transition-colors">
            <Folder size={12} className="text-[var(--vscode-fg-subtle)]" />
            <span className="max-w-[120px] truncate">{activeProjectId}</span>
          </button>
        )}

        {/* 保存状态指示器 - Save Status Indicator */}
        <button className="flex items-center gap-1.5 px-2 h-full hover:bg-[var(--vscode-list-hover)] rounded-[6px] transition-colors">
          {unsavedChanges ? (
            <>
              <AlertCircle size={12} className="text-amber-600" />
              <span className="text-[var(--vscode-fg)]">{t('writingSession.unsavedChanges')}</span>
            </>
          ) : lastSavedAt ? (
            <>
              <Save size={12} className="text-emerald-600" />
              <span className="text-[var(--vscode-fg)]">
                {t('writingSession.saveSuccess')} {formatTime(lastSavedAt)}
              </span>
            </>
          ) : lastAutosavedAt ? (
            <>
              <Save size={12} className="text-emerald-600" />
              <span className="text-[var(--vscode-fg)]">
                {t('writingSession.saveSuccess')} {formatTime(lastAutosavedAt)}
              </span>
            </>
          ) : (
            <>
              <Save size={12} className="text-[var(--vscode-fg-subtle)]" />
              <span className="text-[var(--vscode-fg-subtle)]">--:--</span>
            </>
          )}
        </button>
      </div>

      {/* ========================================================================
                右侧：字数和光标位置 / Right Section: Word Count & Cursor Position
                ======================================================================== */}
      <div className="flex items-center gap-1">
        {/* 字数统计 - Word Count */}
        <button className="flex items-center gap-1.5 px-2 h-full hover:bg-[var(--vscode-list-hover)] rounded-[6px] transition-colors">
          <FileText size={12} className="text-[var(--vscode-fg-subtle)]" />
          <span className="text-[var(--vscode-fg)]">
            {wordCount.toLocaleString()} {t('chapter.wordCount')}
          </span>
          {selectionCount > 0 && (
            <span className="text-[var(--vscode-fg-subtle)]">
              （{t('writingSession.wordCount').replace('{count}', selectionCount.toLocaleString())}）
            </span>
          )}
        </button>

        {/* 光标位置 - Cursor Position */}
        <button className="flex items-center gap-1.5 px-2 h-full hover:bg-[var(--vscode-list-hover)] rounded-[6px] transition-colors font-mono">
          <span className="text-[var(--vscode-fg-subtle)]">
            Ln {cursorPosition.line}, Col {cursorPosition.column}
          </span>
        </button>

        {/* 通知按钮 - Notification Button */}
        <button className="flex items-center gap-1.5 px-2 h-full hover:bg-[var(--vscode-list-hover)] rounded-[6px] transition-colors">
          <Bell size={12} className="text-[var(--vscode-fg-subtle)]" />
        </button>
      </div>
    </div>
  );
}

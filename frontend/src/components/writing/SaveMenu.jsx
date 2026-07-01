/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 */

import { useEffect, useRef, useState } from 'react';
import { Save, ChevronDown, Sparkles } from 'lucide-react';
import { cn } from '../ui/core';
import { useLocale } from '../../i18n';

/**
 * 保存菜单组件 - 提供保存与分析保存的快捷入口
 *
 * Dropdown menu providing save-only and analyze-then-save actions.
 * Handles click-outside detection for auto-closing and disabled states.
 *
 * @component
 * @example
 * return (
 *   <SaveMenu
 *     disabled={false}
 *     busy={false}
 *     onSaveOnly={handleSaveOnly}
 *     onAnalyzeSave={handleAnalyzeSave}
 *   />
 * )
 *
 * @param {Object} props - Component props
 * @param {boolean} [props.disabled=false] - 禁用菜单 / Whether the menu is disabled
 * @param {boolean} [props.busy=false] - 是否正在保存中 / Whether save operation is in progress
 * @param {Function} [props.onSaveOnly] - 仅保存回调 / Callback for save-only action
 * @param {Function} [props.onAnalyzeSave] - 分析并保存回调 / Callback for analyze-then-save action
 * @returns {JSX.Element} 保存菜单元素 / Save menu element
 */
export default function SaveMenu({ disabled = false, busy = false, onSaveOnly, onAnalyzeSave }) {
  const { t } = useLocale();
  const [open, setOpen] = useState(false);
  const menuRef = useRef(null);

  // ========================================================================
  // 点击外部关闭菜单 / Handle click-outside to close menu
  // ========================================================================
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleToggle = () => {
    if (disabled) return;
    setOpen((prev) => !prev);
  };

  const handleAction = (action) => {
    setOpen(false);
    if (action === 'save' && onSaveOnly) {
      onSaveOnly();
    }
    if (action === 'analyze' && onAnalyzeSave) {
      onAnalyzeSave();
    }
  };

  return (
    <div ref={menuRef} className="relative">
      {/*
              ======================================================================
              菜单触发按钮 / Menu toggle button
              ======================================================================
              显示保存状态和菜单展开箭头
              Shows save status and dropdown arrow
            */}
      <button
        onClick={handleToggle}
        disabled={disabled}
        className={cn(
          'flex items-center gap-2 px-3 py-1.5 rounded-[6px] text-sm transition-colors',
          disabled
            ? 'bg-[var(--vscode-list-hover)] text-[var(--vscode-fg-subtle)]'
            : 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] hover:opacity-90',
        )}
        title={t('common.save')}
      >
        <Save size={14} />
        <span>{busy ? t('common.processing') : t('common.save')}</span>
        <ChevronDown size={12} className={cn('transition-transform', open && 'rotate-180')} />
      </button>

      {/*
              ======================================================================
              下拉菜单内容 / Dropdown menu content
              ======================================================================
              提供两个操作选项：分析并保存、仅保存
              Provides two action options: analyze-then-save, save-only
            */}
      {open && !disabled && (
        <div className="absolute right-0 top-full mt-2 w-44 glass-panel border border-[var(--vscode-sidebar-border)] rounded-[6px] py-1 z-50 soft-dropdown">
          <button
            onClick={() => handleAction('analyze')}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
          >
            <Sparkles size={14} className="text-[var(--vscode-focus-border)]" />
            <span>{t('writing.saveMenuAnalyze')}</span>
          </button>
          <button
            onClick={() => handleAction('save')}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
          >
            <Save size={14} className="text-[var(--vscode-fg-subtle)]" />
            <span>{t('writing.saveMenuSave')}</span>
          </button>
        </div>
      )}
    </div>
  );
}

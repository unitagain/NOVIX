/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ChevronDown, FileText, Globe, ListTree, Minus, Plus, User, X } from 'lucide-react';

import { EDITOR_FONT_SIZE } from '../../context/IDEContext';
import { cn } from '../ui/core';

const TYPE_ICONS = { chapter: FileText, outline: ListTree, character: User, world: Globe };

/**
 * EditorTabs - 编辑器标签栏（会话内有效，退出不保留）
 *
 * 与中央写作区左右对齐、紧贴其顶边，取代原先的静态面包屑与章节标题栏。
 * 标签宽度先收缩到阈值，仍放不下则横向滚动，并由「⌄」下拉提供全量入口。
 *
 * 状态角标是必需的而非装饰：标签让切章变廉价，用户会在 AI 正写着 A 章时切到 B 章，
 * 必须让「哪个标签正在被写入 / 有待审修订 / 未保存」一眼可见。
 *
 * 章节标题栏移除后，双击当前章节标签就是唯一的改名入口（提交仍走编辑区自动保存）。
 */
export default function EditorTabs({
  tabs = [],
  activeKey,
  unsavedKey,
  streamingKey,
  diffKey,
  fontSize,
  showFontSize = false,
  onFontSizeChange,
  onSelect,
  onClose,
  onCloseOthers,
  onRename,
  t,
}) {
  const stripRef = useRef(null);
  const activeRef = useRef(null);
  const menuRef = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const [renaming, setRenaming] = useState(null); // { key, draft }

  const label = useCallback((key, fallback) => (t ? t(key) || fallback : fallback), [t]);

  // 溢出探测：决定是否显示「⌄」全量下拉。
  useEffect(() => {
    const el = stripRef.current;
    if (!el) return undefined;
    const update = () => setOverflowing(el.scrollWidth > el.clientWidth + 1);
    update();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [tabs]);

  // 激活的标签滚出视野时拉回（从侧边栏点章节时常见）。
  useLayoutEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [activeKey]);

  // 标签被关闭或切走时退出改名态，避免输入框悬空。
  useEffect(() => {
    if (renaming && renaming.key !== activeKey) setRenaming(null);
  }, [activeKey, renaming]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onPointerDown = (event) => {
      if (!menuRef.current?.contains(event.target)) setMenuOpen(false);
    };
    const onKeyDown = (event) => {
      if (event.key === 'Escape') setMenuOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [menuOpen]);

  if (!tabs.length) return null;

  const displayTitle = (tab) => tab.title || (tab.type === 'outline' ? label('outline.title', '大纲') : tab.id);

  const statusOf = (tab) => {
    if (tab.key === streamingKey)
      return { tone: 'bg-[var(--vscode-focus-border)] animate-pulse', hint: label('tabs.writing', 'AI 正在写入') };
    if (tab.key === diffKey) return { tone: 'bg-amber-500', hint: label('tabs.pendingDiff', '有待审修订') };
    if (tab.key === unsavedKey) return { tone: 'bg-[var(--vscode-fg-subtle)]', hint: label('tabs.unsaved', '未保存') };
    return null;
  };

  // 只允许改当前章节：标题写回依赖编辑区里那一章的 chapterInfo，非当前标签没有可写的缓冲。
  const canRename = (tab) => Boolean(onRename) && tab.type === 'chapter' && tab.key === activeKey;

  const commitRename = () => {
    if (!renaming) return;
    onRename?.(renaming.key, renaming.draft);
    setRenaming(null);
  };

  const showOverflowMenu = overflowing || tabs.length > 1;

  return (
    <div className="flex h-9 shrink-0 items-stretch rounded-t-[8px] border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-bg)]">
      <div ref={stripRef} className="tabs-strip flex min-w-0 flex-1 items-stretch overflow-x-auto rounded-tl-[8px]">
        {tabs.map((tab) => {
          const Icon = TYPE_ICONS[tab.type] || FileText;
          const isActive = tab.key === activeKey;
          const status = statusOf(tab);
          const isRenaming = renaming?.key === tab.key;
          return (
            <div
              key={tab.key}
              ref={isActive ? activeRef : null}
              role="tab"
              aria-selected={isActive}
              tabIndex={0}
              title={isRenaming ? undefined : displayTitle(tab)}
              onClick={() => onSelect?.(tab)}
              onDoubleClick={() => {
                if (canRename(tab)) setRenaming({ key: tab.key, draft: tab.title || '' });
              }}
              onKeyDown={(event) => {
                if (isRenaming) return;
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onSelect?.(tab);
                }
                if (event.key === 'F2' && canRename(tab)) {
                  event.preventDefault();
                  setRenaming({ key: tab.key, draft: tab.title || '' });
                }
              }}
              // 中键关闭：IDE 通用手势
              onAuxClick={(event) => {
                if (event.button === 1) {
                  event.preventDefault();
                  onClose?.(tab);
                }
              }}
              onContextMenu={(event) => {
                event.preventDefault();
                onCloseOthers?.(tab);
              }}
              className={cn(
                'group relative flex min-w-[96px] max-w-[190px] shrink cursor-pointer items-center gap-1.5 border-r border-[var(--vscode-sidebar-border)] px-2.5 text-[12px] transition-colors',
                isRenaming && 'min-w-[150px]',
                isActive
                  ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
                  : 'text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)]',
              )}
            >
              <Icon size={13} className="shrink-0" />
              {isRenaming ? (
                <input
                  autoFocus
                  value={renaming.draft}
                  aria-label={label('tabs.rename', '重命名')}
                  onChange={(event) => setRenaming({ key: tab.key, draft: event.target.value })}
                  onClick={(event) => event.stopPropagation()}
                  onDoubleClick={(event) => event.stopPropagation()}
                  onBlur={commitRename}
                  onKeyDown={(event) => {
                    event.stopPropagation();
                    if (event.key === 'Enter') commitRename();
                    if (event.key === 'Escape') setRenaming(null);
                  }}
                  className="min-w-0 flex-1 rounded-[3px] bg-[var(--vscode-input-bg)] px-1 text-[12px] text-[var(--vscode-fg)] outline-none ring-1 ring-[var(--vscode-focus-border)]"
                />
              ) : (
                <span className="min-w-0 flex-1 truncate">{displayTitle(tab)}</span>
              )}
              {status && !isRenaming ? (
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${status.tone} group-hover:hidden`}
                  title={status.hint}
                />
              ) : null}
              {isRenaming ? null : (
                <button
                  type="button"
                  title={label('tabs.close', '关闭')}
                  aria-label={`${label('tabs.close', '关闭')} ${displayTitle(tab)}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    onClose?.(tab);
                  }}
                  className={cn(
                    'shrink-0 rounded-[4px] p-0.5 hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]',
                    // 有状态角标时：默认让位给角标，悬停才露出关闭键（与主流 IDE 一致）
                    status ? 'hidden group-hover:block' : 'opacity-0 group-hover:opacity-100',
                    !status && isActive ? 'opacity-100' : '',
                  )}
                >
                  <X size={12} />
                </button>
              )}
            </div>
          );
        })}
      </div>

      {showFontSize ? (
        <div className="flex shrink-0 items-center gap-0.5 border-l border-[var(--vscode-sidebar-border)] px-1">
          <button
            type="button"
            title={label('tabs.fontSmaller', '缩小正文字号')}
            aria-label={label('tabs.fontSmaller', '缩小正文字号')}
            disabled={fontSize <= EDITOR_FONT_SIZE.min}
            onClick={() => onFontSizeChange?.(-EDITOR_FONT_SIZE.step)}
            className="rounded-[4px] p-1 text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)] disabled:cursor-not-allowed disabled:opacity-35"
          >
            <Minus size={13} />
          </button>
          <span
            className="w-5 select-none text-center text-[10px] tabular-nums text-[var(--vscode-fg-subtle)]"
            title={label('tabs.fontSize', '正文字号')}
          >
            {fontSize}
          </span>
          <button
            type="button"
            title={label('tabs.fontLarger', '放大正文字号')}
            aria-label={label('tabs.fontLarger', '放大正文字号')}
            disabled={fontSize >= EDITOR_FONT_SIZE.max}
            onClick={() => onFontSizeChange?.(EDITOR_FONT_SIZE.step)}
            className={cn(
              'rounded-[4px] p-1 text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)] disabled:cursor-not-allowed disabled:opacity-35',
              !showOverflowMenu && 'rounded-tr-[8px]',
            )}
          >
            <Plus size={13} />
          </button>
        </div>
      ) : null}

      {showOverflowMenu ? (
        <div
          ref={menuRef}
          className="relative flex shrink-0 items-center border-l border-[var(--vscode-sidebar-border)]"
        >
          <button
            type="button"
            title={label('tabs.overflow', '所有已打开')}
            onClick={() => setMenuOpen((open) => !open)}
            className="flex h-full items-center gap-0.5 rounded-tr-[8px] px-2 text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]"
          >
            <ChevronDown size={14} />
            {overflowing ? <span className="text-[10px] font-medium tabular-nums">{tabs.length}</span> : null}
          </button>
          {menuOpen ? (
            <div className="absolute right-0 top-full z-50 mt-0.5 max-h-[60vh] w-60 overflow-y-auto rounded-[6px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)] py-1 shadow-lg">
              {tabs.map((tab) => {
                const Icon = TYPE_ICONS[tab.type] || FileText;
                return (
                  <button
                    key={tab.key}
                    type="button"
                    onClick={() => {
                      setMenuOpen(false);
                      onSelect?.(tab);
                    }}
                    className={cn(
                      'flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12px]',
                      tab.key === activeKey
                        ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
                        : 'text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)]',
                    )}
                  >
                    <Icon size={13} className="shrink-0 text-[var(--vscode-fg-subtle)]" />
                    <span className="min-w-0 flex-1 truncate">{displayTitle(tab)}</span>
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

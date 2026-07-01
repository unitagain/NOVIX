/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   流式差异视图 - 写作生成期间，把逐 token 增长的新文本呈现为 diff 绿块，
 *   既保留"看着 AI 写作"的体验，底层本质又是一次差异提议（生成结束后转入
 *   DiffReviewView 审阅采纳）。统一了"写全章"与"局部编辑"为同一种差异交互。
 *   Streaming diff view: renders the growing draft as a live "add" hunk so writing a
 *   full chapter still feels like real-time writing, while staying a diff proposal
 *   under the hood (handed to DiffReviewView for accept/reject once streaming ends).
 *
 * @component
 * @param {Object} props
 * @param {string} [props.originalContent=''] - 流式前的原正文（续写/重写时为上下文，写全章时为空）
 * @param {string} [props.content=''] - 流式增长中的新文本 / Growing draft text
 * @param {boolean} [props.active=false] - 是否生成中（显示闪烁光标）/ Whether generation is in progress
 * @param {string} [props.className='']
 * @returns {JSX.Element}
 */
import React from 'react';
import { Plus } from 'lucide-react';
import { cn } from '../ui/core';
import { useLocale } from '../../i18n';

const renderLine = (line) => (line === '' ? ' ' : line);

const StreamingDiffView = ({ originalContent = '', content = '', active = false, className = '' }) => {
  const { t } = useLocale();
  const original = String(originalContent || '');
  const draft = String(content || '');
  const originalLines = original ? original.replace(/\r\n/g, '\n').split('\n') : [];

  return (
    <div
      className={cn(
        'flex flex-col h-full bg-[var(--vscode-bg)] rounded-[6px] border border-[var(--vscode-sidebar-border)] overflow-hidden',
        className,
      )}
    >
      {/* 头部：与 DiffReviewView 同构的统计栏，让"写"与"改"视觉一致 */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)]">
        <div className="flex items-center gap-4">
          <span className="text-xs font-bold text-[var(--vscode-fg)]">{t('diff.streamingTitle')}</span>
          <span className="flex items-center gap-1 text-green-600 text-[10px]">
            <Plus size={12} />
            <span className="font-mono">
              {draft.length} {t('diff.streamingChars')}
            </span>
          </span>
        </div>
        {active ? (
          <span className="flex items-center gap-1.5 text-[10px] text-[var(--vscode-fg-subtle)]">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
            {t('diff.streamingStatus')}
          </span>
        ) : null}
      </div>

      {/* 内容区：原文（灰 context）在上，新文（绿 add 块，流式增长）在下 */}
      <div className="flex-1 overflow-y-auto editor-scrollbar p-6">
        <div className="font-serif text-base leading-relaxed text-[var(--vscode-fg)] space-y-0.5">
          {originalLines.length > 0
            ? originalLines.map((line, idx) => (
                <div
                  key={`ctx-${idx}`}
                  className="leading-loose whitespace-pre-wrap break-words text-[var(--vscode-fg-subtle)]"
                >
                  {renderLine(line)}
                </div>
              ))
            : null}
          <div className="my-2 rounded-[6px] border border-green-100 bg-green-50/60 p-3">
            <p className="whitespace-pre-wrap break-words text-green-800">
              {draft}
              {active ? (
                <span className="inline-block w-2 h-4 bg-green-500/80 ml-0.5 align-middle animate-pulse" />
              ) : null}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default StreamingDiffView;

/**
 * ToolPart —— 一次工具调用（视觉层级：弱）。
 *
 * 默认渲染可见单行 header：{图标} {工具名} · {关键参数} {状态徽章} {耗时}。
 * 折叠的只是 detail，不是调用本身（对齐业内 "every tool call belongs in the
 * transcript with arguments and results visible by default"）。
 * succeeded 默认折叠；failed/timed_out 默认展开。
 */
import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronRight, Check, X, Clock, Ban, Loader2, Wrench } from 'lucide-react';
import { useLocale } from '../../../i18n';
import { toolArgEntries, toolArgSummary, toolDisplayName, toolStatusLabel } from './toolLabels';

const STATUS_ICONS = {
  succeeded: Check,
  failed: X,
  timed_out: Clock,
  cancelled: Ban,
};

const STATUS_CLASS = {
  succeeded: 'text-[var(--vscode-fg-subtle)]',
  failed: 'text-red-600',
  timed_out: 'text-red-600',
  cancelled: 'text-[var(--vscode-fg-subtle)]',
};

/** 失败态必须自曝：不展开等于把错误藏起来。 */
export const defaultToolOpen = (part) => part?.status === 'failed' || part?.status === 'timed_out';

const StatusBadge = ({ status, errorCode }) => {
  const { t } = useLocale();
  if (status === 'running' || !status) {
    return (
      <span className="inline-flex items-center gap-1 text-[var(--vscode-focus-border)]">
        <Loader2 size={11} className="animate-spin" />
        <span>{toolStatusLabel('running', t)}</span>
      </span>
    );
  }
  const Icon = STATUS_ICONS[status] || Check;
  const isBad = status === 'failed' || status === 'timed_out';
  return (
    <span className={`inline-flex items-center gap-1 ${STATUS_CLASS[status] || ''}`}>
      <Icon size={11} />
      {isBad ? <span>{errorCode || toolStatusLabel(status, t)}</span> : null}
      {status === 'cancelled' ? <span>{toolStatusLabel(status, t)}</span> : null}
    </span>
  );
};

export const ToolPart = ({ part, isOpen, onToggle }) => {
  const { t } = useLocale();
  const name = toolDisplayName(part.name, t);
  const argSummary = toolArgSummary(part.name, part.args, t);
  const entries = toolArgEntries(part.args);
  const preview = String(part.preview || '');
  const hasDetail = entries.length > 0 || Boolean(preview.trim());

  return (
    <div className="text-[12px]">
      <button
        type="button"
        onClick={hasDetail ? onToggle : undefined}
        aria-expanded={hasDetail ? isOpen : undefined}
        className={[
          'w-full flex items-center gap-1.5 text-left rounded-[6px] px-1 py-0.5 -mx-1',
          hasDetail ? 'hover:bg-[var(--vscode-list-hover)] cursor-pointer' : 'cursor-default',
        ].join(' ')}
      >
        {hasDetail ? (
          <motion.span
            animate={{ rotate: isOpen ? 90 : 0 }}
            transition={{ duration: 0.15 }}
            className="shrink-0 text-[var(--vscode-fg-subtle)]"
          >
            <ChevronRight size={13} />
          </motion.span>
        ) : (
          <span className="w-[13px] shrink-0" />
        )}
        <Wrench size={12} className="shrink-0 text-[var(--vscode-fg-subtle)]" />
        <span className="text-[var(--vscode-fg)] shrink-0">{name}</span>
        {argSummary ? (
          <span className="min-w-0 truncate text-[var(--vscode-fg-subtle)] font-mono">{argSummary}</span>
        ) : null}
        <span className="ml-auto flex shrink-0 items-center gap-2 text-[11px]">
          <StatusBadge status={part.status} errorCode={part.errorCode} />
          {part.elapsedMs ? (
            <span className="font-mono text-[var(--vscode-fg-subtle)]">
              {t('agentPanel.toolElapsed').replace('{n}', part.elapsedMs)}
            </span>
          ) : null}
        </span>
      </button>
      <AnimatePresence initial={false}>
        {hasDetail && isOpen ? (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden"
          >
            <div className="ml-[22px] mt-0.5 mb-1 space-y-1 border-l border-[var(--vscode-sidebar-border)] pl-2 text-[11px] text-[var(--vscode-fg-subtle)]">
              {entries.length ? (
                <div>
                  <div className="text-[10px] uppercase tracking-wide opacity-70">{t('agentPanel.toolArguments')}</div>
                  <dl className="mt-0.5 space-y-0.5 font-mono">
                    {entries.map(([key, value]) => (
                      <div key={key} className="flex gap-2">
                        <dt className="shrink-0 opacity-70">{key}</dt>
                        <dd className="min-w-0 break-words whitespace-pre-wrap">{value}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ) : null}
              {preview.trim() ? (
                <div>
                  <div className="text-[10px] uppercase tracking-wide opacity-70">{t('agentPanel.toolOutput')}</div>
                  <div className="mt-0.5 whitespace-pre-wrap break-words font-mono">{preview}</div>
                </div>
              ) : null}
              {part.recoverable ? (
                <div className="text-[10px] opacity-70">{t('agentPanel.toolRecoverable')}</div>
              ) : null}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
};

export default ToolPart;

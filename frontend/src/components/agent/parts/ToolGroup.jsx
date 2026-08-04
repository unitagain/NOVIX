/**
 * ToolGroup —— 连续多次工具调用的渲染层编组（不是 part 类型，见 plan.md §9.3）。
 *
 * 组头为语义摘要行而非图标入口：默认展开列出各行；仅当组内 ≥6 次且全部成功时
 * 默认折叠（Trae 式已完成节点折叠）。单次调用不编组，直接渲染 ToolPart。
 */
import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronRight } from 'lucide-react';
import { useLocale } from '../../../i18n';
import { ToolPart } from './ToolPart';

export const TOOL_GROUP_COLLAPSE_THRESHOLD = 6;

const failedCount = (parts) =>
  parts.filter((part) => part.status === 'failed' || part.status === 'timed_out').length;

/** 组内全部成功且调用较多时才默认折叠——有失败必须一眼可见。 */
export const defaultGroupOpen = (parts) =>
  !(parts.length >= TOOL_GROUP_COLLAPSE_THRESHOLD && failedCount(parts) === 0);

export const groupSummary = (parts, t) => {
  const failed = failedCount(parts);
  if (failed > 0) {
    return t('agentPanel.toolGroupWithFailures')
      .replace('{n}', parts.length)
      .replace('{failed}', failed);
  }
  const allDone = parts.every((part) => part.status === 'succeeded');
  const key = allDone ? 'agentPanel.toolGroupAllOk' : 'agentPanel.toolGroupSummary';
  return t(key).replace('{n}', parts.length);
};

export const ToolGroup = ({ parts, isOpen, onToggle, expandedSteps, onToggleStep, defaultStepOpen }) => {
  const { t } = useLocale();
  const summary = groupSummary(parts, t);

  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        className="flex w-full items-center gap-1.5 rounded-[6px] px-1 py-0.5 -mx-1 text-left text-[12px] text-[var(--vscode-fg-subtle)] transition-colors hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]"
      >
        <motion.span animate={{ rotate: isOpen ? 90 : 0 }} transition={{ duration: 0.15 }} className="shrink-0">
          <ChevronRight size={13} />
        </motion.span>
        <span>{summary}</span>
      </button>
      <AnimatePresence initial={false}>
        {isOpen ? (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden"
          >
            <div className="mt-1 space-y-1 border-l border-[var(--vscode-sidebar-border)] pl-2">
              {parts.map((part) => {
                const open =
                  expandedSteps[part.id] !== undefined ? expandedSteps[part.id] : defaultStepOpen(part);
                return (
                  <ToolPart key={part.id} part={part} isOpen={open} onToggle={() => onToggleStep(part.id, open)} />
                );
              })}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
};

export default ToolGroup;

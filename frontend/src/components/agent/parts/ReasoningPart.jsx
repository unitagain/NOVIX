/**
 * ReasoningPart —— 思考块（视觉层级：最弱）。
 *
 * 流式中自动展开、结束自动收起（对齐 AI SDK `Reasoning`）；用户手动 toggle 后以
 * 用户状态为准（expandedSteps 显式记录 true/false）。同一轮可出现多个，按时序排列。
 */
import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronRight } from 'lucide-react';
import { useLocale } from '../../../i18n';

/** 思考块 trigger 文案：流式中 / 已完成秒数 / 不足 1 秒。纯函数，便于回归。 */
export const durationLabel = (part, t) => {
  if (part?.streaming) return t('agentPanel.thinking');
  const seconds = Math.round(((part?.endedAt || 0) - (part?.startedAt || 0)) / 1000);
  if (seconds < 1) return t('agentPanel.thinkingDone');
  return t('agentPanel.thinkingSeconds').replace('{n}', seconds);
};

export const ReasoningPart = ({ part, isOpen, onToggle }) => {
  const { t } = useLocale();
  const text = String(part.text || '');
  const hasDetail = Boolean(text.trim());

  return (
    <div>
      <button
        type="button"
        onClick={hasDetail ? onToggle : undefined}
        aria-expanded={hasDetail ? isOpen : undefined}
        className={[
          'inline-flex items-center gap-1 px-2 py-1 rounded-[6px] text-[12px] bg-[var(--vscode-list-hover)] text-[var(--vscode-fg-subtle)] transition-colors',
          hasDetail ? 'hover:text-[var(--vscode-fg)] cursor-pointer' : 'cursor-default',
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
        ) : null}
        <span>{durationLabel(part, t)}</span>
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
            <div className="mt-1.5 text-xs leading-relaxed text-[var(--vscode-fg-subtle)] whitespace-pre-wrap break-words">
              {text}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
};

export default ReasoningPart;

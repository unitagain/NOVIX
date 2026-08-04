/**
 * AgentTranscript —— 对话流（live transcript）：轮列表 + part 分发。
 *
 * 消费 `lib/agentThread.js` 产出的 5 类 part（该文件是 part 模型唯一 owner）；
 * 连续 tool part 在此编组为 ToolGroup —— 编组是渲染层结果，不是 part 类型。
 */
import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Bot, Loader2, User, RotateCcw } from 'lucide-react';
import { useLocale } from '../../i18n';
import { buildAgentParts } from '../../lib/agentThread';
import { ReasoningPart } from './parts/ReasoningPart';
import { ToolPart, defaultToolOpen } from './parts/ToolPart';
import { ToolGroup, defaultGroupOpen } from './parts/ToolGroup';
import { AnswerPart, MetaPart, NarrationPart } from './parts/TextParts';

/** 相邻 tool part 编组；其余 part 原样保留时序。 */
export const groupToolParts = (parts = []) => {
  const grouped = [];
  let buffer = [];
  const flush = () => {
    if (!buffer.length) return;
    if (buffer.length === 1) grouped.push(buffer[0]);
    else grouped.push({ kind: 'tool-group', id: `group-${buffer[0].id}`, parts: buffer });
    buffer = [];
  };
  parts.forEach((part) => {
    if (part.kind === 'tool') {
      buffer.push(part);
      return;
    }
    flush();
    grouped.push(part);
  });
  flush();
  return grouped;
};

const TurnParts = ({ parts, active, expandedSteps, onToggleStep }) => {
  const resolved = groupToolParts(parts);
  // 最后一个 reasoning 在生成中视为流式：自动展开，结束后自动收起。
  const streamingId = active
    ? [...parts].reverse().find((part) => part.kind === 'reasoning')?.id
    : undefined;

  const openState = (id, fallback) => (expandedSteps[id] !== undefined ? expandedSteps[id] : fallback);

  return resolved.map((part) => {
    if (part.kind === 'reasoning') {
      const streaming = part.id === streamingId;
      const isOpen = openState(part.id, streaming);
      return (
        <motion.div key={part.id} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.16 }}>
          <ReasoningPart
            part={{ ...part, streaming }}
            isOpen={isOpen}
            onToggle={() => onToggleStep(part.id, isOpen)}
          />
        </motion.div>
      );
    }
    if (part.kind === 'tool') {
      const isOpen = openState(part.id, defaultToolOpen(part));
      return (
        <motion.div key={part.id} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.16 }}>
          <ToolPart part={part} isOpen={isOpen} onToggle={() => onToggleStep(part.id, isOpen)} />
        </motion.div>
      );
    }
    if (part.kind === 'tool-group') {
      const isOpen = openState(part.id, defaultGroupOpen(part.parts));
      return (
        <motion.div key={part.id} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.16 }}>
          <ToolGroup
            parts={part.parts}
            isOpen={isOpen}
            onToggle={() => onToggleStep(part.id, isOpen)}
            expandedSteps={expandedSteps}
            onToggleStep={onToggleStep}
            defaultStepOpen={defaultToolOpen}
          />
        </motion.div>
      );
    }
    const Component = part.kind === 'answer' ? AnswerPart : part.kind === 'narration' ? NarrationPart : MetaPart;
    return (
      <motion.div key={part.id} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.16 }}>
        <Component part={part} />
      </motion.div>
    );
  });
};

export const ChatTurn = ({ run, active, canRollback, onRollback, expandedSteps, onToggleStep }) => {
  const { t } = useLocale();
  const hasAgent = run.timeline.length > 0 || active;
  const parts = buildAgentParts(run.timeline);

  return (
    <div className="my-3">
      {run.userContent ? (
        <div className="mb-3">
          <div className="flex items-center justify-end gap-1.5 mb-1 text-[var(--vscode-fg-subtle)]">
            <span className="text-[11px]">{t('agentPanel.userRole')}</span>
            <span className="w-4 h-4 rounded-[4px] bg-[var(--vscode-list-active)] flex items-center justify-center">
              <User size={11} className="text-[var(--vscode-fg-subtle)]" />
            </span>
          </div>
          <div className="flex items-start justify-end gap-1.5 group">
            <div className="max-w-[85%] px-3 py-2 rounded-[10px] text-xs leading-relaxed bg-[var(--vscode-list-hover)] text-[var(--vscode-fg)] whitespace-pre-wrap break-words">
              {run.userContent}
            </div>
            {canRollback ? (
              <button
                type="button"
                onClick={onRollback}
                title={t('agentPanel.rollbackTurn')}
                aria-label={t('agentPanel.rollbackTurn')}
                className="mt-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] text-[var(--vscode-fg-subtle)] opacity-70 transition-opacity hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)] hover:opacity-100 focus-visible:opacity-100"
              >
                <RotateCcw size={13} />
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
      {hasAgent ? (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <span className="w-5 h-5 rounded-[5px] bg-[var(--vscode-fg)] flex items-center justify-center">
              <Bot size={12} className="text-[var(--vscode-bg)]" />
            </span>
            <span className="text-sm font-bold text-[var(--vscode-fg)]">{t('agentPanel.agentRole')}</span>
            {active ? <Loader2 size={12} className="animate-spin text-[var(--vscode-focus-border)]" /> : null}
          </div>
          <div className="space-y-1.5">
            <AnimatePresence initial={false}>
              <TurnParts
                parts={parts}
                active={active}
                expandedSteps={expandedSteps}
                onToggleStep={onToggleStep}
              />
            </AnimatePresence>
            {active ? (
              <div className="flex items-center gap-1 pl-0.5 text-[var(--vscode-fg-subtle)]">
                <span className="w-1 h-1 rounded-full bg-[var(--vscode-fg-subtle)] animate-pulse" />
                <span
                  className="w-1 h-1 rounded-full bg-[var(--vscode-fg-subtle)] animate-pulse"
                  style={{ animationDelay: '0.15s' }}
                />
                <span
                  className="w-1 h-1 rounded-full bg-[var(--vscode-fg-subtle)] animate-pulse"
                  style={{ animationDelay: '0.3s' }}
                />
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
};

export const AgentTranscript = ({ runs, activeRunId, latestRunId, isGenerating, expandedSteps, onToggleStep, onRollback }) =>
  runs.map((run) => (
    <ChatTurn
      key={run.id}
      run={run}
      active={run.id === activeRunId}
      canRollback={!isGenerating && run.id === latestRunId && Boolean(run.userContent)}
      onRollback={() => onRollback(run)}
      expandedSteps={expandedSteps}
      onToggleStep={onToggleStep}
    />
  ));

export default AgentTranscript;

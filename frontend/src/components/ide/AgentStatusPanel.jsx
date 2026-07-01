/**
 * AgentStatusPanel - Agent 状态面板（带消息历史和输入框）
 *
 * 保留对话形式的同时，在 Agent 工作时显示状态卡片
 * - 消息历史记录（用户可追溯修改意见）
 * - 动态 Agent 状态卡片
 * - 底部输入框用于用户交互
 */

import React, { useMemo, useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ChevronDown,
  ChevronRight,
  Sparkles,
  Copy,
  X,
  Square,
  Brain,
  Search,
  PenLine,
  Check,
  Loader2,
  Bot,
  ArrowUp,
  User,
} from 'lucide-react';
import { useLocale } from '../../i18n';
import { buildAgentThread } from '../../lib/agentThread';

// 复制按钮：悬停浮现，复制纯文本（用于 Agent 正文）。
const CopyButton = ({ text }) => {
  const { t } = useLocale();
  const [done, setDone] = useState(false);
  const onCopy = async () => {
    if (!navigator?.clipboard?.writeText) return;
    try {
      await navigator.clipboard.writeText(String(text || ''));
      setDone(true);
      setTimeout(() => setDone(false), 1200);
    } catch (_e) {
      /* noop */
    }
  };
  return (
    <button
      type="button"
      onClick={onCopy}
      title={t('common.copy')}
      className="opacity-0 group-hover:opacity-100 transition-opacity text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)]"
    >
      {done ? <Check size={12} /> : <Copy size={12} />}
    </button>
  );
};

// 步骤图标：按阶段映射为语义图标（思考/检索/工具/撰写/完成），未知阶段用小圆点
const stageIconFor = (stage) => {
  const map = {
    thinking: Brain,
    tool_call: Search,
    tool_result: Check,
    read_previous: Search,
    read_facts: Search,
    lookup_cards: Search,
    prepare_retrieval: Search,
    execute_retrieval: Search,
    generate_plan: Search,
    self_check: Check,
    memory_pack: Search,
    writing: PenLine,
    edit_suggest: PenLine,
    edit_suggest_done: Check,
    persist: Check,
    scene_brief: Sparkles,
  };
  const Icon = map[stage];
  if (!Icon) return <span className="inline-block w-1.5 h-1.5 rounded-full bg-[var(--vscode-fg-subtle)]" />;
  return <Icon size={12} className="text-[var(--vscode-fg-subtle)]" />;
};

// 内联过程步骤（Trae 式）：思考 = 灰色「思考过程」药丸，展开为整段推理正文；
// 工具/检索/读文件 = 左侧 › 箭头行，展开为等宽结果。
const StepRow = ({ event, isOpen, onToggle, formatStageLabel }) => {
  const isThinking = event.stage === 'thinking';
  const hasNote = Boolean(event.note);
  const Chevron = (
    <motion.span
      animate={{ rotate: isOpen ? 90 : 0 }}
      transition={{ duration: 0.15 }}
      className="shrink-0 text-[var(--vscode-fg-subtle)]"
    >
      <ChevronRight size={13} />
    </motion.span>
  );

  if (isThinking) {
    return (
      <div>
        <button
          type="button"
          onClick={hasNote ? onToggle : undefined}
          className={[
            'inline-flex items-center gap-1 px-2 py-1 rounded-[6px] text-[12px] bg-[var(--vscode-list-hover)] text-[var(--vscode-fg-subtle)] transition-colors',
            hasNote ? 'hover:text-[var(--vscode-fg)] cursor-pointer' : 'cursor-default',
          ].join(' ')}
        >
          {hasNote ? Chevron : null}
          <span>{formatStageLabel('thinking')}</span>
        </button>
        <AnimatePresence initial={false}>
          {hasNote && isOpen ? (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.18 }}
              className="overflow-hidden"
            >
              <div className="mt-1.5 text-xs leading-relaxed text-[var(--vscode-fg)] whitespace-pre-wrap break-words">
                {event.note}
              </div>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    );
  }

  return (
    <div className="text-[12px]">
      <button
        type="button"
        onClick={hasNote ? onToggle : undefined}
        className={[
          'w-full flex items-center gap-1.5 text-left rounded-[6px] px-1 py-0.5 -mx-1',
          hasNote ? 'hover:bg-[var(--vscode-list-hover)] cursor-pointer' : 'cursor-default',
        ].join(' ')}
      >
        {hasNote ? Chevron : <span className="w-[13px] shrink-0" />}
        <span className="shrink-0">{stageIconFor(event.stage)}</span>
        <span className="text-[var(--vscode-fg)] shrink-0">{formatStageLabel(event.stage)}</span>
        {event.message ? (
          <span className="text-[var(--vscode-fg-subtle)] font-mono truncate min-w-0">{event.message}</span>
        ) : null}
      </button>
      <AnimatePresence initial={false}>
        {hasNote && isOpen ? (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden"
          >
            <div className="ml-[22px] mt-0.5 mb-1 whitespace-pre-wrap break-words border-l border-[var(--vscode-sidebar-border)] pl-2 text-[11px] text-[var(--vscode-fg-subtle)] font-mono">
              {event.note}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
};

// Agent 正文：无气泡、全宽、文档式（对标 Trae/ChatGPT），悬停显示复制。
const AgentProse = ({ content }) => (
  <div className="group relative text-xs leading-relaxed text-[var(--vscode-fg)] whitespace-pre-wrap break-words pr-5">
    {content}
    <span className="absolute top-0 right-0">
      <CopyButton text={content} />
    </span>
  </div>
);

// 系统/错误小行：紧凑、低存在感。
const MetaLine = ({ type, content }) => (
  <div
    className={[
      'text-[11px] px-2 py-1 rounded-[6px] border',
      type === 'error'
        ? 'bg-red-50 text-red-700 border-red-200'
        : 'bg-[var(--vscode-input-bg)] text-[var(--vscode-fg-subtle)] border-[var(--vscode-sidebar-border)] font-mono',
    ].join(' ')}
  >
    {content}
  </div>
);

// 一轮对话：用户气泡（右）→ Agent 头（机器人图标 + 进行中转圈）→ 内联时间线（步骤/正文交织）
// 把时间线里连续的工具步骤折叠成一组（思考/正文保持独立），避免「数十个工具调用」刷屏。
const _TOOL_STAGES = new Set(['tool_call', 'tool_result']);

function groupTimeline(timeline) {
  const out = [];
  let buf = [];
  const flush = () => {
    if (!buf.length) return;
    if (buf.length >= 2) out.push({ kind: 'tool-group', id: `grp-${buf[0].id}`, steps: buf });
    else out.push(buf[0]);
    buf = [];
  };
  (timeline || []).forEach((item) => {
    if (item.kind === 'progress' && _TOOL_STAGES.has(item.event?.stage)) {
      buf.push(item);
    } else {
      flush();
      out.push(item);
    }
  });
  flush();
  return out;
}

// 折叠的工具调用组：默认收起，显示「调用工具 · N 次」，展开看每步（含逐项结果）。
const ToolGroupRow = ({ steps, isOpen, onToggle, expandedSteps, onToggleStep, defaultStepOpen, formatStageLabel }) => {
  const { t } = useLocale();
  const callCount = steps.filter((s) => s.event?.stage === 'tool_call').length || steps.length;
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        className="inline-flex items-center gap-1 px-2 py-1 rounded-[6px] text-[12px] bg-[var(--vscode-list-hover)] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] transition-colors"
      >
        <motion.span animate={{ rotate: isOpen ? 90 : 0 }} transition={{ duration: 0.15 }} className="shrink-0">
          <ChevronRight size={13} />
        </motion.span>
        <Search size={12} />
        <span>{(t('agentPanel.toolGroup') || '调用工具 · {n} 次').replace('{n}', callCount)}</span>
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
            <div className="mt-1 space-y-1 pl-2 border-l border-[var(--vscode-sidebar-border)]">
              {steps.map((s) => (
                <StepRow
                  key={s.id}
                  event={s.event}
                  isOpen={expandedSteps[s.id] !== undefined ? expandedSteps[s.id] : defaultStepOpen(s.event.stage)}
                  onToggle={() =>
                    onToggleStep(
                      s.id,
                      expandedSteps[s.id] !== undefined ? expandedSteps[s.id] : defaultStepOpen(s.event.stage),
                    )
                  }
                  formatStageLabel={formatStageLabel}
                />
              ))}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
};

const ChatTurn = ({ run, active, expandedSteps, onToggleStep, defaultStepOpen, formatStageLabel }) => {
  const { t } = useLocale();
  const hasAgent = run.timeline.length > 0 || active;
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
          <div className="flex items-start justify-end gap-1.5">
            <div className="max-w-[85%] px-3 py-2 rounded-[10px] text-xs leading-relaxed bg-[var(--vscode-list-hover)] text-[var(--vscode-fg)] whitespace-pre-wrap break-words">
              {run.userContent}
            </div>
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
              {groupTimeline(run.timeline).map((item) => (
                <motion.div
                  key={item.id}
                  initial={{ opacity: 0, x: -4 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.16 }}
                >
                  {item.kind === 'tool-group' ? (
                    <ToolGroupRow
                      steps={item.steps}
                      isOpen={expandedSteps[item.id] !== undefined ? expandedSteps[item.id] : false}
                      onToggle={() =>
                        onToggleStep(item.id, expandedSteps[item.id] !== undefined ? expandedSteps[item.id] : false)
                      }
                      expandedSteps={expandedSteps}
                      onToggleStep={onToggleStep}
                      defaultStepOpen={defaultStepOpen}
                      formatStageLabel={formatStageLabel}
                    />
                  ) : item.kind === 'progress' ? (
                    <StepRow
                      event={item.event}
                      isOpen={
                        expandedSteps[item.id] !== undefined
                          ? expandedSteps[item.id]
                          : defaultStepOpen(item.event.stage)
                      }
                      onToggle={() =>
                        onToggleStep(
                          item.id,
                          expandedSteps[item.id] !== undefined
                            ? expandedSteps[item.id]
                            : defaultStepOpen(item.event.stage),
                        )
                      }
                      formatStageLabel={formatStageLabel}
                    />
                  ) : item.type === 'assistant' ? (
                    <AgentProse content={item.content} />
                  ) : (
                    <MetaLine type={item.type} content={item.content} />
                  )}
                </motion.div>
              ))}
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

// 主面板组件
const AgentStatusPanel = ({
  mode = 'create',
  inputDisabled = false,
  inputDisabledReason = '',
  selectionCandidateSummary = '',
  selectionAttachedSummary = '',
  selectionCandidateDifferent = false,
  onAttachSelection = () => {},
  onClearAttachedSelection = () => {},
  editScope = 'document',
  onEditScopeChange = () => {},
  contextDebug = null,
  progressEvents = [],
  messages = [],
  diffReview = null,
  diffDecisions = null,
  onAcceptAllDiff = () => {},
  onRejectAllDiff = () => {},
  onApplySelectedDiff = () => {},
  onSubmit = () => {},
  inputMaxLength = 2000,
  deepThinkingEnabled = false,
  deepThinkingSupported = false,
  onToggleDeepThinking = () => {},
  pendingPlan = null,
  planExecuting = false,
  onExecutePlan = () => {},
  onDismissPlan = () => {},
  isGenerating = false,
  isCancelling = false,
  onCancel = () => {},
  className = '',
}) => {
  const [inputValue, setInputValue] = useState('');
  const [copyStatus, setCopyStatus] = useState('');
  const [expandedSteps, setExpandedSteps] = useState({});
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const composerRef = useRef(null);
  const scrollAreaRef = useRef(null);
  const [composerH, setComposerH] = useState(140);
  const [scrollH, setScrollH] = useState(0);
  const { t } = useLocale();

  const runs = useMemo(() => buildAgentThread(messages, progressEvents), [messages, progressEvents]);

  const feedItems = useMemo(() => {
    const runItems = runs.map((run) => ({
      kind: 'run',
      id: run.id,
      ts: run.startedAt || 0,
      run,
    }));
    const contextItems = contextDebug
      ? [
          {
            kind: 'context',
            id: 'context-debug',
            ts: Number.MAX_SAFE_INTEGER,
            debug: contextDebug,
          },
        ]
      : [];
    return [...runItems, ...contextItems].sort((a, b) => a.ts - b.ts);
  }, [runs, contextDebug]);

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, progressEvents.length, contextDebug, diffReview]);

  // 测量输入框实际高度 + 对话区可视高度 → 消息区动态底部留白：
  // = 输入框高度 + 半屏冗余，既不被悬浮输入框遮挡，又能自由上滑约半个页面。
  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(() => {
      if (composerRef.current) setComposerH(Math.ceil(composerRef.current.offsetHeight));
      if (scrollAreaRef.current) setScrollH(Math.ceil(scrollAreaRef.current.clientHeight));
    });
    if (composerRef.current) ro.observe(composerRef.current);
    if (scrollAreaRef.current) ro.observe(scrollAreaRef.current);
    return () => ro.disconnect();
  }, []);

  const diffSummary = useMemo(() => {
    if (!diffReview?.hunks?.length) return null;
    const total = diffReview.hunks.length;
    const decisions = diffDecisions || {};
    let accepted = 0;
    let rejected = 0;
    let pending = 0;
    diffReview.hunks.forEach((hunk) => {
      const decision = decisions[hunk.id];
      if (decision === 'accepted') accepted += 1;
      else if (decision === 'rejected') rejected += 1;
      else pending += 1;
    });
    return {
      total,
      accepted,
      rejected,
      pending,
      additions: diffReview.stats?.additions || 0,
      deletions: diffReview.stats?.deletions || 0,
    };
  }, [diffReview, diffDecisions]);

  const hasDiffActions = Boolean(diffSummary);
  const hasAnyContent = runs.length > 0 || Boolean(contextDebug) || hasDiffActions || Boolean(pendingPlan);

  const handleSubmit = () => {
    if (inputDisabled) return;
    if (!inputValue.trim()) return;
    onSubmit(inputValue.trim());
    setInputValue('');
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const updateInputHeight = (el) => {
    if (!el) return;
    el.style.height = 'auto';
    const maxHeight = 160;
    const nextHeight = Math.min(el.scrollHeight, maxHeight);
    el.style.height = `${Math.max(nextHeight, 40)}px`;
    el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden';
  };

  const handleCopyContextDebug = async () => {
    if (!contextDebug) return;
    const text = typeof contextDebug === 'string' ? contextDebug : JSON.stringify(contextDebug, null, 2);
    if (!navigator?.clipboard?.writeText) {
      window.alert(t('agentPanel.clipboardNotSupported'));
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setCopyStatus(t('common.copied'));
      setTimeout(() => setCopyStatus(''), 1500);
    } catch (_error) {
      setCopyStatus(t('common.copyFailed'));
      setTimeout(() => setCopyStatus(''), 2000);
    }
  };

  // 步骤展开：显式记录 true/false（覆盖默认值）。current 为当前可见状态，点击则取反。
  const toggleStep = (id, current) => {
    setExpandedSteps((prev) => ({ ...prev, [id]: !current }));
  };
  // 思考步骤默认展开（呈现真实推理），其余步骤默认折叠。
  const defaultStepOpen = (stage) => stage === 'thinking';

  const formatStageLabel = (stage) => {
    return t(`agentPanel.stageLabels.${stage}`) !== `agentPanel.stageLabels.${stage}`
      ? t(`agentPanel.stageLabels.${stage}`)
      : stage || t('agentPanel.stageLabels.default');
  };

  return (
    <div className={`relative flex flex-col h-full ${className}`}>
      {/* 消息列表（对话 + 行动轨迹） */}
      <div
        ref={scrollAreaRef}
        className="flex-1 overflow-y-auto custom-scrollbar p-3"
        style={{ paddingBottom: composerH + 16 + Math.round(scrollH * 0.5) }}
      >
        {!hasAnyContent ? (
          /* 欢迎提示 */
          <div className="h-full flex flex-col items-center justify-center text-center p-6">
            <div className="w-16 h-16 rounded-[6px] bg-[var(--vscode-list-hover)] border border-[var(--vscode-sidebar-border)] flex items-center justify-center mb-4">
              <Sparkles size={28} className="text-[var(--vscode-focus-border)]" />
            </div>
            <h3 className="text-sm font-bold text-[var(--vscode-fg)] mb-2">{t('agentPanel.welcome')}</h3>
            <p className="text-xs text-[var(--vscode-fg-subtle)] max-w-[200px]">{t('agentPanel.welcomeHint')}</p>
          </div>
        ) : (
          <>
            {feedItems.map((item) => {
              if (item.kind === 'run') {
                return (
                  <ChatTurn
                    key={item.id}
                    run={item.run}
                    active={isGenerating && runs.length > 0 && item.run.id === runs[runs.length - 1].id}
                    expandedSteps={expandedSteps}
                    onToggleStep={toggleStep}
                    defaultStepOpen={defaultStepOpen}
                    formatStageLabel={formatStageLabel}
                  />
                );
              }
              if (item.kind === 'context') {
                const expanded = Boolean(expandedSteps[item.id]);
                return (
                  <div
                    key={item.id}
                    className="border border-[var(--vscode-sidebar-border)] rounded-[6px] bg-[var(--vscode-input-bg)] my-2 overflow-hidden"
                  >
                    <button
                      type="button"
                      onClick={() => toggleStep(item.id, expanded)}
                      className="w-full text-left px-3 py-2 flex items-start justify-between gap-2 hover:bg-[var(--vscode-list-hover)]"
                    >
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-bold text-[var(--vscode-fg)]">
                            {t('agentPanel.workingMemory')}
                          </span>
                          <span className="text-[10px] text-[var(--vscode-fg-subtle)]">
                            {t('agentPanel.workingMemoryHint')}
                          </span>
                        </div>
                        <div className="text-xs text-[var(--vscode-fg-subtle)]">
                          {t('agentPanel.workingMemoryDesc')}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 text-[10px] text-[var(--vscode-fg-subtle)]">
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            handleCopyContextDebug();
                          }}
                          title={t('agentPanel.copyJson')}
                          className="flex items-center gap-1 px-2 py-1 rounded-[6px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)] hover:border-[var(--vscode-focus-border)]"
                        >
                          <Copy size={12} />
                          <span>{copyStatus || t('common.copy')}</span>
                        </button>
                        <motion.div animate={{ rotate: expanded ? 180 : 0 }} transition={{ duration: 0.15 }}>
                          <ChevronDown size={14} />
                        </motion.div>
                      </div>
                    </button>
                    {expanded && (
                      <div className="px-3 pb-3">
                        <div className="bg-[var(--vscode-input-bg)] border border-[var(--vscode-sidebar-border)] rounded-[6px] p-3 max-h-64 overflow-y-auto custom-scrollbar">
                          <pre className="text-[10px] text-[var(--vscode-fg-subtle)] font-mono whitespace-pre-wrap break-words">
                            {typeof item.debug === 'string' ? item.debug : JSON.stringify(item.debug, null, 2)}
                          </pre>
                        </div>
                      </div>
                    )}
                  </div>
                );
              }

              return null;
            })}
            {hasDiffActions ? (
              <div className="border border-[var(--vscode-sidebar-border)] rounded-[6px] bg-[var(--vscode-input-bg)] my-2 overflow-hidden">
                <div className="px-3 py-2 border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)]">
                  <div className="flex items-center justify-between">
                    <div className="text-xs font-bold text-[var(--vscode-fg)]">{t('agentPanel.diffDone')}</div>
                    <div className="text-[10px] text-[var(--vscode-fg-subtle)]">
                      {t('agentPanel.diffStats')
                        .replace('{add}', diffSummary.additions)
                        .replace('{del}', diffSummary.deletions)}
                    </div>
                  </div>
                  <div className="text-[10px] text-[var(--vscode-fg-subtle)] mt-1">
                    {t('agentPanel.diffSummary')
                      .replace('{total}', diffSummary.total)
                      .replace('{accepted}', diffSummary.accepted)
                      .replace('{rejected}', diffSummary.rejected)
                      .replace('{pending}', diffSummary.pending)}
                  </div>
                </div>
                <div className="px-3 py-2 text-[10px] text-[var(--vscode-fg-subtle)]">{t('agentPanel.diffHint')}</div>
                <div className="px-3 pb-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={onRejectAllDiff}
                    className="text-[10px] px-3 py-1.5 rounded-[6px] border border-red-200 text-red-600 hover:bg-red-50 transition-colors"
                  >
                    {t('agentPanel.rejectAll')}
                  </button>
                  <button
                    type="button"
                    onClick={onAcceptAllDiff}
                    className="text-[10px] px-3 py-1.5 rounded-[6px] border border-green-200 text-green-700 hover:bg-green-50 transition-colors"
                  >
                    {t('agentPanel.acceptAll')}
                  </button>
                  <button
                    type="button"
                    onClick={onApplySelectedDiff}
                    className="text-[10px] px-3 py-1.5 rounded-[6px] border border-[var(--vscode-input-border)] bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] hover:opacity-90 transition-colors"
                  >
                    {t('agentPanel.applyAccepted')}
                  </button>
                </div>
              </div>
            ) : null}
            {pendingPlan ? (
              <div className="border border-[var(--vscode-sidebar-border)] rounded-[6px] bg-[var(--vscode-input-bg)] my-2 overflow-hidden">
                <div className="px-3 py-2 border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)]">
                  <div className="text-xs font-bold text-[var(--vscode-fg)]">{t('agentPanel.planTitle')}</div>
                  {pendingPlan.goal ? (
                    <div className="text-[10px] text-[var(--vscode-fg-subtle)] mt-1 truncate">{pendingPlan.goal}</div>
                  ) : null}
                </div>
                <div className="px-3 py-2 space-y-1 max-h-48 overflow-y-auto custom-scrollbar">
                  {(pendingPlan.steps || []).map((step, i) => (
                    <div key={i} className="flex items-start gap-2 text-[11px] text-[var(--vscode-fg)]">
                      <span className="font-mono text-[var(--vscode-fg-subtle)] shrink-0">{i + 1}.</span>
                      <span className="px-1.5 rounded-[4px] bg-[var(--vscode-list-hover)] text-[10px] text-[var(--vscode-fg-subtle)] shrink-0">
                        {step.action}
                      </span>
                      <span className="min-w-0 break-words">
                        {step.description}
                        {step.chapter ? ` · ${step.chapter}` : ''}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="px-3 pb-3 pt-1 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={onDismissPlan}
                    disabled={planExecuting}
                    className="text-[10px] px-3 py-1.5 rounded-[6px] border border-[var(--vscode-sidebar-border)] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] disabled:opacity-50 transition-colors"
                  >
                    {t('agentPanel.planDismiss')}
                  </button>
                  <button
                    type="button"
                    onClick={onExecutePlan}
                    disabled={planExecuting}
                    className="text-[10px] px-3 py-1.5 rounded-[6px] border border-green-200 text-green-700 hover:bg-green-50 disabled:opacity-50 inline-flex items-center gap-1 transition-colors"
                  >
                    {planExecuting ? <Loader2 size={12} className="animate-spin" /> : null}
                    {planExecuting ? t('agentPanel.planRunning') : t('agentPanel.planExecute')}
                  </button>
                </div>
              </div>
            ) : null}
          </>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* 底部输入框（Trae 式 · 悬浮于对话栏，左右下等距，液态玻璃） */}
      <div ref={composerRef} className="absolute bottom-0 inset-x-0 p-3">
        {inputDisabled && inputDisabledReason ? (
          <div className="mb-2 text-[10px] text-[var(--vscode-fg-subtle)] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)] rounded-[6px] px-3 py-2">
            {inputDisabledReason}
          </div>
        ) : null}

        {/* 编辑态选区控件（划词即编辑：整篇 / 选区 / 附加选区） */}
        {mode === 'edit' && selectionCandidateSummary ? (
          <div className="mb-2 flex flex-wrap items-center gap-1">
                <button
                  type="button"
                  disabled={inputDisabled}
                  onClick={() => onEditScopeChange('document')}
                  className={[
                    'px-2 h-6 text-[10px] rounded-[6px] border transition-colors',
                    editScope === 'document'
                      ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] border-[var(--vscode-input-border)]'
                      : 'bg-[var(--vscode-input-bg)] text-[var(--vscode-fg)] border-[var(--vscode-sidebar-border)] hover:border-[var(--vscode-focus-border)]',
                    inputDisabled ? 'opacity-50 cursor-not-allowed' : '',
                  ].join(' ')}
                  title={t('agentPanel.scopeDocumentHint')}
                >
                  {t('agentPanel.scopeDocument')}
                </button>
                <button
                  type="button"
                  disabled={inputDisabled || !selectionAttachedSummary}
                  onClick={() => onEditScopeChange('selection')}
                  className={[
                    'px-2 h-6 text-[10px] rounded-[6px] border transition-colors',
                    editScope === 'selection'
                      ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] border-[var(--vscode-input-border)]'
                      : 'bg-[var(--vscode-input-bg)] text-[var(--vscode-fg)] border-[var(--vscode-sidebar-border)] hover:border-[var(--vscode-focus-border)]',
                    inputDisabled || !selectionAttachedSummary ? 'opacity-50 cursor-not-allowed' : '',
                  ].join(' ')}
                  title={
                    selectionAttachedSummary
                      ? t('agentPanel.scopeSelectionHint')
                      : t('agentPanel.scopeSelectionDisabledHint')
                  }
                >
                  {t('agentPanel.scopeSelection')}
                </button>
                <button
                  type="button"
                  disabled={inputDisabled || (selectionAttachedSummary && !selectionCandidateDifferent)}
                  onClick={onAttachSelection}
                  className={[
                    'px-2 h-6 text-[10px] rounded-[6px] border transition-colors',
                    'bg-[var(--vscode-input-bg)] text-[var(--vscode-fg)] border-[var(--vscode-sidebar-border)] hover:border-[var(--vscode-focus-border)]',
                    inputDisabled || (selectionAttachedSummary && !selectionCandidateDifferent)
                      ? 'opacity-50 cursor-not-allowed'
                      : '',
                  ].join(' ')}
                  title={t('agentPanel.scopeDocumentHint')}
                >
                  {selectionAttachedSummary
                    ? selectionCandidateDifferent
                      ? t('agentPanel.replaceSelection')
                      : t('agentPanel.selectionAttached')
                    : t('agentPanel.attachSelection')}
                </button>
          </div>
        ) : null}
        {mode === 'edit' && selectionAttachedSummary ? (
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="text-[10px] px-2 py-1 rounded-[6px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)] text-[var(--vscode-fg-subtle)] truncate">
              {selectionAttachedSummary}
            </div>
            <button
              type="button"
              disabled={inputDisabled}
              onClick={onClearAttachedSelection}
              className={[
                'p-1 rounded-[6px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] hover:border-[var(--vscode-focus-border)] transition-colors shrink-0',
                inputDisabled ? 'opacity-50 cursor-not-allowed' : '',
              ].join(' ')}
              title={t('agentPanel.clearSelection')}
              aria-label={t('agentPanel.clearSelection')}
            >
              <X size={14} />
            </button>
          </div>
        ) : null}

        {/* Trae 式输入框：@Agent 顶条 + 无边框文本区 + 底排工具条 */}
        <div className="rounded-[16px] overflow-hidden liquid-glass">
          <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1">
            <span className="w-[18px] h-[18px] rounded-[5px] bg-[var(--vscode-fg)] flex items-center justify-center">
              <Bot size={11} className="text-[var(--vscode-bg)]" />
            </span>
            <span className="text-xs font-bold text-[var(--vscode-fg)]">{t('agentPanel.composerAgent')}</span>
            {deepThinkingSupported ? (
              <button
                type="button"
                onClick={onToggleDeepThinking}
                title={t('agentPanel.deepThinkingHint')}
                aria-pressed={deepThinkingEnabled}
                className={[
                  'ml-auto inline-flex items-center gap-1 px-2 h-6 rounded-full border text-[10px] transition-colors',
                  deepThinkingEnabled
                    ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] border-[var(--vscode-focus-border)]'
                    : 'bg-[var(--vscode-input-bg)] text-[var(--vscode-fg-subtle)] border-[var(--vscode-sidebar-border)] hover:text-[var(--vscode-fg)]',
                ].join(' ')}
              >
                <Brain size={12} />
                {t('agentPanel.deepThinking')}
              </button>
            ) : null}
          </div>
          <textarea
            ref={inputRef}
            rows={1}
            value={inputValue}
            onChange={(e) => {
              setInputValue(e.target.value);
              updateInputHeight(e.target);
            }}
            onKeyDown={handleKeyDown}
            onFocus={(e) => updateInputHeight(e.target)}
            disabled={inputDisabled}
            maxLength={inputMaxLength}
            placeholder={t('agentPanel.composerPlaceholder')}
            className={[
              'w-full px-3 pb-2 text-sm bg-transparent border-0 text-[var(--vscode-fg)] focus:outline-none focus:ring-0 resize-none min-h-[44px] overscroll-contain placeholder:text-[var(--vscode-fg-subtle)]',
              inputDisabled ? 'opacity-60 cursor-not-allowed' : '',
            ].join(' ')}
          />
          <div className="flex items-center justify-end gap-1 px-2 pb-2">
            {isGenerating ? (
              <button
                onClick={onCancel}
                disabled={isCancelling}
                title={t('agentPanel.cancelGeneration')}
                className="w-8 h-8 flex items-center justify-center bg-red-500/10 text-red-600 rounded-[8px] border border-red-300/50 hover:bg-red-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150 active:scale-90"
              >
                {isCancelling ? (
                  <motion.div
                    animate={{ rotate: 360 }}
                    transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                    className="w-4 h-4 border-2 border-red-500 border-t-transparent rounded-full"
                  />
                ) : (
                  <Square size={15} />
                )}
              </button>
            ) : (
              <button
                onClick={handleSubmit}
                disabled={inputDisabled || !inputValue.trim()}
                title={t('agentPanel.send')}
                className="w-8 h-8 flex items-center justify-center rounded-[8px] bg-emerald-100 text-emerald-700 hover:bg-emerald-200 disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-150 active:scale-90"
              >
                <ArrowUp size={16} />
              </button>
            )}
          </div>
        </div>
        <div className="flex justify-end mt-1 text-[10px] text-[var(--vscode-fg-subtle)]">
          {`${inputValue.length}/${inputMaxLength}`}
        </div>
      </div>
    </div>
  );
};

export default AgentStatusPanel;

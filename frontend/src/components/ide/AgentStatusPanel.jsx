/**
 * AgentStatusPanel - Agent 面板组装壳
 *
 * 对话流渲染已抽出到 `components/agent/`（AgentTranscript + parts）：本文件只负责
 * 组装 —— transcript、写作记忆/Canon 卡、diff 审阅、plan 卡与底部输入区。
 */

import React, { useMemo, useState, useRef, useEffect } from 'react';
import { motion } from 'framer-motion';
import { ChevronDown, X, Square, Brain, Check, Bot, ArrowUp } from 'lucide-react';
import { useLocale } from '../../i18n';
import { buildAgentThread } from '../../lib/agentThread';
import { AgentTranscript } from '../agent/AgentTranscript';
import { PlanTaskCard } from '../agent/parts/PlanTaskCard';
import { ClarificationPart } from '../agent/parts/ClarificationPart';
import { WritingMemoryCard } from '../../features/agent/components/WritingMemoryCard';
import { CanonTurnCard } from '../../features/agent/components/CanonTurnCard';

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
  memoryPackStatus = null,
  memoryPackLoading = false,
  activeChapter = '',
  showWritingMemory = false,
  canonTurnState = null,
  progressEvents = [],
  messages = [],
  diffReview = null,
  diffDecisions = null,
  onAcceptAllDiff = () => {},
  onRejectAllDiff = () => {},
  onApplySelectedDiff = () => {},
  onSubmit = () => {},
  inputMaxLength = 2000,
  reasoningLevel = 'off',
  reasoningLevels = ['off'],
  reasoningSupported = false,
  onReasoningLevelChange = () => {},
  agentMention = 'Agent',
  pendingPlan = null,
  planExecuting = false,
  planActiveStepId = null,
  onExecutePlan = () => {},
  onDismissPlan = () => {},
  clarification = null,
  onClarificationConfirm = () => {},
  onClarificationSkip = () => {},
  onRollbackConversation = () => {},
  isGenerating = false,
  isCancelling = false,
  onCancel = () => {},
  className = '',
}) => {
  const [inputValue, setInputValue] = useState('');
  const [expandedSteps, setExpandedSteps] = useState({});
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const composerRef = useRef(null);
  const scrollAreaRef = useRef(null);
  const [composerH, setComposerH] = useState(140);
  const [scrollH, setScrollH] = useState(0);
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const { t } = useLocale();

  useEffect(() => {
    if (!reasoningOpen) return undefined;
    const close = (event) => {
      if (!composerRef.current?.contains(event.target)) {
        setReasoningOpen(false);
      }
    };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [reasoningOpen]);

  const runs = useMemo(() => buildAgentThread(messages, progressEvents), [messages, progressEvents]);
  const latestRunId = runs[runs.length - 1]?.id;
  const activeRunId = isGenerating ? latestRunId : undefined;

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [
    messages.length,
    progressEvents.length,
    diffReview,
    memoryPackStatus?.built_at,
    canonTurnState?.status,
  ]);

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
  const hasAnyContent =
    runs.length > 0 ||
    showWritingMemory ||
    Boolean(canonTurnState) ||
    hasDiffActions ||
    Boolean(pendingPlan);

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

  const handleRollback = async (run) => {
    const restored = await onRollbackConversation(run?.startedAt || 0);
    if (typeof restored !== 'string') return;
    setInputValue(restored);
    window.requestAnimationFrame(() => {
      updateInputHeight(inputRef.current);
      inputRef.current?.focus();
      if (inputRef.current) {
        const end = restored.length;
        inputRef.current.setSelectionRange(end, end);
      }
    });
  };

  // 步骤展开：显式记录 true/false（覆盖默认值）。current 为当前可见状态，点击则取反。
  const toggleStep = (id, current) => {
    setExpandedSteps((prev) => ({ ...prev, [id]: !current }));
  };

  const reasoningLabel = (level) =>
    ({ auto: 'AUTO', off: 'OFF', minimal: 'MINIMAL', low: 'LOW', medium: 'MEDIUM', high: 'HIGH', xhigh: 'XHIGH', max: 'MAX' })[
      level
    ] || level;

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
          <div
            className="flex items-center justify-center p-6 text-center"
            style={{ height: Math.max(180, scrollH - composerH - 24) }}
          >
            <h3 className="text-lg font-semibold tracking-tight text-[var(--vscode-fg)]">{t('agentPanel.welcome')}</h3>
          </div>
        ) : (
          <>
            <AgentTranscript
              runs={runs}
              activeRunId={activeRunId}
              latestRunId={latestRunId}
              isGenerating={isGenerating}
              expandedSteps={expandedSteps}
              onToggleStep={toggleStep}
              onRollback={handleRollback}
            />
            {showWritingMemory ? (
              <WritingMemoryCard
                status={memoryPackStatus}
                loading={memoryPackLoading}
                chapter={activeChapter}
              />
            ) : null}
            {canonTurnState ? <CanonTurnCard state={canonTurnState} /> : null}
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
              <PlanTaskCard
                plan={pendingPlan}
                executing={planExecuting}
                activeStepId={planActiveStepId}
                onExecute={onExecutePlan}
                onDismiss={onDismissPlan}
              />
            ) : null}
            {clarification ? (
              <ClarificationPart
                questions={clarification.questions || []}
                reason={clarification.reason || ''}
                resolved={clarification.resolved || null}
                onConfirm={onClarificationConfirm}
                onSkip={onClarificationSkip}
              />
            ) : null}
          </>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* 底部输入框（Trae 式 · 悬浮于对话栏，左右下等距，液态玻璃） */}
      <div ref={composerRef} className="absolute bottom-0 inset-x-0 z-30 p-3">
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
        <div className="rounded-[16px] overflow-visible liquid-glass">
          <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1">
            <span className="w-[18px] h-[18px] rounded-[5px] bg-[var(--vscode-fg)] flex items-center justify-center">
              <Bot size={11} className="text-[var(--vscode-bg)]" />
            </span>
            <span className="text-xs font-bold text-[var(--vscode-fg)]">@{agentMention}</span>
            {reasoningSupported ? (
              <div className="relative ml-auto">
                <button
                  type="button"
                  onClick={() => {
                    setReasoningOpen((value) => !value);
                  }}
                  title={t('agentPanel.deepThinkingHint')}
                  aria-expanded={reasoningOpen}
                  className="inline-flex h-6 items-center gap-1 rounded-full border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)] px-2 text-[10px] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)]"
                >
                  <Brain size={12} />
                  <span>思考 · {reasoningLabel(reasoningLevel)}</span>
                  <ChevronDown size={11} />
                </button>
                {reasoningOpen ? (
                  <div className="absolute bottom-full right-0 z-50 mb-2 min-w-32 rounded-[8px] border border-[var(--vscode-input-border)] bg-white p-1 shadow-[0_8px_24px_rgba(15,23,42,0.12)]">
                    {reasoningLevels.map((level) => (
                      <button
                        key={level}
                        type="button"
                        onClick={() => {
                          onReasoningLevelChange(level);
                          setReasoningOpen(false);
                        }}
                        className="flex w-full items-center justify-between rounded-[6px] px-2 py-1.5 text-left text-[11px] hover:bg-[var(--vscode-list-hover)]"
                      >
                        <span>{reasoningLabel(level)}</span>
                        {level === reasoningLevel ? <Check size={11} /> : null}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
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

import AgentsPanel from '../ide/panels/AgentsPanel';
import AgentStatusPanel from '../ide/AgentStatusPanel';

export default function WritingSessionAgentPanel({ vm }) {
  const {
    traceEvents,
    agentTraces,
    agentMode,
    agentBusy,
    aiLockedChapter,
    activeChapterKey,
    t,
    isCancelling,
    handleCancel,
    selectionInfo,
    attachedSelection,
    setAttachedSelection,
    setEditScope,
    editScope,
    contextDebug,
    progressEvents,
    messages,
    diffReview,
    agentChapterKey,
    diffDecisions,
    handleAcceptAllDiff,
    handleRejectAllDiff,
    handleApplySelectedDiff,
    handleChatSubmit,
    countWords,
    writingLanguage,
    dialogMaxChars,
    deepThinkingEnabled,
    deepThinkingSupported,
    onToggleDeepThinking,
    pendingPlan,
    pendingApproval,
    agentTurnMeta,
    planExecuting,
    onExecutePlan,
    onDismissPlan,
    onApproveFallback,
    onDismissFallback,
  } = vm;

  // 输入禁用仅限「AI 正忙于其它章节」。无激活章节不再禁用——复杂规划 / 问答可直接对话，
  // 撰写 / 编辑由 handleChatSubmit 在无章节时友好引导（vibe writing 灵活性）。
  const aiBusyElsewhere = agentBusy && String(aiLockedChapter || '') !== activeChapterKey;
  const inputDisabled = aiBusyElsewhere;
  const inputDisabledReason = aiBusyElsewhere
    ? t('writingSession.aiLockedHint').replace('{n}', String(aiLockedChapter))
    : '';

  return (
    <AgentsPanel traceEvents={traceEvents} agentTraces={agentTraces}>
      <AgentStatusPanel
        mode={agentMode}
        inputDisabled={inputDisabled}
        inputDisabledReason={inputDisabledReason}
        isGenerating={agentBusy && String(aiLockedChapter || '') === activeChapterKey}
        isCancelling={isCancelling}
        onCancel={handleCancel}
        selectionCandidateSummary={
          agentMode === 'edit' && selectionInfo?.text?.trim()
            ? t('writingSession.selectionPending').replace('{n}', countWords(selectionInfo.text, writingLanguage))
            : ''
        }
        selectionAttachedSummary={
          agentMode === 'edit' && attachedSelection?.text?.trim()
            ? t('writingSession.selectionAdded').replace('{n}', countWords(attachedSelection.text, writingLanguage))
            : ''
        }
        selectionCandidateDifferent={
          Boolean(selectionInfo?.text?.trim()) &&
          Boolean(attachedSelection?.text?.trim()) &&
          (selectionInfo.start !== attachedSelection.start ||
            selectionInfo.end !== attachedSelection.end ||
            selectionInfo.text !== attachedSelection.text)
        }
        onAttachSelection={() => {
          if (!selectionInfo?.text?.trim()) return;
          setAttachedSelection({
            start: selectionInfo.start,
            end: selectionInfo.end,
            text: selectionInfo.text,
          });
          setEditScope('selection');
        }}
        onClearAttachedSelection={() => {
          setAttachedSelection(null);
          setEditScope('document');
        }}
        editScope={editScope}
        onEditScopeChange={setEditScope}
        contextDebug={contextDebug}
        progressEvents={progressEvents}
        messages={messages}
        diffReview={diffReview && String(diffReview?.chapterKey || '') === agentChapterKey ? diffReview : null}
        diffDecisions={diffDecisions}
        onAcceptAllDiff={handleAcceptAllDiff}
        onRejectAllDiff={handleRejectAllDiff}
        onApplySelectedDiff={handleApplySelectedDiff}
        onSubmit={(text) => handleChatSubmit(text)}
        inputMaxLength={dialogMaxChars}
        deepThinkingEnabled={deepThinkingEnabled}
        deepThinkingSupported={deepThinkingSupported}
        onToggleDeepThinking={onToggleDeepThinking}
        pendingPlan={pendingPlan}
        pendingApproval={pendingApproval}
        agentTurnMeta={agentTurnMeta}
        planExecuting={planExecuting}
        onExecutePlan={onExecutePlan}
        onDismissPlan={onDismissPlan}
        onApproveFallback={onApproveFallback}
        onDismissFallback={onDismissFallback}
      />
    </AgentsPanel>
  );
}

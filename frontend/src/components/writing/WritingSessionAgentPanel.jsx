import AgentsPanel from '../ide/panels/AgentsPanel';
import AgentStatusPanel from '../ide/AgentStatusPanel';
import { AgentPanelErrorBoundary } from '../../features/agent/components/AgentPanelErrorBoundary';

export default function WritingSessionAgentPanel({ vm }) {
  const {
    traceEvents,
    agentTraces,
    agentMode,
    agentBusy,
    t,
    isCancelling,
    handleCancel,
    selectionInfo,
    attachedSelection,
    setAttachedSelection,
    setEditScope,
    editScope,
    memoryPackStatus,
    memoryPackLoading,
    memoryPackChapter,
    showWritingMemory,
    canonTurnState,
    progressEvents,
    messages,
    diffReview,
    diffDecisions,
    handleAcceptAllDiff,
    handleRejectAllDiff,
    handleApplySelectedDiff,
    handleChatSubmit,
    countWords,
    writingLanguage,
    dialogMaxChars,
    reasoningLevel,
    reasoningLevels,
    reasoningSupported,
    onReasoningLevelChange,
    agentMention,
    pendingPlan,
    planExecuting,
    planActiveStepId,
    onExecutePlan,
    onDismissPlan,
    clarification,
    onClarificationConfirm,
    onClarificationSkip,
    conversations,
    activeConversationId,
    onNewConversation,
    onSelectConversation,
    onRollbackConversation,
  } = vm;

  return (
    <AgentsPanel
      traceEvents={traceEvents}
      agentTraces={agentTraces}
      conversations={conversations}
      activeConversationId={activeConversationId}
      onNewConversation={onNewConversation}
      onSelectConversation={onSelectConversation}
    >
      <AgentPanelErrorBoundary>
      <AgentStatusPanel
        mode={agentMode}
        inputDisabled={false}
        inputDisabledReason=""
        isGenerating={agentBusy}
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
        memoryPackStatus={memoryPackStatus}
        memoryPackLoading={memoryPackLoading}
        activeChapter={memoryPackChapter}
        showWritingMemory={showWritingMemory}
        canonTurnState={canonTurnState}
        progressEvents={progressEvents}
        messages={messages}
        diffReview={diffReview}
        diffDecisions={diffDecisions}
        onAcceptAllDiff={handleAcceptAllDiff}
        onRejectAllDiff={handleRejectAllDiff}
        onApplySelectedDiff={handleApplySelectedDiff}
        onSubmit={(text) => handleChatSubmit(text)}
        inputMaxLength={dialogMaxChars}
        reasoningLevel={reasoningLevel}
        reasoningLevels={reasoningLevels}
        reasoningSupported={reasoningSupported}
        onReasoningLevelChange={onReasoningLevelChange}
        agentMention={agentMention}
        pendingPlan={pendingPlan}
        planExecuting={planExecuting}
        planActiveStepId={planActiveStepId}
        onExecutePlan={onExecutePlan}
        onDismissPlan={onDismissPlan}
        clarification={clarification}
        onClarificationConfirm={onClarificationConfirm}
        onClarificationSkip={onClarificationSkip}
        onRollbackConversation={onRollbackConversation}
      />
      </AgentPanelErrorBoundary>
    </AgentsPanel>
  );
}

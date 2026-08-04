import { AnimatePresence, motion } from 'framer-motion';
import { X } from 'lucide-react';

import DiffReviewView from '../ide/DiffReviewView';
import EditorTabs from '../ide/EditorTabs';
import { Input } from '../ui/core';
import FanfictionView from '../../pages/FanfictionView';
import { normalizeStars } from '../../utils/writingSessionHelpers';
import StreamingDiffView from './StreamingDiffView';
import OutlineView from './OutlineView';

export default function WritingSessionMainContent({ vm }) {
  const {
    activeActivity,
    activeCard,
    activeDocument,
    projectId,
    cardForm,
    chapterInfo,
    chapterLoadError,
    chapterLoading,
    diffDecisions,
    diffReview,
    dispatch,
    editorRef,
    fontSize,
    isDiffReviewForActiveChapter,
    isStreamingForActiveChapter,
    streamOriginalContent,
    manualContent,
    onAcceptDiffHunk,
    onCardFormChange,
    onCloseCardEditor,
    onCloseTab,
    onCloseOtherTabs,
    onEditorScroll,
    onFontSizeChange,
    onManualContentChange,
    onManualSelectionChange,
    onRejectDiffHunk,
    onRenameTab,
    onSelectTab,
    status,
    t,
    tabs,
    tabStatusKeys,
  } = vm;

  // 同人视图走 activeActivity 另一条路径，不属于文档标签体系。
  if (activeActivity === 'fanfiction') {
    return <FanfictionView embedded onClose={() => dispatch({ type: 'SET_ACTIVE_PANEL', payload: 'explorer' })} />;
  }

  const body =
    activeDocument?.type === 'outline' && projectId ? (
      <OutlineView projectId={projectId} />
    ) : (
      <AnimatePresence mode="wait">
        {status === 'card_editing' && activeCard ? (
          <motion.div
            key="card-editor"
            initial={{ opacity: 0, scale: 0.98, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98, y: -10 }}
            transition={{ duration: 0.3, ease: 'easeOut' }}
            className="h-full flex flex-col max-w-3xl mx-auto w-full px-4 pt-4"
          >
            <div className="flex items-center justify-between mb-4 pb-3 border-b border-border">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-primary/10 rounded-lg text-primary">
                  {activeCard.type === 'character' ? (
                    <div className="i-lucide-user" />
                  ) : (
                    <div className="i-lucide-globe" />
                  )}
                </div>
                <div>
                  <p className="text-xs text-ink-400 font-mono uppercase tracking-wider">
                    {activeCard.type === 'character'
                      ? t('writingSession.cardTypeChar')
                      : t('writingSession.cardTypeWorld')}
                  </p>
                </div>
              </div>
              <button
                onClick={onCloseCardEditor}
                className="p-2 hover:bg-ink-100 rounded-lg transition-colors text-ink-400 hover:text-ink-700"
                title={t('writingSession.closeCardEdit')}
              >
                <X size={20} />
              </button>
            </div>

            <div className="space-y-5 flex-1 overflow-y-auto px-1 pb-10">
              <div className="space-y-1">
                <label className="text-xs font-bold text-ink-500 tracking-wider">{t('card.fieldName')}</label>
                <Input
                  value={cardForm.name}
                  onChange={(e) => onCardFormChange({ name: e.target.value })}
                  className="font-serif text-lg bg-[var(--vscode-input-bg)] font-bold"
                />
              </div>

              <div className="space-y-1">
                <label className="text-xs font-bold text-ink-500 tracking-wider">{t('card.fieldStars')}</label>
                <select
                  value={cardForm.stars}
                  onChange={(e) => onCardFormChange({ stars: normalizeStars(e.target.value) })}
                  className="w-full h-10 px-3 rounded-[6px] border border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] text-sm focus:ring-1 focus:ring-[var(--vscode-focus-border)]"
                >
                  <option value={3}>{t('card.stars3')}</option>
                  <option value={2}>{t('card.stars2')}</option>
                  <option value={1}>{t('card.stars1')}</option>
                </select>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-bold text-ink-500 tracking-wider">{t('card.fieldAliases')}</label>
                <Input
                  value={cardForm.aliases || ''}
                  onChange={(e) => onCardFormChange({ aliases: e.target.value })}
                  placeholder={t('card.fieldAliasesPlaceholder')}
                  className="bg-[var(--vscode-input-bg)]"
                />
              </div>

              {activeCard.type === 'world' ? (
                <div className="space-y-1">
                  <label className="text-xs font-bold text-ink-500 tracking-wider">{t('card.fieldCategory')}</label>
                  <Input
                    value={cardForm.category || ''}
                    onChange={(e) => onCardFormChange({ category: e.target.value })}
                    placeholder={t('card.categoryPlaceholder')}
                    className="bg-[var(--vscode-input-bg)]"
                  />
                </div>
              ) : null}

              <div className="space-y-1">
                <label className="text-xs font-bold text-ink-500 tracking-wider">{t('card.fieldDescription')}</label>
                <textarea
                  className="w-full min-h-[200px] p-3 rounded-[6px] border border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] text-sm focus:ring-1 focus:ring-[var(--vscode-focus-border)] resize-none overflow-hidden"
                  value={cardForm.description || ''}
                  onChange={(e) => {
                    onCardFormChange({ description: e.target.value });
                    e.target.style.height = 'auto';
                    e.target.style.height = `${e.target.scrollHeight}px`;
                  }}
                  onFocus={(e) => {
                    e.target.style.height = 'auto';
                    e.target.style.height = `${e.target.scrollHeight}px`;
                  }}
                  placeholder={t('card.charDescPlaceholder')}
                />
              </div>
            </div>
          </motion.div>
        ) : !chapterInfo.chapter ? (
          <motion.div
            key="empty-state"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="flex h-full items-center justify-center"
          >
            <div className="-translate-y-12 text-center">
              <div className="flex flex-col items-center gap-2">
                <span className="brand-logo text-[clamp(38px,5vw,64px)] leading-none text-ink-900/40">文枢</span>
                <span className="text-[11px] font-medium tracking-[0.18em] text-ink-500/70">Let the story unfold</span>
              </div>
            </div>
          </motion.div>
        ) : (
          <motion.div
            key="chapter-editor"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.3 }}
            className="h-full flex flex-col relative"
          >
            <div className="flex-1 overflow-hidden bg-[var(--vscode-bg)]">
              {isDiffReviewForActiveChapter ? (
                <DiffReviewView
                  ops={diffReview.ops}
                  hunks={diffReview.hunks}
                  stats={diffReview.stats}
                  decisions={diffDecisions}
                  onAcceptHunk={onAcceptDiffHunk}
                  onRejectHunk={onRejectDiffHunk}
                  originalVersion={t('writingSession.currentText')}
                  revisedVersion={t('writingSession.revisedText')}
                />
              ) : isStreamingForActiveChapter ? (
                <StreamingDiffView
                  originalContent={streamOriginalContent}
                  content={manualContent}
                  active={isStreamingForActiveChapter}
                  className="h-full"
                />
              ) : chapterLoading && !manualContent ? (
                <div className="flex h-full items-center justify-center text-sm text-[var(--vscode-fg-subtle)]">
                  {t('writingSession.loadingChapter')}
                </div>
              ) : chapterLoadError && !manualContent ? (
                <div className="flex h-full items-center justify-center px-8 text-center text-sm text-red-600">
                  {t('error.loadFailed')}
                </div>
              ) : (
                <textarea
                  ref={editorRef}
                  className="editor-canvas h-full w-full resize-none overflow-y-auto border-none bg-transparent px-[clamp(16px,3.5vw,56px)] py-5 font-serif text-[length:var(--editor-font-size,16px)] leading-[1.9] text-ink-900 outline-none placeholder:text-ink-300 focus:ring-0"
                  value={manualContent}
                  onChange={(e) =>
                    onManualContentChange(e.target.value, e.target.selectionStart, e.target.selectionEnd)
                  }
                  onSelect={(e) =>
                    onManualSelectionChange(e.target.value, e.target.selectionStart, e.target.selectionEnd)
                  }
                  onScroll={onEditorScroll}
                  placeholder={t('writingSession.writePlaceholder')}
                  disabled={!chapterInfo.chapter}
                  spellCheck={false}
                />
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    );

  return (
    // --editor-font-size 由标签栏右上角的加减号调整，向下贯穿正文编辑器、大纲与流式/差异视图，
    // 保证同一段文字在这几个视图之间切换时不跳字号。
    <div className="flex h-full min-h-0 flex-col" style={{ '--editor-font-size': `${fontSize}px` }}>
      <EditorTabs
        tabs={tabs}
        activeKey={tabStatusKeys?.active}
        unsavedKey={tabStatusKeys?.unsaved}
        streamingKey={tabStatusKeys?.streaming}
        diffKey={tabStatusKeys?.diff}
        fontSize={fontSize}
        showFontSize={activeDocument?.type === 'chapter' || activeDocument?.type === 'outline'}
        onFontSizeChange={onFontSizeChange}
        onSelect={onSelectTab}
        onClose={onCloseTab}
        onCloseOthers={onCloseOtherTabs}
        onRename={onRenameTab}
        t={t}
      />
      <div className="min-h-0 flex-1">{body}</div>
    </div>
  );
}

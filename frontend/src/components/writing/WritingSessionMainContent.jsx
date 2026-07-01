import { AnimatePresence, motion } from 'framer-motion';
import { X } from 'lucide-react';

import DiffReviewView from '../ide/DiffReviewView';
import { Input } from '../ui/core';
import FanfictionView from '../../pages/FanfictionView';
import { normalizeStars } from '../../utils/writingSessionHelpers';
import StreamingDiffView from './StreamingDiffView';

export default function WritingSessionMainContent({ vm }) {
  const {
    activeActivity,
    activeCard,
    cardForm,
    chapterInfo,
    diffDecisions,
    diffReview,
    dispatch,
    isDiffReviewForActiveChapter,
    isStreamingForActiveChapter,
    streamOriginalContent,
    lockedOnActiveChapter,
    manualContent,
    onAcceptDiffHunk,
    onCardFormChange,
    onCloseCardEditor,
    onManualContentChange,
    onManualSelectionChange,
    onRejectDiffHunk,
    setChapterInfo,
    status,
    t,
  } = vm;

  if (activeActivity === 'fanfiction') {
    return <FanfictionView embedded onClose={() => dispatch({ type: 'SET_ACTIVE_PANEL', payload: 'explorer' })} />;
  }

  return (
    <AnimatePresence mode="wait">
      {status === 'card_editing' && activeCard ? (
        <motion.div
          key="card-editor"
          initial={{ opacity: 0, scale: 0.98, y: 10 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.98, y: -10 }}
          transition={{ duration: 0.3, ease: 'easeOut' }}
          className="h-full flex flex-col max-w-3xl mx-auto w-full pt-4"
        >
          <div className="flex items-center justify-between mb-6 pb-4 border-b border-border">
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

          <div className="space-y-6 flex-1 overflow-y-auto px-1 pb-20">
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
          className="h-[60vh] flex items-center justify-center"
        >
          <div className="text-center">
            <div className="flex flex-col items-center gap-2 mb-4">
              <span className="brand-logo text-4xl text-ink-900/40">文枢</span>
            </div>
            <p className="text-sm text-ink-500">{t('writingSession.selectResourceHint')}</p>
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
          <div className="mb-4 pb-3 border-b border-border flex flex-wrap items-center gap-3">
            <span className="text-[11px] font-mono text-ink-500 uppercase tracking-wider">{chapterInfo.chapter}</span>
            <input
              className="flex-1 min-w-[200px] bg-transparent text-2xl font-serif font-bold text-ink-900 outline-none placeholder:text-ink-300"
              value={chapterInfo.chapter_title || ''}
              onChange={(e) => {
                setChapterInfo((prev) => ({ ...prev, chapter_title: e.target.value }));
                dispatch({ type: 'SET_UNSAVED' });
              }}
              placeholder={t('writingSession.chapterTitlePlaceholder')}
              disabled={!chapterInfo.chapter}
            />
          </div>
          <div className="flex-1 overflow-hidden bg-[var(--vscode-bg)] border-t border-[var(--vscode-sidebar-border)]">
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
            ) : (
              <textarea
                className="h-full w-full resize-none border-none outline-none bg-transparent p-6 text-base font-serif text-ink-900 leading-relaxed focus:ring-0 placeholder:text-ink-300 overflow-y-auto editor-scrollbar"
                value={manualContent}
                onChange={(e) => onManualContentChange(e.target.value, e.target.selectionStart, e.target.selectionEnd)}
                onSelect={(e) =>
                  onManualSelectionChange(e.target.value, e.target.selectionStart, e.target.selectionEnd)
                }
                placeholder={t('writingSession.writePlaceholder')}
                disabled={!chapterInfo.chapter || lockedOnActiveChapter}
                spellCheck={false}
              />
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

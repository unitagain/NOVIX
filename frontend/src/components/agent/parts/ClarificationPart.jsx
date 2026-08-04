/**
 * ClarificationPart —— Writer 反问的对话流内联卡（替代全屏 modal）。
 *
 * 后端语义不变（同一 Writer 工具集、调用即暂停、答案作为下一轮输入，见 plan.md §9.1）：
 * 本组件只改呈现，提交/跳过复用页面既有的文本拼装回调。
 * 提交后转为只读摘要保留在历史中，不消失。
 */
import React, { useState } from 'react';
import { MessageCircleQuestion, Check } from 'lucide-react';
import { useLocale } from '../../../i18n';

/** 已回答题数（用于只读摘要）。 */
export const answeredCount = (answers = []) =>
  answers.filter((answer) => String(answer || '').trim()).length;

export const ClarificationPart = ({ questions = [], reason = '', resolved = null, onConfirm, onSkip }) => {
  const { t } = useLocale();
  const [answers, setAnswers] = useState(() => questions.map((question) => String(question?.default || '')));

  const setAnswer = (index, value) =>
    setAnswers((prev) => {
      const next = [...prev];
      next[index] = value;
      return next;
    });

  if (resolved) {
    const label =
      resolved.kind === 'skipped'
        ? t('agentPanel.clarifySkippedSummary')
        : t('agentPanel.clarifyAnswered')
            .replace('{n}', resolved.answered ?? 0)
            .replace('{total}', questions.length);
    return (
      <div className="flex items-center gap-1.5 rounded-[6px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)] px-2 py-1 text-[11px] text-[var(--vscode-fg-subtle)]">
        <Check size={12} />
        <span>{label}</span>
      </div>
    );
  }

  return (
    <div className="rounded-[8px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)] overflow-hidden">
      <div className="flex items-center gap-1.5 border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)] px-3 py-2">
        <MessageCircleQuestion size={13} className="text-[var(--vscode-fg-subtle)]" />
        <span className="text-xs font-bold text-[var(--vscode-fg)]">{t('agentPanel.clarificationTitle')}</span>
      </div>
      <div className="space-y-3 px-3 py-2">
        {reason ? <p className="text-[11px] text-[var(--vscode-fg-subtle)]">{reason}</p> : null}
        {questions.map((question, index) => (
          <div key={question.key || `q-${index}`} className="space-y-1">
            <div className="text-[12px] text-[var(--vscode-fg)]">{question.text}</div>
            {question.reason ? (
              <div className="text-[10px] text-[var(--vscode-fg-subtle)]">
                {t('preWriting.reason')}：{question.reason}
              </div>
            ) : null}
            {Array.isArray(question.options) && question.options.length ? (
              <div className="flex flex-wrap gap-1">
                {question.options.map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => setAnswer(index, option)}
                    className={[
                      'px-2 h-6 rounded-[6px] border text-[10px] transition-colors',
                      answers[index] === option
                        ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] border-[var(--vscode-input-border)]'
                        : 'bg-[var(--vscode-input-bg)] text-[var(--vscode-fg)] border-[var(--vscode-sidebar-border)] hover:border-[var(--vscode-focus-border)]',
                    ].join(' ')}
                  >
                    {option}
                  </button>
                ))}
              </div>
            ) : null}
            <input
              value={answers[index] || ''}
              onChange={(event) => setAnswer(index, event.target.value)}
              placeholder={t('preWriting.answerPlaceholder')}
              className="w-full rounded-[6px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-bg)] px-2 py-1 text-[12px] text-[var(--vscode-fg)] focus:border-[var(--vscode-focus-border)] focus:outline-none"
            />
          </div>
        ))}
      </div>
      <div className="flex justify-end gap-2 px-3 pb-3">
        <button
          type="button"
          onClick={onSkip}
          className="rounded-[6px] border border-[var(--vscode-sidebar-border)] px-3 py-1.5 text-[10px] text-[var(--vscode-fg-subtle)] transition-colors hover:text-[var(--vscode-fg)]"
        >
          {t('agentPanel.clarifyInlineSkip')}
        </button>
        <button
          type="button"
          onClick={() =>
            onConfirm(
              questions.map((question, index) => ({
                type: question.type,
                question: question.text,
                key: question.key,
                answer: answers[index] || '',
              })),
            )
          }
          className="rounded-[6px] border border-green-200 bg-green-50 px-3 py-1.5 text-[10px] text-green-700 transition-colors hover:bg-green-100"
        >
          {t('agentPanel.clarifyInlineSubmit')}
        </button>
      </div>
    </div>
  );
};

export default ClarificationPart;

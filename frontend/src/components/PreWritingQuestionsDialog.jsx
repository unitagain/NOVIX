/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   写作前问题对话框 - 在生成初稿前收集关键创意指导信息
 *   Pre-writing questions dialog for collecting key guidance before generation.
 */

import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Button, Card, Input } from './ui/core';
import { useLocale } from '../i18n';

/**
 * 写作前问题确认对话框 - 收集创意指导与关键设定
 *
 * Modal dialog displayed before draft generation to collect key information
 * and creative guidance. Helps ensure the writer agent receives sufficient context.
 *
 * @component
 * @example
 * return (
 *   <PreWritingQuestionsDialog
 *     open={true}
 *     questions={['场景设定?', '主要冲突?']}
 *     onConfirm={handleConfirm}
 *     onSkip={handleSkip}
 *   />
 * )
 *
 * @param {Object} props - Component props
 * @param {boolean} [props.open=false] - 对话框是否打开 / Whether dialog is open
 * @param {Array} [props.questions=[]] - 问题列表 / List of questions to ask
 * @param {Function} [props.onConfirm] - 确认回调，传递答案数组 / Confirm callback with answers
 * @param {Function} [props.onSkip] - 跳过回调 / Skip callback
 * @param {string} [props.title='写作前确认'] - 对话框标题 / Dialog title
 * @param {string} [props.subtitle='先回答几个关键问题，帮助主笔精准开写。'] - 子标题 / Subtitle
 * @param {string} [props.confirmText='开始撰写'] - 确认按钮文本 / Confirm button text
 * @param {string} [props.skipText='跳过'] - 跳过按钮文本 / Skip button text
 * @returns {JSX.Element} 写作前问题对话框 / Pre-writing questions dialog
 */
export default function PreWritingQuestionsDialog({
  open,
  questions = [],
  onConfirm,
  onSkip,
  title,
  subtitle,
  confirmText,
  skipText,
}) {
  const { t } = useLocale();
  const resolvedTitle = title ?? t('preWriting.title');
  const resolvedSubtitle = subtitle ?? t('preWriting.subtitle');
  const resolvedConfirmText = confirmText ?? t('preWriting.confirmText');
  const resolvedSkipText = skipText ?? t('preWriting.skipText');
  const [answers, setAnswers] = useState([]);

  useEffect(() => {
    if (!open) return;
    setAnswers((questions || []).map(() => ''));
  }, [open, questions]);

  const handleChange = (index, value) => {
    setAnswers((prev) => {
      const next = [...(prev || [])];
      next[index] = value;
      return next;
    });
  };

  const handleConfirm = () => {
    if (onConfirm) {
      const payload = questions.map((q, index) => ({
        type: q.type,
        question: q.text,
        key: q.key,
        answer: answers[index] || '',
      }));
      onConfirm(payload);
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/30 z-40"
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96 }}
            className="fixed inset-0 z-50 flex items-center justify-center p-4 anti-theme"
          >
            <Card className="w-full max-w-2xl p-6 space-y-5">
              <div className="space-y-1">
                <h2 className="text-xl font-bold text-[var(--vscode-fg)]">{resolvedTitle}</h2>
                <p className="text-sm text-[var(--vscode-fg-subtle)]">{resolvedSubtitle}</p>
              </div>

              <div className="space-y-4">
                {questions.map((q, index) => (
                  <div key={`${q.type || 'q'}-${index}`} className="space-y-2">
                    <div className="text-sm font-semibold text-[var(--vscode-fg)]">{q.text}</div>
                    {q.reason && (
                      <div className="text-xs text-[var(--vscode-fg-subtle)]">
                        {t('preWriting.reason')}：{q.reason}
                      </div>
                    )}
                    <Input
                      value={answers[index] || ''}
                      onChange={(e) => handleChange(index, e.target.value)}
                      placeholder={t('preWriting.answerPlaceholder')}
                      className="bg-[var(--vscode-input-bg)]"
                    />
                  </div>
                ))}
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <Button variant="ghost" onClick={onSkip}>
                  {resolvedSkipText}
                </Button>
                <Button onClick={handleConfirm}>{resolvedConfirmText}</Button>
              </div>
            </Card>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

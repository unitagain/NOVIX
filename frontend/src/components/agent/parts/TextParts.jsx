/**
 * NarrationPart / AnswerPart / MetaPart —— 四级视觉层级中的后三级（plan.md §9.5 Step 5）。
 *
 *   narration  中：正文色小字段落，无标题无项目符号（agent 工作时的即时说明）
 *   answer     最强：全宽文档式、悬停复制（本轮最终答复）
 *   meta       系统/错误小行，低存在感
 */
import React, { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { useLocale } from '../../../i18n';

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

export const NarrationPart = ({ part }) => {
  const paragraphs = String(part?.text || '')
    .split(/\n{2,}/)
    .map((item) => item.trim())
    .filter(Boolean);
  if (!paragraphs.length) return null;
  return (
    <div className="space-y-1 text-xs leading-relaxed text-[var(--vscode-fg-subtle)]">
      {paragraphs.map((paragraph, index) => (
        <p key={`${index}-${paragraph.slice(0, 24)}`} className="whitespace-pre-wrap break-words">
          {paragraph}
        </p>
      ))}
    </div>
  );
};

export const AnswerPart = ({ part }) => (
  <div className="group relative text-xs leading-relaxed text-[var(--vscode-fg)] whitespace-pre-wrap break-words pr-5">
    {part?.text}
    <span className="absolute top-0 right-0">
      <CopyButton text={part?.text} />
    </span>
  </div>
);

export const MetaPart = ({ part }) => {
  const { t } = useLocale();
  const stageKey = `agentPanel.stageLabels.${part?.type}`;
  const stageLabel = t(stageKey);
  const label = stageLabel === stageKey ? '' : stageLabel;
  const isError = part?.type === 'error';
  return (
    <div
      className={[
        'text-[11px] px-2 py-1 rounded-[6px] border',
        isError
          ? 'bg-red-50 text-red-700 border-red-200'
          : 'bg-[var(--vscode-input-bg)] text-[var(--vscode-fg-subtle)] border-[var(--vscode-sidebar-border)] font-mono',
      ].join(' ')}
    >
      {label && !isError ? <span className="mr-1.5 opacity-70">{label}</span> : null}
      {part?.text}
    </div>
  );
};

export default NarrationPart;

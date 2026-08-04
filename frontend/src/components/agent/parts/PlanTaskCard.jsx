/**
 * PlanTaskCard —— 计划执行卡（Task 形态）。
 *
 * 执行中显示「第 N / M 步」与当前步高亮（业内反模式：opaque percentage progress），
 * 已完成步折叠为一行摘要；执行后按 per-step status 渲染终态。
 * interrupted 必须显式呈现，不得伪装完成（对齐 §4「incomplete 不伪装 completed」）。
 */
import React from 'react';
import { Check, X, Loader2, CircleDashed, CircleSlash } from 'lucide-react';
import { useLocale } from '../../../i18n';

/**
 * 计划终态判定：后端 per-step status 是唯一真相源。
 *  running      执行中
 *  failed       任一步 failed
 *  interrupted  未失败但仍有未完成步（取消/中断）
 *  done         全部完成
 */
export const planOutcome = (steps = [], { executing = false } = {}) => {
  if (executing) return 'running';
  if (!steps.length) return 'pending';
  if (steps.some((step) => step?.status === 'failed')) return 'failed';
  if (steps.every((step) => step?.status === 'done')) return 'done';
  return 'interrupted';
};

/** 当前执行到第几步（1-based）；无进行中步骤时返回已完成数。 */
export const currentStepIndex = (steps = [], activeStepId = null) => {
  if (activeStepId !== null && activeStepId !== undefined) {
    const index = steps.findIndex((step) => String(step?.id) === String(activeStepId));
    if (index >= 0) return index + 1;
  }
  return steps.filter((step) => step?.status === 'done').length;
};

const STEP_ICONS = {
  done: Check,
  failed: X,
  running: Loader2,
  pending: CircleDashed,
};

const StepRow = ({ step, index, isCurrent, collapsed }) => {
  const status = isCurrent ? 'running' : step?.status || 'pending';
  const Icon = STEP_ICONS[status] || CircleDashed;
  return (
    <div
      className={[
        'flex items-start gap-2 rounded-[4px] px-1 py-0.5 text-[11px]',
        isCurrent ? 'bg-[var(--vscode-list-hover)] text-[var(--vscode-fg)]' : '',
        collapsed ? 'text-[var(--vscode-fg-subtle)]' : 'text-[var(--vscode-fg)]',
      ].join(' ')}
    >
      <span className="shrink-0 font-mono text-[var(--vscode-fg-subtle)]">{index + 1}.</span>
      <Icon
        size={11}
        className={[
          'mt-0.5 shrink-0',
          status === 'failed' ? 'text-red-600' : 'text-[var(--vscode-fg-subtle)]',
          status === 'running' ? 'animate-spin' : '',
        ].join(' ')}
      />
      {step?.action ? (
        <span className="shrink-0 rounded-[4px] bg-[var(--vscode-list-hover)] px-1.5 text-[10px] text-[var(--vscode-fg-subtle)]">
          {step.action}
        </span>
      ) : null}
      <span className="min-w-0 break-words">
        {step?.description}
        {step?.chapter ? ` · ${step.chapter}` : ''}
        {step?.status === 'failed' && step?.error ? (
          <span className="ml-1 text-red-600">{step.error}</span>
        ) : null}
      </span>
    </div>
  );
};

export const PlanTaskCard = ({ plan, executing = false, activeStepId = null, onExecute, onDismiss }) => {
  const { t } = useLocale();
  const steps = Array.isArray(plan?.steps) ? plan.steps : [];
  const outcome = planOutcome(steps, { executing });
  const current = currentStepIndex(steps, activeStepId);

  return (
    <div className="my-2 overflow-hidden rounded-[6px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)]">
      <div className="border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)] px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <div className="text-xs font-bold text-[var(--vscode-fg)]">{t('agentPanel.planTitle')}</div>
          {outcome === 'running' ? (
            <div className="text-[10px] font-mono text-[var(--vscode-fg-subtle)]">
              {t('agentPanel.planStep').replace('{n}', current).replace('{total}', steps.length)}
            </div>
          ) : null}
          {outcome === 'interrupted' ? (
            <div className="inline-flex items-center gap-1 text-[10px] text-red-600">
              <CircleSlash size={11} />
              <span>
                {t('agentPanel.planInterrupted')
                  .replace('{n}', steps.filter((step) => step?.status === 'done').length)
                  .replace('{total}', steps.length)}
              </span>
            </div>
          ) : null}
          {outcome === 'done' ? (
            <div className="inline-flex items-center gap-1 text-[10px] text-[var(--vscode-fg-subtle)]">
              <Check size={11} />
              <span>{t('agentPanel.planStepDone').replace('{total}', steps.length)}</span>
            </div>
          ) : null}
        </div>
        {plan?.goal ? (
          <div className="mt-1 truncate text-[10px] text-[var(--vscode-fg-subtle)]">{plan.goal}</div>
        ) : null}
      </div>
      <div className="custom-scrollbar max-h-48 space-y-1 overflow-y-auto px-3 py-2">
        {steps.map((step, index) => (
          <StepRow
            key={step?.id ?? index}
            step={step}
            index={index}
            isCurrent={outcome === 'running' && index + 1 === current}
            collapsed={step?.status === 'done' && outcome === 'running'}
          />
        ))}
      </div>
      {onExecute || onDismiss ? (
        <div className="flex flex-wrap gap-2 px-3 pb-3 pt-1">
          <button
            type="button"
            onClick={onDismiss}
            disabled={executing}
            className="rounded-[6px] border border-[var(--vscode-sidebar-border)] px-3 py-1.5 text-[10px] text-[var(--vscode-fg-subtle)] transition-colors hover:text-[var(--vscode-fg)] disabled:opacity-50"
          >
            {t('agentPanel.planDismiss')}
          </button>
          <button
            type="button"
            onClick={onExecute}
            disabled={executing}
            className="inline-flex items-center gap-1 rounded-[6px] border border-green-200 px-3 py-1.5 text-[10px] text-green-700 transition-colors hover:bg-green-50 disabled:opacity-50"
          >
            {executing ? <Loader2 size={12} className="animate-spin" /> : null}
            {executing ? t('agentPanel.planRunning') : t('agentPanel.planExecute')}
          </button>
        </div>
      ) : null}
    </div>
  );
};

export default PlanTaskCard;

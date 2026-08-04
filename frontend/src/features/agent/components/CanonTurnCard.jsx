import { AlertTriangle, BookOpen, Check, Clock3, Loader2, Minus } from 'lucide-react';
import { useLocale } from '../../../i18n';

export function canonTurnVisualState(status) {
  if (status === 'saving' || status === 'syncing') return 'syncing';
  if (status === 'applied') return 'applied';
  if (status === 'failed') return 'failed';
  if (status === 'pending_acceptance') return 'pending';
  return 'skipped';
}

export function CanonTurnCard({ state = null }) {
  const { t } = useLocale();
  if (!state?.effect) return null;

  const visualState = canonTurnVisualState(state.status);
  const effect = state.effect || {};
  const candidates = Array.isArray(effect.fact_candidates) ? effect.fact_candidates : [];
  const factsSaved = Number(state.result?.stats?.facts_saved || 0);
  const rejected = Number(state.result?.stats?.facts_rejected_evidence || 0);
  const StateIcon =
    visualState === 'syncing'
      ? Loader2
      : visualState === 'applied'
        ? Check
        : visualState === 'failed'
          ? AlertTriangle
          : visualState === 'pending'
            ? Clock3
            : Minus;

  return (
    <details className="my-2 overflow-hidden rounded-[8px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)]">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 hover:bg-[var(--vscode-list-hover)]">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] bg-emerald-50 text-emerald-600">
          <BookOpen size={13} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-xs font-semibold text-[var(--vscode-fg)]">{t('agentPanel.canonTurn')}</span>
          <span className="block truncate text-[10px] text-[var(--vscode-fg-subtle)]">
            {t(`agentPanel.canonChangeTypes.${effect.change_type || 'conversation'}`)}
            {candidates.length ? ` · ${t('agentPanel.canonCandidates').replace('{count}', candidates.length)}` : ''}
          </span>
        </span>
        <span className="inline-flex shrink-0 items-center gap-1 text-[10px] text-[var(--vscode-fg-subtle)]">
          <StateIcon size={12} className={visualState === 'syncing' ? 'animate-spin' : ''} />
          {t(`agentPanel.canonTurnStates.${visualState}`)}
        </span>
      </summary>
      <div className="space-y-2 border-t border-[var(--vscode-sidebar-border)] px-3 py-2 text-[11px] text-[var(--vscode-fg-subtle)]">
        <div>
          {t('agentPanel.canonOperation').replace(
            '{operation}',
            t(`agentPanel.canonOperations.${effect.fact_operation || 'none'}`),
          )}
        </div>
        {effect.chapter_summary ? <div className="leading-relaxed">{effect.chapter_summary}</div> : null}
        {candidates.length ? (
          <div className="space-y-1">
            {candidates.map((item, index) => (
              <div key={`${item?.statement || 'fact'}-${index}`} className="rounded-[5px] bg-[var(--vscode-list-hover)] px-2 py-1">
                {item?.statement}
              </div>
            ))}
          </div>
        ) : null}
        {state.result ? (
          <div>{t('agentPanel.canonResult').replace('{saved}', factsSaved).replace('{rejected}', rejected)}</div>
        ) : null}
      </div>
    </details>
  );
}

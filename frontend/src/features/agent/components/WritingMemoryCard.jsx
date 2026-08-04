import { AlertTriangle, Check, Database, Loader2 } from 'lucide-react';
import { useLocale } from '../../../i18n';

export function getWritingMemoryState(status, loading = false) {
  if (loading || !status) return 'loading';
  const turnContext = status.turn_context || {};
  const hasTurnContext = ['available', 'retrieved', 'used'].some(
    (key) => Array.isArray(turnContext[key]) && turnContext[key].length > 0,
  );
  if (!status.exists) return hasTurnContext ? 'context_only' : 'missing';
  return status.stale ? 'stale' : 'ready';
}

const formatBuiltAt = (value) => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
};

export function WritingMemoryCard({ status = null, loading = false, chapter = '' }) {
  const { t } = useLocale();
  if (!chapter) return null;

  const state = getWritingMemoryState(status, loading);
  const evidenceTotal = status?.evidence_stats?.total;
  const evidenceTypes = status?.evidence_stats?.types || {};
  const builtAt = formatBuiltAt(status?.built_at);
  const source = String(status?.source || '');
  const staleReasons = Array.isArray(status?.stale_reasons) ? status.stale_reasons : [];
  const turnContextTypes = Array.from(
    new Set([
      ...(status?.turn_context?.used || []),
      ...(status?.turn_context?.retrieved || []),
      ...(status?.turn_context?.available || []),
    ]),
  );
  const stateLabel = t(`agentPanel.writingMemoryStates.${state}`);
  const StateIcon = state === 'loading' ? Loader2 : state === 'ready' ? Check : state === 'context_only' ? Database : AlertTriangle;

  return (
    <details className="my-2 overflow-hidden rounded-[8px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)]">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 hover:bg-[var(--vscode-list-hover)]">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] bg-violet-50 text-violet-600">
          <Database size={13} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-xs font-semibold text-[var(--vscode-fg)]">{t('agentPanel.writingMemory')}</span>
          <span className="block truncate text-[10px] text-[var(--vscode-fg-subtle)]">
            {[builtAt, typeof evidenceTotal === 'number' ? t('agentPanel.memoryPackEvidence').replace('{count}', evidenceTotal) : '', source]
              .filter(Boolean)
              .join(' · ') || t('agentPanel.writingMemoryHint')}
          </span>
        </span>
        <span className="inline-flex shrink-0 items-center gap-1 text-[10px] text-[var(--vscode-fg-subtle)]">
          <StateIcon size={12} className={state === 'loading' ? 'animate-spin' : ''} />
          {stateLabel}
        </span>
      </summary>
      <div className="space-y-2 border-t border-[var(--vscode-sidebar-border)] px-3 py-2 text-[11px] text-[var(--vscode-fg-subtle)]">
        <div>{t('agentPanel.writingMemoryChapter').replace('{chapter}', chapter)}</div>
        {Object.keys(evidenceTypes).length ? (
          <div className="flex flex-wrap gap-1">
            {Object.entries(evidenceTypes).map(([type, count]) => (
              <span key={type} className="rounded-[5px] bg-[var(--vscode-list-hover)] px-1.5 py-0.5 font-mono text-[10px]">
                {type} · {count}
              </span>
            ))}
          </div>
        ) : null}
        {turnContextTypes.length ? (
          <div>
            <div className="mb-1">{t('agentPanel.writingMemoryTurnContext')}</div>
            <div className="flex flex-wrap gap-1">
              {turnContextTypes.map((type) => (
                <span key={type} className="rounded-[5px] bg-[var(--vscode-list-hover)] px-1.5 py-0.5 font-mono text-[10px]">
                  {type}
                </span>
              ))}
            </div>
          </div>
        ) : null}
        {staleReasons.length ? (
          <div>{t('agentPanel.writingMemoryStaleReasons').replace('{reasons}', staleReasons.join(', '))}</div>
        ) : null}
      </div>
    </details>
  );
}

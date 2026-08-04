/**
 * FactsEncyclopedia - 事实全典
 * 仅做视觉一致性优化，不改变数据与交互逻辑。
 */
import { useMemo, useState } from 'react';
import useSWR from 'swr';
import { ChevronDown, ChevronRight, Loader2, Pencil, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { SidebarPanelHeader } from './SidebarPanelHeader';
import { useParams } from 'react-router-dom';
import { bindingsAPI, canonAPI, draftsAPI, volumesAPI } from '../../api';
import { Button, Card, Input, cn } from '../ui/core';
import logger from '../../utils/logger';
import { extractErrorDetail } from '../../utils/extractError';
import { useLocale } from '../../i18n';

const FactsEncyclopedia = ({ projectId: overrideProjectId, onFactSelect }) => {
  const { t } = useLocale();
  const { projectId: routeProjectId } = useParams();
  const projectId = overrideProjectId || routeProjectId;

  const [expandedChapters, setExpandedChapters] = useState(new Set());
  const [expandedSummaries, setExpandedSummaries] = useState(new Set());
  const [expandedFacts, setExpandedFacts] = useState(new Set());
  const [editingFact, setEditingFact] = useState(null);
  const [editingSummary, setEditingSummary] = useState(null);
  const [creatingFact, setCreatingFact] = useState(null);
  const [refreshingSummary, setRefreshingSummary] = useState(false);

  const {
    data: factsTree = { volumes: [] },
    isLoading,
    mutate,
  } = useSWR(projectId ? [projectId, 'facts-tree'] : null, () => canonAPI.getTree(projectId).then((res) => res.data), {
    revalidateOnFocus: false,
  });

  const getChapterWeight = (chapterId) => {
    const normalized = (chapterId || '').toUpperCase();
    const match = normalized.match(/^(?:V(\d+))?C(\d+)(?:([EI])(\d+))?$/);
    if (!match) return Number.MAX_SAFE_INTEGER;

    const volume = match[1] ? Number.parseInt(match[1], 10) : 0;
    const chapter = Number.parseInt(match[2], 10);
    const type = match[3];
    const seq = match[4] ? Number.parseInt(match[4], 10) : 0;

    let weight = volume * 100000 + chapter * 10;
    if (type && seq) weight += seq;
    return weight;
  };

  const stats = useMemo(() => {
    const volumes = factsTree?.volumes || [];
    let chapterCount = 0;
    let factCount = 0;

    volumes.forEach((v) => {
      (v.chapters || []).forEach((c) => {
        chapterCount += 1;
        factCount += (c.facts || []).length;
      });
    });

    return { chapterCount, factCount };
  }, [factsTree]);

  const filteredTree = useMemo(() => {
    const rawVolumes = factsTree?.volumes || [];
    const volumes = rawVolumes.map((v) => ({
      ...v,
      chapters: [...(v.chapters || [])].sort((a, b) => getChapterWeight(a.id) - getChapterWeight(b.id)),
    }));
    return { volumes };
  }, [factsTree]);

  const toggleChapter = (chapterId) => {
    const next = new Set(expandedChapters);
    if (next.has(chapterId)) next.delete(chapterId);
    else next.add(chapterId);
    setExpandedChapters(next);
  };

  const toggleSummary = (key) => {
    const next = new Set(expandedSummaries);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setExpandedSummaries(next);
  };

  const toggleFact = (factKey) => {
    setExpandedFacts((prev) => {
      const next = new Set(prev);
      if (next.has(factKey)) next.delete(factKey);
      else next.add(factKey);
      return next;
    });
  };

  const handleDeleteFact = async (factId) => {
    if (!factId) {
      alert(t('facts.noIdError'));
      return;
    }
    if (!window.confirm(t('facts.deleteConfirm'))) return;

    try {
      await canonAPI.delete(projectId, factId);
      mutate();
    } catch (error) {
      logger.error(error);
      alert(t('facts.deleteFailed') + ': ' + extractErrorDetail(error));
    }
  };

  const handleSaveFact = async () => {
    if (!editingFact) return;

    try {
      const payload = {
        ...editingFact,
        statement: editingFact.content || editingFact.statement || '',
      };
      await canonAPI.update(projectId, editingFact.id, payload);
      setEditingFact(null);
      mutate();
    } catch (error) {
      logger.error(error);
      alert(t('facts.saveFailed') + ': ' + extractErrorDetail(error));
    }
  };

  const handleCreateFact = async () => {
    if (!creatingFact) return;

    const statement = (creatingFact.content || creatingFact.statement || '').trim();
    if (!statement) {
      alert(t('facts.contentRequired'));
      return;
    }

    try {
      const payload = {
        title: creatingFact.title || undefined,
        content: creatingFact.content || statement,
        statement,
        source: creatingFact.chapterId,
        introduced_in: creatingFact.chapterId,
        confidence: 1.0,
      };
      await canonAPI.createManual(projectId, payload);
      setCreatingFact(null);
      mutate();
    } catch (error) {
      logger.error(error);
      alert(t('facts.addFailed') + ': ' + extractErrorDetail(error));
    }
  };

  const openCreateFactDialog = (chapterId) => {
    setCreatingFact({
      chapterId,
      title: '',
      content: '',
    });
  };

  const handleSaveSummary = async () => {
    if (!editingSummary) return;

    try {
      if (editingSummary.type === 'volume') {
        let existing = null;
        try {
          const res = await volumesAPI.getSummary(projectId, editingSummary.id);
          existing = res.data;
        } catch (_error) {
          existing = null;
        }

        const payload = {
          volume_id: editingSummary.id,
          brief_summary: editingSummary.text || '',
          key_themes: existing?.key_themes || [],
          major_events: existing?.major_events || [],
          chapter_count: existing?.chapter_count || editingSummary.chapterCount || 0,
        };

        await volumesAPI.saveSummary(projectId, editingSummary.id, payload);
      } else {
        let existing = null;
        try {
          const res = await draftsAPI.getSummary(projectId, editingSummary.id);
          existing = res.data;
        } catch (_error) {
          existing = null;
        }

        const payload = {
          chapter: editingSummary.id,
          volume_id: existing?.volume_id || editingSummary.volumeId || 'V1',
          title: existing?.title || editingSummary.title || editingSummary.id,
          word_count: existing?.word_count || 0,
          key_events: existing?.key_events || [],
          new_facts: existing?.new_facts || [],
          character_state_changes: existing?.character_state_changes || [],
          open_loops: existing?.open_loops || [],
          brief_summary: editingSummary.text || '',
        };

        await draftsAPI.saveSummary(projectId, editingSummary.id, payload);
      }

      setEditingSummary(null);
      mutate();
    } catch (error) {
      logger.error(error);
      alert(t('facts.saveFailed') + ': ' + extractErrorDetail(error));
    }
  };

  if (isLoading) {
    return (
      <div className="anti-theme h-full flex items-center justify-center text-[var(--vscode-fg-subtle)] text-xs bg-[var(--vscode-bg)]">
        {t('common.loading')}
      </div>
    );
  }

  return (
    <div className="anti-theme h-full flex flex-col overflow-hidden bg-[var(--vscode-bg)] text-[var(--vscode-fg)] text-[12px]">
      <SidebarPanelHeader
        title={t('facts.encyclopediaTitle')}
        meta={`${stats.chapterCount} ${t('facts.chapterUnit')} · ${stats.factCount} ${t('facts.factUnit')}`}
        actions={
          <button
            onClick={() => setRefreshingSummary(true)}
            className="p-2 rounded-[6px] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-none"
            title={t('facts.refreshSummaryBtn')}
          >
            <RefreshCw size={14} />
          </button>
        }
      />

      <div className="flex-1 overflow-hidden relative">
        <div className="h-full overflow-y-auto custom-scrollbar facts-scroll p-2 space-y-3">
          {(filteredTree.volumes || []).length === 0 ? (
            <div className="p-6 text-center text-xs text-[var(--vscode-fg-subtle)] border border-dashed border-[var(--vscode-sidebar-border)] rounded-[6px] bg-[var(--vscode-bg)]">
              {t('facts.noFacts')}
            </div>
          ) : (
            filteredTree.volumes.map((volume) => (
              <VolumeSection
                key={volume.id}
                projectId={projectId}
                volume={volume}
                expandedChapters={expandedChapters}
                expandedSummaries={expandedSummaries}
                expandedFacts={expandedFacts}
                onToggleChapter={toggleChapter}
                onToggleSummary={toggleSummary}
                onToggleFact={toggleFact}
                onEditSummary={(payload) => setEditingSummary(payload)}
                onEditFact={(fact) => setEditingFact(fact)}
                onAddFact={openCreateFactDialog}
                onDeleteFact={handleDeleteFact}
                onFactSelect={onFactSelect}
              />
            ))
          )}
        </div>
      </div>

      <EditFactDialog
        open={Boolean(editingFact)}
        fact={editingFact}
        onChange={setEditingFact}
        onClose={() => setEditingFact(null)}
        onSave={handleSaveFact}
      />

      <CreateFactDialog
        open={Boolean(creatingFact)}
        fact={creatingFact}
        onChange={setCreatingFact}
        onClose={() => setCreatingFact(null)}
        onSave={handleCreateFact}
      />

      <EditSummaryDialog
        open={Boolean(editingSummary)}
        summary={editingSummary}
        onChange={setEditingSummary}
        onClose={() => setEditingSummary(null)}
        onSave={handleSaveSummary}
      />

      <RefreshSummaryDialog
        open={refreshingSummary}
        volumes={filteredTree.volumes || []}
        projectId={projectId}
        onClose={() => setRefreshingSummary(false)}
        onRefreshed={() => mutate()}
      />
    </div>
  );
};

function VolumeSection({
  projectId,
  volume,
  expandedChapters,
  expandedSummaries,
  expandedFacts,
  onToggleChapter,
  onToggleSummary,
  onToggleFact,
  onEditSummary,
  onEditFact,
  onAddFact,
  onDeleteFact,
  onFactSelect,
}) {
  const { t } = useLocale();
  const volumeSummaryKey = `volume-${volume.id}`;
  const volumeSummaryExpanded = expandedSummaries.has(volumeSummaryKey);
  const chapters = volume.chapters || [];

  return (
    <Card className="overflow-hidden bg-[var(--vscode-bg)] text-[var(--vscode-fg)] border border-[var(--vscode-sidebar-border)] rounded-[4px] shadow-none">
      <div className="px-3 py-2 border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)]">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-mono text-[var(--vscode-fg-subtle)]">{volume.id}</span>
          <span className="text-sm font-semibold text-[var(--vscode-fg)] truncate">{volume.title}</span>
          <span className="ml-auto text-[10px] text-[var(--vscode-fg-subtle)] tabular-nums">{chapters.length}</span>
        </div>

        <div className="mt-2 flex items-start gap-1">
          <span className="text-[10px] text-[var(--vscode-fg-subtle)] shrink-0">{t('common.summary')}</span>
          <button
            className="flex-1 text-left text-[11px] text-[var(--vscode-fg)] leading-snug"
            onClick={() => onToggleSummary(volumeSummaryKey)}
            title={volume.summary || ''}
          >
            <span className={volumeSummaryExpanded ? '' : 'line-clamp-2'}>
              {volume.summary && volume.summary.trim() ? volume.summary : t('facts.noSummary')}
            </span>
          </button>

          <div className="flex items-center gap-1">
            <button
              onClick={() =>
                onEditSummary({
                  type: 'volume',
                  id: volume.id,
                  title: volume.title,
                  text: volume.summary || '',
                  chapterCount: chapters.length,
                })
              }
              className="p-2 rounded-[6px] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-none"
              title={t('facts.editSummary')}
            >
              <Pencil size={14} />
            </button>
            <button
              onClick={() => onToggleSummary(volumeSummaryKey)}
              className="p-2 rounded-[6px] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-none"
              title={volumeSummaryExpanded ? t('common.collapse') : t('common.expand')}
            >
              <ChevronDown size={14} />
            </button>
          </div>
        </div>
      </div>

      <div className="p-2 space-y-2">
        {chapters.map((chapter) => (
          <ChapterBlock
            key={chapter.id}
            projectId={projectId}
            volumeId={volume.id}
            chapter={chapter}
            isExpanded={expandedChapters.has(chapter.id)}
            summaryExpanded={expandedSummaries.has(`chapter-${chapter.id}`)}
            expandedFacts={expandedFacts}
            onToggleChapter={onToggleChapter}
            onToggleSummary={onToggleSummary}
            onEditSummary={onEditSummary}
            onEditFact={onEditFact}
            onAddFact={onAddFact}
            onDeleteFact={onDeleteFact}
            onToggleFact={onToggleFact}
            onFactSelect={onFactSelect}
          />
        ))}
      </div>
    </Card>
  );
}

function ChapterBlock({
  projectId,
  volumeId,
  chapter,
  isExpanded,
  summaryExpanded,
  expandedFacts,
  onToggleChapter,
  onToggleSummary,
  onEditSummary,
  onEditFact,
  onAddFact,
  onDeleteFact,
  onToggleFact,
  onFactSelect,
}) {
  const { t } = useLocale();
  const facts = chapter.facts || [];
  const chapterSummaryKey = `chapter-${chapter.id}`;

  const { data: bindingResp, isLoading: bindingLoading } = useSWR(
    isExpanded && projectId ? [projectId, chapter.id, 'bindings'] : null,
    () => bindingsAPI.get(projectId, chapter.id).then((res) => res.data),
    { revalidateOnFocus: false },
  );

  const binding = bindingResp?.binding;
  const boundCharacters = binding?.characters || [];
  const charactersText = binding
    ? boundCharacters.length
      ? boundCharacters.join('、')
      : t('common.none')
    : t('facts.noBinding');

  return (
    <div className="border border-[var(--vscode-sidebar-border)] rounded-[4px] bg-[var(--vscode-bg)] overflow-hidden">
      <div
        onClick={() => onToggleChapter(chapter.id)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onToggleChapter(chapter.id);
          }
        }}
        role="button"
        tabIndex={0}
        className={cn(
          'w-full flex items-center gap-2 px-2 py-1.5 text-left transition-none',
          'hover:bg-[var(--vscode-list-hover)]',
        )}
      >
        <span className={cn('text-[var(--vscode-fg-subtle)] inline-flex', isExpanded ? 'rotate-90' : '')} aria-hidden>
          <ChevronRight size={14} />
        </span>

        <span className="text-[10px] font-mono text-[var(--vscode-fg-subtle)]">{chapter.id}</span>
        <span className="text-[12px] text-[var(--vscode-fg)] truncate flex-1">
          {chapter.title || t('chapter.noTitle')}
        </span>

        <div className="flex items-center gap-1">
          <button
            type="button"
            className="p-1 rounded-[4px] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-none"
            title={t('facts.addFactBtn')}
            onClick={(e) => {
              e.stopPropagation();
              onAddFact?.(chapter.id);
              if (!isExpanded) onToggleChapter(chapter.id);
            }}
          >
            <Plus size={14} />
          </button>
          <span className="text-[10px] text-[var(--vscode-fg-subtle)] tabular-nums">{facts.length}</span>
        </div>
      </div>

      {isExpanded && (
        <div className="border-t border-[var(--vscode-sidebar-border)] bg-[var(--vscode-bg)] overflow-hidden">
          <div className="px-2 py-2 flex items-start gap-1">
            <span className="text-[10px] text-[var(--vscode-fg-subtle)] shrink-0">{t('common.summary')}</span>
            <button
              className="flex-1 text-left text-[11px] text-[var(--vscode-fg)] leading-snug"
              onClick={() => onToggleSummary(chapterSummaryKey)}
              title={chapter.summary || ''}
            >
              <span className={summaryExpanded ? '' : 'line-clamp-2'}>
                {chapter.summary && chapter.summary.trim() ? chapter.summary : t('facts.noSummary')}
              </span>
            </button>

            <div className="flex items-center gap-1">
              <button
                onClick={() =>
                  onEditSummary({
                    type: 'chapter',
                    id: chapter.id,
                    title: chapter.title,
                    volumeId,
                    text: chapter.summary || '',
                  })
                }
                className="p-1 rounded-[4px] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-none"
                title={t('facts.editSummary')}
              >
                <Pencil size={12} />
              </button>
              <button
                onClick={() => onToggleSummary(chapterSummaryKey)}
                className="p-1 rounded-[4px] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-none"
                title={summaryExpanded ? t('common.collapse') : t('common.expand')}
              >
                <ChevronDown size={12} />
              </button>
            </div>
          </div>

          <div className="px-2 pb-2 flex items-start gap-1">
            <span className="text-[10px] text-[var(--vscode-fg-subtle)] shrink-0">{t('common.characters')}</span>
            <div
              className="flex-1 text-[11px] text-[var(--vscode-fg)] leading-snug truncate"
              title={bindingLoading ? t('common.loading') : charactersText}
            >
              {bindingLoading ? t('common.loading') : charactersText}
            </div>
          </div>

          <div className="px-2 pb-2">
            {facts.length ? (
              <div className="space-y-1">
                {facts.map((fact, idx) => {
                  const factKey = fact.id || `${chapter.id}-${idx}`;
                  const factExpanded = expandedFacts?.has(factKey);
                  return (
                    <FactRow
                      key={factKey}
                      fact={fact}
                      index={idx + 1}
                      expanded={factExpanded}
                      onToggleExpand={() => onToggleFact?.(factKey)}
                      onEdit={() => onEditFact(fact)}
                      onDelete={() => onDeleteFact(fact.id)}
                      onSelect={onFactSelect ? () => onFactSelect(fact) : null}
                    />
                  );
                })}
              </div>
            ) : (
              <div className="px-2 py-3 text-[11px] text-[var(--vscode-fg-subtle)]">{t('facts.empty')}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function FactRow({ fact, index, expanded, onToggleExpand, onEdit, onDelete, onSelect }) {
  const { t } = useLocale();
  const statement = (fact.statement || fact.content || '').trim();
  const title = (fact.title || '').trim();
  const display = title && title !== statement ? `${title}：${statement}` : statement || title || t('facts.noContent');

  return (
    <div
      className={cn(
        'group flex items-start gap-0.5 px-0.5 py-1.5 rounded-[4px] transition-none',
        'hover:bg-[var(--vscode-list-hover)]',
        onSelect ? 'cursor-pointer' : '',
      )}
      onClick={onSelect || undefined}
    >
      <div className="w-5 shrink-0 flex flex-col items-start">
        <span className="text-[9px] font-mono text-[var(--vscode-fg-subtle)] tabular-nums leading-none">#{index}</span>
        <button
          type="button"
          className="mt-0.5 p-0.5 rounded-[2px] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-none"
          title={expanded ? t('common.collapse') : t('common.expand')}
          aria-label={expanded ? t('common.collapse') : t('common.expand')}
          onClick={(e) => {
            e.stopPropagation();
            onToggleExpand?.();
          }}
        >
          <ChevronDown size={12} className={cn(expanded ? 'rotate-180' : '')} />
        </button>
      </div>

      <div className="flex-1 min-w-0">
        <div className={cn('text-[10.5px] leading-snug', 'text-[var(--vscode-fg)]', expanded ? '' : 'line-clamp-3')}>
          {display}
        </div>
      </div>

      <div className="shrink-0 flex items-center gap-0.5 justify-end">
        <button
          className="w-5 h-5 inline-flex items-center justify-center rounded-[2px] hover:bg-[var(--vscode-list-hover)] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] opacity-50 hover:opacity-100"
          onClick={(e) => {
            e.stopPropagation();
            onEdit?.();
          }}
          title={t('common.edit')}
        >
          <Pencil size={11} strokeWidth={1.7} />
        </button>
        <button
          className="w-5 h-5 inline-flex items-center justify-center rounded-[2px] hover:bg-red-50 text-red-500 opacity-50 hover:opacity-100"
          onClick={(e) => {
            e.stopPropagation();
            onDelete?.();
          }}
          title={t('common.delete')}
        >
          <Trash2 size={11} strokeWidth={1.7} />
        </button>
      </div>
    </div>
  );
}

function EditFactDialog({ open, fact, onChange, onClose, onSave }) {
  const { t } = useLocale();
  if (!open || !fact) return null;

  return (
    <div className="anti-theme fixed inset-0 z-50 flex items-center justify-center bg-black/20 px-4">
      <div className="w-full max-w-md border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-bg)] text-[var(--vscode-fg)] rounded-[6px] shadow-none overflow-hidden">
        <div className="px-4 py-3 border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)] flex items-center justify-between">
          <div className="text-sm font-bold text-[var(--vscode-fg)]">{t('facts.editTitle')}</div>
          <button
            className="p-2 rounded-[6px] hover:bg-[var(--vscode-list-hover)] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] transition-none"
            onClick={onClose}
            title={t('common.close')}
          >
            <X size={16} />
          </button>
        </div>

        <div className="p-4 space-y-3">
          <Input
            placeholder={t('facts.titlePlaceholder')}
            value={fact.title || ''}
            onChange={(e) => onChange({ ...fact, title: e.target.value })}
            className="h-10 text-sm bg-[var(--vscode-input-bg)] border-[var(--vscode-input-border)] text-[var(--vscode-fg)] focus-visible:border-[var(--vscode-focus-border)] focus-visible:ring-[var(--vscode-focus-border)]"
          />

          <textarea
            placeholder={t('facts.contentPlaceholder')}
            value={fact.content || fact.statement || ''}
            onChange={(e) => onChange({ ...fact, content: e.target.value })}
            className="w-full min-h-[140px] text-sm bg-[var(--vscode-input-bg)] border border-[var(--vscode-input-border)] rounded-[6px] px-3 py-2 text-[var(--vscode-fg)] focus:outline-none focus:ring-2 focus:ring-[var(--vscode-focus-border)] focus:border-[var(--vscode-focus-border)] resize-none"
          />
        </div>

        <div className="px-4 py-3 border-t border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)] flex justify-end gap-2">
          <Button
            variant="ghost"
            onClick={onClose}
            className="h-8 px-3 text-xs rounded-[4px] border border-[var(--vscode-input-border)] text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] shadow-none"
          >
            {t('common.cancel')}
          </Button>
          <Button
            onClick={onSave}
            className="h-8 px-3 text-xs rounded-[4px] bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] hover:opacity-90 shadow-none"
          >
            {t('common.save')}
          </Button>
        </div>
      </div>
    </div>
  );
}

function CreateFactDialog({ open, fact, onChange, onClose, onSave }) {
  const { t } = useLocale();
  if (!open || !fact) return null;

  return (
    <div className="anti-theme fixed inset-0 z-50 flex items-center justify-center bg-black/20 px-4">
      <div className="w-full max-w-md border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-bg)] text-[var(--vscode-fg)] rounded-[6px] shadow-none overflow-hidden">
        <div className="px-4 py-3 border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)] flex items-center justify-between">
          <div className="text-sm font-bold text-[var(--vscode-fg)]">{t('facts.addTitle')}</div>
          <button
            className="p-2 rounded-[6px] hover:bg-[var(--vscode-list-hover)] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] transition-none"
            onClick={onClose}
            title={t('common.close')}
          >
            <X size={16} />
          </button>
        </div>

        <div className="p-4 space-y-3">
          <div className="text-[11px] text-[var(--vscode-fg-subtle)]">
            {t('facts.chapterLabel')}：<span className="font-mono">{fact.chapterId}</span>
          </div>

          <Input
            placeholder={t('facts.titlePlaceholder')}
            value={fact.title || ''}
            onChange={(e) => onChange({ ...fact, title: e.target.value })}
            className="h-10 text-sm bg-[var(--vscode-input-bg)] border-[var(--vscode-input-border)] text-[var(--vscode-fg)] focus-visible:border-[var(--vscode-focus-border)] focus-visible:ring-[var(--vscode-focus-border)]"
          />

          <textarea
            placeholder={t('facts.contentRequiredPlaceholder')}
            value={fact.content || ''}
            onChange={(e) => onChange({ ...fact, content: e.target.value })}
            className="w-full min-h-[140px] text-sm bg-[var(--vscode-input-bg)] border border-[var(--vscode-input-border)] rounded-[6px] px-3 py-2 text-[var(--vscode-fg)] focus:outline-none focus:ring-2 focus:ring-[var(--vscode-focus-border)] focus:border-[var(--vscode-focus-border)] resize-none"
          />
        </div>

        <div className="px-4 py-3 border-t border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)] flex justify-end gap-2">
          <Button
            variant="ghost"
            onClick={onClose}
            className="h-8 px-3 text-xs rounded-[4px] border border-[var(--vscode-input-border)] text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] shadow-none"
          >
            {t('common.cancel')}
          </Button>
          <Button
            onClick={onSave}
            className="h-8 px-3 text-xs rounded-[4px] bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] hover:opacity-90 shadow-none"
          >
            {t('facts.addBtn')}
          </Button>
        </div>
      </div>
    </div>
  );
}

function EditSummaryDialog({ open, summary, onChange, onClose, onSave }) {
  const { t } = useLocale();
  if (!open || !summary) return null;

  return (
    <div className="anti-theme fixed inset-0 z-50 flex items-center justify-center bg-black/20 px-4">
      <div className="w-full max-w-md border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-bg)] text-[var(--vscode-fg)] rounded-[6px] shadow-none overflow-hidden">
        <div className="px-4 py-3 border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)] flex items-center justify-between">
          <div className="text-sm font-bold text-[var(--vscode-fg)]">{t('facts.editSummaryTitle')}</div>
          <button
            className="p-2 rounded-[6px] hover:bg-[var(--vscode-list-hover)] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] transition-none"
            onClick={onClose}
            title={t('common.close')}
          >
            <X size={16} />
          </button>
        </div>

        <div className="p-4 space-y-3">
          <textarea
            placeholder={t('facts.summaryPlaceholder')}
            value={summary.text || ''}
            onChange={(e) => onChange({ ...summary, text: e.target.value })}
            className="w-full min-h-[180px] text-sm bg-[var(--vscode-input-bg)] border border-[var(--vscode-input-border)] rounded-[6px] px-3 py-2 text-[var(--vscode-fg)] focus:outline-none focus:ring-2 focus:ring-[var(--vscode-focus-border)] focus:border-[var(--vscode-focus-border)] resize-none"
          />
        </div>

        <div className="px-4 py-3 border-t border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)] flex justify-end gap-2">
          <Button
            variant="ghost"
            onClick={onClose}
            className="h-8 px-3 text-xs rounded-[4px] border border-[var(--vscode-input-border)] text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] shadow-none"
          >
            {t('common.cancel')}
          </Button>
          <Button
            onClick={onSave}
            className="h-8 px-3 text-xs rounded-[4px] bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] hover:opacity-90 shadow-none"
          >
            {t('common.save')}
          </Button>
        </div>
      </div>
    </div>
  );
}

function RefreshSummaryDialog({ open, volumes, projectId, onClose, onRefreshed }) {
  const { t } = useLocale();
  const [selected, setSelected] = useState(new Set());
  const [loading, setLoading] = useState(false);

  if (!open) return null;

  const toggleVolume = (volumeId) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(volumeId)) next.delete(volumeId);
      else next.add(volumeId);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === volumes.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(volumes.map((v) => v.id)));
    }
  };

  const handleRefresh = async () => {
    if (selected.size === 0) return;
    setLoading(true);
    try {
      await volumesAPI.refreshSummaries(projectId, Array.from(selected));
      onRefreshed?.();
      onClose();
    } catch (error) {
      logger.error(error);
      alert(t('facts.refreshFailed') + ': ' + extractErrorDetail(error));
    } finally {
      setLoading(false);
    }
  };

  const handleClose = () => {
    if (loading) return;
    onClose();
  };

  return (
    <div className="anti-theme fixed inset-0 z-50 flex items-center justify-center bg-black/20 px-4">
      <div className="w-full max-w-md border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-bg)] text-[var(--vscode-fg)] rounded-[6px] shadow-none overflow-hidden">
        <div className="px-4 py-3 border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)] flex items-center justify-between">
          <div className="text-sm font-bold text-[var(--vscode-fg)]">{t('facts.refreshSummaryTitle')}</div>
          <button
            className="p-2 rounded-[6px] hover:bg-[var(--vscode-list-hover)] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] transition-none"
            onClick={handleClose}
            disabled={loading}
            title={t('common.close')}
          >
            <X size={16} />
          </button>
        </div>

        <div className="p-4 space-y-3">
          <div className="text-[11px] text-[var(--vscode-fg-subtle)]">{t('facts.refreshSummaryDesc')}</div>

          <div className="space-y-1 max-h-[240px] overflow-y-auto custom-scrollbar">
            <button
              type="button"
              onClick={toggleAll}
              className={cn(
                'w-full flex items-center gap-2 px-3 py-2 rounded-[4px] text-left transition-none',
                selected.size === volumes.length
                  ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
                  : 'hover:bg-[var(--vscode-list-hover)]',
              )}
            >
              <span className="text-xs font-medium">{t('common.selectAll')}</span>
              <span className="ml-auto text-[10px] text-[var(--vscode-fg-subtle)] tabular-nums">{volumes.length}</span>
            </button>

            {volumes.map((volume) => (
              <button
                key={volume.id}
                type="button"
                onClick={() => toggleVolume(volume.id)}
                className={cn(
                  'w-full flex items-center gap-2 px-3 py-2 rounded-[4px] text-left transition-none',
                  selected.has(volume.id)
                    ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
                    : 'hover:bg-[var(--vscode-list-hover)]',
                )}
              >
                <span className="text-[10px] font-mono text-[var(--vscode-fg-subtle)]">{volume.id}</span>
                <span className="text-xs text-[var(--vscode-fg)] truncate">{volume.title}</span>
                <span className="ml-auto text-[10px] text-[var(--vscode-fg-subtle)] tabular-nums">
                  {(volume.chapters || []).length} {t('facts.chapterUnit')}
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="px-4 py-3 border-t border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)] flex justify-end gap-2">
          <Button
            variant="ghost"
            onClick={handleClose}
            disabled={loading}
            className="h-8 px-3 text-xs rounded-[4px] border border-[var(--vscode-input-border)] text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] shadow-none"
          >
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleRefresh}
            disabled={selected.size === 0 || loading}
            className="h-8 px-3 text-xs rounded-[4px] bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] hover:opacity-90 shadow-none disabled:opacity-50"
          >
            {loading ? (
              <span className="flex items-center gap-1.5">
                <Loader2 size={12} className="animate-spin" />
                {t('facts.refreshing')}
              </span>
            ) : (
              t('facts.refreshBtn')
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default FactsEncyclopedia;

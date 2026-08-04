/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   资源管理器面板 - 项目结构浏览、章节管理、批量同步与结果校对
 *   Explorer panel for project structure, chapter management, batch sync, and analysis review.
 */

/**
 * 资源管理器面板 - 项目结构浏览与批量操作入口
 *
 * Main IDE explorer panel for browsing project structure, managing chapters and volumes,
 * syncing chapter analysis, and reviewing extracted facts and summaries before persisting.
 *
 * @component
 * @example
 * return (
 *   <ExplorerPanel className="custom-class" />
 * )
 *
 * @param {Object} props - Component props
 * @param {string} [props.className] - 自定义样式类名 / Additional CSS classes
 * @returns {JSX.Element} 资源管理器面板 / Explorer panel element
 */
import { useState } from 'react';
import { useIDE } from '../../../context/IDEContext';
import { bindingsAPI, evidenceAPI, sessionAPI, textChunksAPI } from '../../../api';
import AnalysisSyncDialog from '../AnalysisSyncDialog';
import AnalysisReviewDialog from '../../writing/AnalysisReviewDialog';
import VolumeManageDialog from '../VolumeManageDialog';
import VolumeTree from '../VolumeTree';
import { Layers, RefreshCw, Plus, ArrowUpDown, FileText } from 'lucide-react';
import { cn } from '../../ui/core';
import { SidebarPanelHeader } from '../SidebarPanelHeader';
import logger from '../../../utils/logger';
import { useLocale } from '../../../i18n';

export default function ExplorerPanel({ className }) {
  const { t, locale } = useLocale();
  const requestLanguage = String(locale || '')
    .toLowerCase()
    .startsWith('en')
    ? 'en'
    : 'zh';
  const { state, dispatch } = useIDE();
  const [syncOpen, setSyncOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewItems, setReviewItems] = useState([]);
  const [reviewSaving, setReviewSaving] = useState(false);
  const [reviewError, setReviewError] = useState('');
  const [syncLoading, setSyncLoading] = useState(false);
  const [syncResults, setSyncResults] = useState([]);
  const [syncError, setSyncError] = useState('');
  const [indexRebuildLoading, setIndexRebuildLoading] = useState(false);
  const [indexRebuildError, setIndexRebuildError] = useState('');
  const [indexRebuildSuccess, setIndexRebuildSuccess] = useState(false);
  const [volumeManageOpen, setVolumeManageOpen] = useState(false);
  const [reorderMode, setReorderMode] = useState(false);

  const handleSyncConfirm = async (selectedChapters) => {
    if (selectedChapters.length === 0 || !state.activeProjectId) return;
    setSyncError('');
    setSyncResults([]);
    setSyncLoading(true);
    try {
      const res = await sessionAPI.analyzeSync(state.activeProjectId, {
        language: requestLanguage,
        chapters: selectedChapters,
      });
      const payload = Array.isArray(res.data) ? { success: true, results: res.data } : res.data || {};
      if (!payload?.success) {
        throw new Error(payload?.error || payload?.detail || t('writingSession.syncFailed'));
      }
      const results = Array.isArray(payload?.results) ? payload.results : [];
      const analyses = results
        .filter((item) => item?.success && item?.analysis && item?.chapter)
        .map((item) => ({ chapter: item.chapter, analysis: item.analysis }));
      const bindingResults = await Promise.all(
        results.map(async (item) => {
          const chapter = item?.chapter;
          if (!chapter) return null;
          try {
            const bindingResp = await bindingsAPI.get(state.activeProjectId, chapter);
            return { ...item, binding: bindingResp.data?.binding || null };
          } catch (error) {
            return {
              ...item,
              binding_error: error?.response?.data?.detail || error?.message || t('error.loadFailed'),
            };
          }
        }),
      );
      setSyncResults(bindingResults.filter(Boolean));
      setReviewItems(analyses);
      if (analyses.length > 0) {
        setSyncOpen(false);
        setReviewError('');
        setReviewOpen(true);
      }
    } catch (err) {
      logger.error(err);
      const detail = err?.response?.data?.detail || err?.response?.data?.error;
      setSyncError(detail || err?.message || t('writingSession.syncFailed'));
    } finally {
      setSyncLoading(false);
    }
  };

  const handleRebuildBindings = async (selectedChapters) => {
    if (!state.activeProjectId) return;
    setSyncError('');
    setSyncResults([]);
    setSyncLoading(true);
    try {
      const res = await bindingsAPI.rebuildBatch(state.activeProjectId, {
        chapters: selectedChapters.length > 0 ? selectedChapters : undefined,
      });
      if (!res.data?.success) {
        throw new Error(res.data?.error || t('error.loadFailed'));
      }
      const results = Array.isArray(res.data?.results) ? res.data.results : [];
      setSyncResults(results);
    } catch (err) {
      logger.error(err);
      setSyncError(err?.message || t('error.loadFailed'));
    } finally {
      setSyncLoading(false);
    }
  };

  const handleRebuildIndexes = async () => {
    if (!state.activeProjectId) return;
    setIndexRebuildError('');
    setIndexRebuildSuccess(false);
    setIndexRebuildLoading(true);
    try {
      await evidenceAPI.rebuild(state.activeProjectId);
      await textChunksAPI.rebuild(state.activeProjectId);
      setIndexRebuildSuccess(true);
    } catch (err) {
      logger.error(err);
      const detail = err?.response?.data?.detail || err?.response?.data?.error;
      setIndexRebuildError(detail || err?.message || t('error.loadFailed'));
    } finally {
      setIndexRebuildLoading(false);
    }
  };

  const handleReviewSave = async (updatedAnalyses) => {
    setReviewSaving(true);
    setReviewError('');
    try {
      const resp = await sessionAPI.saveAnalysisBatch(state.activeProjectId, {
        language: requestLanguage,
        items: updatedAnalyses,
        overwrite: true,
      });
      if (resp?.data?.success === false) {
        throw new Error(resp?.data?.error || resp?.data?.detail || t('error.saveFailed'));
      }
      setReviewOpen(false);
      setReviewItems([]);
    } catch (err) {
      logger.error(err);
      const detail = err?.response?.data?.detail || err?.response?.data?.error;
      const code = err?.code || err?.name;
      if (code === 'ECONNABORTED') {
        setReviewError(t('panels.explorer.saveTimeout'));
      } else {
        setReviewError(detail || err?.message || t('error.saveFailed'));
      }
    } finally {
      setReviewSaving(false);
    }
  };

  const handleChapterSelect = (chapter) => {
    dispatch({ type: 'SET_ACTIVE_DOCUMENT', payload: { ...chapter, type: 'chapter' } });
  };

  // 通用操作按钮组件
  const ActionButton = ({ onClick, icon: Icon, title }) => (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      className={cn(
        'p-1 rounded-[6px] text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-all duration-150 active:scale-90 outline-none focus:ring-1 focus:ring-[var(--vscode-focus-border)]',
        'opacity-70 hover:opacity-100 focus:opacity-100',
        'flex items-center justify-center w-6 h-6',
      )}
    >
      <Icon size={14} strokeWidth={1.5} />
    </button>
  );

  return (
    <div
      className={cn(
        'anti-theme explorer-panel flex flex-col h-full bg-[var(--vscode-bg)] text-[var(--vscode-fg)] select-none',
        className,
      )}
    >
      {/* VS Code 风格工具栏 */}
      <SidebarPanelHeader
        title={t('panels.explorer.title')}
        actions={
          <>
          <ActionButton
            onClick={() => setReorderMode((prev) => !prev)}
            icon={ArrowUpDown}
            title={reorderMode ? t('panels.explorer.exitReorder') : t('panels.explorer.reorderMode')}
          />
          <ActionButton
            onClick={() =>
              dispatch({ type: 'OPEN_CREATE_CHAPTER_DIALOG', payload: { volumeId: state.selectedVolumeId } })
            }
            icon={Plus}
            title={t('panels.explorer.newChapter')}
          />
          <ActionButton
            onClick={() => {
              setSyncError('');
              setSyncResults([]);
              setIndexRebuildError('');
              setIndexRebuildSuccess(false);
              setSyncOpen(true);
            }}
            icon={RefreshCw}
            title={t('panels.explorer.syncAll')}
          />
          <ActionButton
            onClick={() => setVolumeManageOpen(true)}
            icon={Layers}
            title={t('panels.explorer.manageVolumes')}
          />
          </>
        }
      />

      <div className="flex-1 overflow-hidden relative">
        <div className="absolute inset-0 overflow-y-auto custom-scrollbar px-1.5 py-1">
          {/* 大纲：始终置于所有分卷之前的第一项（全文规划资产，非章节） */}
          <button
            type="button"
            onClick={() =>
              dispatch({ type: 'SET_ACTIVE_DOCUMENT', payload: { type: 'outline', id: 'outline' } })
            }
            className={[
              'mb-1 flex w-full items-center gap-2 rounded-[6px] px-2 py-1.5 text-left text-[13px] transition-colors',
              state.activeDocument?.type === 'outline'
                ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
                : 'text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)]',
            ].join(' ')}
            title={t('outline.explorerHint') || '全文规划大纲'}
          >
            <FileText size={14} className="shrink-0 text-[var(--vscode-fg-subtle)]" />
            <span className="font-medium">{t('outline.title') || '大纲'}</span>
          </button>
          <VolumeTree
            projectId={state.activeProjectId}
            onChapterSelect={handleChapterSelect}
            selectedChapter={state.activeDocument?.id}
            reorderMode={reorderMode}
          />
        </div>
      </div>

      <AnalysisSyncDialog
        open={syncOpen}
        projectId={state.activeProjectId}
        loading={syncLoading}
        results={syncResults}
        error={syncError}
        indexRebuildLoading={indexRebuildLoading}
        indexRebuildError={indexRebuildError}
        indexRebuildSuccess={indexRebuildSuccess}
        onClose={() => setSyncOpen(false)}
        onConfirm={handleSyncConfirm}
        onRebuild={handleRebuildBindings}
        onRebuildIndexes={handleRebuildIndexes}
      />

      <AnalysisReviewDialog
        open={reviewOpen}
        analyses={reviewItems}
        error={reviewError}
        onCancel={() => {
          setReviewOpen(false);
          setReviewItems([]);
          setReviewError('');
        }}
        onSave={handleReviewSave}
        saving={reviewSaving}
      />

      <VolumeManageDialog
        open={volumeManageOpen}
        projectId={state.activeProjectId}
        onClose={() => setVolumeManageOpen(false)}
      />
    </div>
  );
}

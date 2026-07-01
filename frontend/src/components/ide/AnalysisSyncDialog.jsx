/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   批量同步分析对话框 - 选择要分析的章节后批量触发后端分析流程
 *   Analysis sync dialog for selecting chapters and triggering batch analysis.
 */

import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check } from 'lucide-react';
import { Button } from '../ui/core';
import { draftsAPI, volumesAPI } from '../../api';
import { cn } from '../ui/core';
import { useLocale } from '../../i18n';

/**
 * 批量同步分析对话框 - 用户选择要分析的章节并批量触发分析
 *
 * Modal dialog for selecting chapters to analyze and triggering batch analysis.
 * Only optimizes visual consistency without altering data and behavior logic.
 *
 * @component
 * @example
 * return (
 *   <AnalysisSyncDialog
 *     open={true}
 *     projectId="proj-001"
 *     onConfirm={handleConfirm}
 *     onCancel={handleCancel}
 *     loading={false}
 *   />
 * )
 *
 * @param {Object} props - Component props
 * @param {boolean} [props.open=false] - 对话框是否打开 / Whether dialog is open
 * @param {string} [props.projectId] - 项目ID / Project identifier
 * @param {Function} [props.onConfirm] - 确认回调，返回选中章节数组 / Confirm callback with selected chapters
 * @param {Function} [props.onCancel] - 取消回调 / Cancel callback
 * @param {boolean} [props.loading=false] - 是否加载中 / Whether loading
 * @returns {JSX.Element} 批量同步分析对话框 / Analysis sync dialog element
 */

// 辅助函数保持原逻辑 / Helper functions maintain original logic
const getChapterWeight = (chapterId) => {
  const match = chapterId.match(/^(?:V(\d+))?C(\d+)(?:([EI])(\d+))?$/i);
  if (!match) return 0;
  const volume = parseInt(match[1] || '0', 10);
  const chapter = parseInt(match[2] || '0', 10);
  const type = match[3];
  const seq = parseInt(match[4] || '0', 10);
  let weight = volume * 1000 + chapter;
  if (type && seq > 0) weight += 0.1 * seq;
  return weight;
};

const getVolumeId = (chapterId, summary) => {
  if (summary?.volume_id) return summary.volume_id;
  const match = chapterId.match(/^V(\d+)/i);
  return match ? `V${match[1]}` : 'V1';
};
export default function AnalysisSyncDialog({
  open,
  projectId,
  onClose,
  onConfirm,
  onRebuild,
  onRebuildIndexes,
  loading,
  results = [],
  error = '',
  indexRebuildLoading = false,
  indexRebuildError = '',
  indexRebuildSuccess = false,
}) {
  const { t } = useLocale();
  const [chapters, setChapters] = useState([]);
  const [summaries, setSummaries] = useState({});
  const [volumes, setVolumes] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [fetching, setFetching] = useState(false);

  // 数据获取逻辑保持不变
  useEffect(() => {
    if (!open || !projectId) return;
    setSelected(new Set());
    setFetching(true);
    Promise.all([draftsAPI.listChapters(projectId), draftsAPI.listSummaries(projectId), volumesAPI.list(projectId)])
      .then(([chaptersResp, summariesResp, volumesResp]) => {
        const chapterList = Array.isArray(chaptersResp.data) ? chaptersResp.data : [];
        const summaryList = Array.isArray(summariesResp.data) ? summariesResp.data : [];
        const volumeList = Array.isArray(volumesResp.data) ? volumesResp.data : [];
        const summaryMap = {};
        summaryList.forEach((item) => {
          if (item?.chapter) summaryMap[item.chapter] = item;
        });
        setChapters(chapterList.sort((a, b) => getChapterWeight(a) - getChapterWeight(b)));
        setSummaries(summaryMap);
        setVolumes(volumeList);
      })
      .catch(() => {
        setChapters([]);
        setSummaries({});
        setVolumes([]);
      })
      .finally(() => setFetching(false));
  }, [open, projectId]);

  const grouped = useMemo(() => {
    // 布局分组逻辑保持不变
    const groups = {};
    chapters.forEach((chapterId) => {
      const summary = summaries[chapterId];
      const volumeId = getVolumeId(chapterId, summary);
      if (!groups[volumeId]) groups[volumeId] = [];
      groups[volumeId].push({
        id: chapterId,
        title: summary?.title || '',
      });
    });
    const volumeOrder = new Map(volumes.map((v, idx) => [v.id, v.order ?? idx]));
    const volumeIds = Object.keys(groups).sort((a, b) => {
      const orderA = volumeOrder.has(a) ? volumeOrder.get(a) : 999;
      const orderB = volumeOrder.has(b) ? volumeOrder.get(b) : 999;
      if (orderA !== orderB) return orderA - orderB;
      return a.localeCompare(b);
    });
    return volumeIds.map((id) => ({
      id,
      title: volumes.find((v) => v.id === id)?.title || id,
      chapters: groups[id] || [],
    }));
  }, [chapters, summaries, volumes]);

  const toggleChapter = (chapterId) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(chapterId)) next.delete(chapterId);
      else next.add(chapterId);
      return next;
    });
  };

  const selectAll = () => setSelected(new Set(chapters));
  const clearAll = () => setSelected(new Set());
  const handleConfirm = () => {
    if (onConfirm) onConfirm(Array.from(selected));
  };
  const handleRebuild = () => {
    if (onRebuild) onRebuild(Array.from(selected));
  };
  const handleRebuildIndexes = () => {
    if (onRebuildIndexes) onRebuildIndexes();
  };
  const formatCharacters = (binding) => {
    const list = binding?.characters;
    if (!Array.isArray(list) || list.length === 0) return t('common.unknown');
    return list.join('、');
  };

  if (!open) return null;

  return createPortal(
    <>
      {/* 背景遮罩（轻微遮挡） */}
      <div className="fixed inset-0 bg-black/10 z-[100]" onClick={onClose} />

      {/* 命令面板布局 */}
      <div className="vscode-command-palette anti-theme z-[101]">
        {/* 头部区域 */}
        <div className="bg-[var(--vscode-sidebar-bg)] p-2 border-b border-[var(--vscode-input-border)] flex items-center gap-2">
          <div className="p-1 px-2 text-[11px] font-bold bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] rounded-[2px]">
            {t('chapter.analyze')}
          </div>
          <span className="text-xs font-medium text-[var(--vscode-fg)]">{t('writingSession.analysisReview')}</span>
          <div className="flex-1" />
          <span className="text-[10px] text-[var(--vscode-fg-subtle)] mr-2">
            {t('fanfiction.selectedPages').replace('{count}', selected.size)}
          </span>
        </div>

        {/* 快捷操作栏 */}
        <div className="bg-[var(--vscode-bg)] px-2 py-1 border-b border-[var(--vscode-input-border)] flex gap-2">
          <button
            onClick={selectAll}
            className="text-[11px] px-2 py-0.5 hover:bg-[var(--vscode-list-hover)] rounded-[2px] transition-none"
          >
            {t('fanfiction.selectAll')}
          </button>
          <button
            onClick={clearAll}
            className="text-[11px] px-2 py-0.5 hover:bg-[var(--vscode-list-hover)] rounded-[2px] transition-none"
          >
            {t('fanfiction.deselectAll')}
          </button>
        </div>

        {/* 可滚动列表 */}
        <div className="max-h-[500px] overflow-y-auto overflow-x-hidden py-1">
          {fetching ? (
            <div className="px-4 py-8 text-center text-xs text-[var(--vscode-fg-subtle)]">{t('common.loading')}</div>
          ) : (
            grouped.map((volume) => (
              <div key={volume.id}>
                {/* 分卷标题 */}
                <div className="px-3 py-1 text-[11px] font-bold text-[var(--vscode-fg-subtle)] bg-[var(--vscode-sidebar-bg)] border-y border-[var(--vscode-sidebar-border)] mt-[-1px]">
                  {volume.title} ({volume.id})
                </div>

                {/* 章节网格 */}
                <div className="grid grid-cols-2">
                  {volume.chapters.map((chapter) => {
                    const checked = selected.has(chapter.id);
                    return (
                      <div
                        key={chapter.id}
                        className={cn(
                          'vscode-tree-item gap-2 border-r border-[var(--vscode-sidebar-border)] last:border-r-0 border-b',
                          checked && 'selected',
                        )}
                        onClick={() => toggleChapter(chapter.id)}
                      >
                        {/* 简易勾选样式 */}
                        <div
                          className={cn(
                            'w-3 h-3 border grid place-items-center',
                            checked ? 'border-white bg-transparent' : 'border-[var(--vscode-fg-subtle)]',
                          )}
                        >
                          {checked && <Check size={10} strokeWidth={4} />}
                        </div>
                        <span className="font-mono text-[11px] opacity-70 w-8">{chapter.id}</span>
                        <span className="truncate">{chapter.title || t('chapter.noTitle')}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </div>

        {(loading || error || results.length > 0) && (
          <div className="border-t border-[var(--vscode-input-border)] bg-[var(--vscode-bg)]">
            <div className="px-3 py-1 text-[11px] font-bold text-[var(--vscode-fg-subtle)] bg-[var(--vscode-sidebar-bg)] border-b border-[var(--vscode-sidebar-border)]">
              {t('writingSession.analysisReview')}
            </div>
            {loading && (
              <div className="px-3 py-2 text-xs text-[var(--vscode-fg-subtle)]">{t('common.processing')}</div>
            )}
            {error && (
              <div className="px-3 py-2 text-xs text-red-500">
                {t('writingSession.syncFailed')} {error}
              </div>
            )}
            {results.length > 0 && (
              <div className="max-h-[180px] overflow-y-auto">
                {results.map((item, idx) => {
                  const success = Boolean(item?.success);
                  const itemError = item?.error || item?.detail || '';
                  const bindingError = item?.binding_error || item?.bindings_error;
                  return (
                    <div
                      key={item?.chapter || idx}
                      className="px-3 py-1.5 text-xs border-b border-[var(--vscode-sidebar-border)] last:border-b-0"
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[11px] opacity-70">{item?.chapter || '-'}</span>
                        <span className={success ? 'text-emerald-400' : 'text-red-500'}>
                          {success ? t('common.success') : t('common.error')}
                        </span>
                      </div>
                      <div className="text-[11px] text-[var(--vscode-fg-subtle)] mt-0.5">
                        {t('fact.bindings')}: {formatCharacters(item?.binding)}
                      </div>
                      {!success && itemError ? (
                        <div className="text-[11px] text-red-500 mt-0.5">
                          {t('common.error')}: {String(itemError)}
                        </div>
                      ) : null}
                      {bindingError && (
                        <div className="text-[11px] text-red-500 mt-0.5">
                          {t('error.unknown')}: {bindingError}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
            {(indexRebuildLoading || indexRebuildError || indexRebuildSuccess) && (
              <div className="px-3 py-2 text-xs border-t border-[var(--vscode-sidebar-border)]">
                {indexRebuildLoading && <div className="text-[var(--vscode-fg-subtle)]">{t('common.processing')}</div>}
                {indexRebuildSuccess && !indexRebuildLoading && !indexRebuildError && (
                  <div className="text-emerald-400">{t('common.success')}</div>
                )}
                {indexRebuildError && (
                  <div className="text-red-500">
                    {t('error.unknown')}: {indexRebuildError}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* 底部操作区 */}
        <div className="p-2 bg-[var(--vscode-sidebar-bg)] border-t border-[var(--vscode-input-border)] flex justify-end gap-2">
          <Button
            variant="ghost"
            onClick={onClose}
            className="h-6 px-3 text-xs rounded-[2px] hover:bg-[var(--vscode-list-hover)] transition-none"
          >
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={loading || selected.size === 0}
            className="h-6 px-3 text-xs bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] hover:opacity-90 rounded-[2px] shadow-none transition-none"
          >
            {loading ? t('common.processing') : t('writingSession.syncAll')}
          </Button>
          <Button
            onClick={handleRebuild}
            disabled={loading || selected.size === 0}
            className="h-6 px-3 text-xs bg-[var(--vscode-bg)] text-[var(--vscode-fg)] border border-[var(--vscode-input-border)] hover:bg-[var(--vscode-list-hover)] rounded-[2px] shadow-none transition-none"
          >
            {loading ? t('common.processing') : t('analysisSyncDialog.rebuildBindingsBtn')}
          </Button>
          <Button
            onClick={handleRebuildIndexes}
            disabled={loading || indexRebuildLoading}
            className="h-6 px-3 text-xs bg-[var(--vscode-bg)] text-[var(--vscode-fg)] border border-[var(--vscode-input-border)] hover:bg-[var(--vscode-list-hover)] rounded-[2px] shadow-none transition-none"
            title={t('analysisSyncDialog.rebuildIndexTitle')}
          >
            {indexRebuildLoading ? t('common.processing') : t('analysisSyncDialog.rebuildIndexBtn')}
          </Button>
        </div>
      </div>
    </>,
    document.body,
  );
}

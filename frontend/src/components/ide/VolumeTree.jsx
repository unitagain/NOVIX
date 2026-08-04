/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   分卷树视图 - IDE 资源管理器中的分卷和章节树，支持树形导航和重排
 *   Volume tree view for IDE explorer showing volumes and chapters with reordering.
 */

/**
 * 分卷树组件 - IDE 资源管理器中的分卷和章节树形结构
 *
 * IDE explorer tree component displaying project volumes and chapters in hierarchical view.
 * Only handles list presentation and interaction triggering, preserving data structure
 * and business logic. Implements "Calm & Focus" design language with:
 * - High density: unified line height
 * - Low noise: subtle decorations
 * - Clear hierarchy: indentation and light indicators
 *
 * @component
 * @example
 * return (
 *   <VolumeTree
 *     projectId="proj-001"
 *     onChapterSelect={handleSelect}
 *     selectedChapter="ch001"
 *     reorderMode={false}
 *   />
 * )
 *
 * @param {Object} props - Component props
 * @param {string} [props.projectId] - 项目ID / Project identifier
 * @param {Function} [props.onChapterSelect] - 章节选择回调 / Chapter selection callback
 * @param {string} [props.selectedChapter] - 当前选中章节 / Currently selected chapter
 * @param {boolean} [props.reorderMode=false] - 是否处于重排模式 / Whether in reorder mode
 * @returns {JSX.Element} 分卷树元素 / Volume tree element
 */
import { useEffect, useMemo, useState } from 'react';
import useSWR, { mutate } from 'swr';
import { ChevronRight, ChevronDown, Trash2, ArrowUp, ArrowDown } from 'lucide-react';
import { draftsAPI, volumesAPI } from '../../api';
import { useIDE } from '../../context/IDEContext';
import { cn } from '../ui/core';
import { useLocale } from '../../i18n';

export default function VolumeTree({ projectId, onChapterSelect, selectedChapter, reorderMode = false }) {
  const { state, dispatch } = useIDE();
  const { t } = useLocale();

  // 数据获取
  const { data: volumesData } = useSWR(
    projectId ? `/volumes/${projectId}` : null,
    () => volumesAPI.list(projectId).then((res) => res.data),
    { refreshInterval: 5000 }, // 轮询更新
  );

  const { data: chaptersData } = useSWR(
    projectId ? `/drafts/${projectId}/chapters` : null,
    () => draftsAPI.listChapters(projectId).then((res) => res.data),
    { refreshInterval: 5000 },
  );

  const { data: summariesData } = useSWR(projectId ? `/drafts/${projectId}/summaries` : null, () =>
    draftsAPI.listSummaries(projectId).then((res) => res.data),
  );

  // 状态
  const [expandedVolumes, setExpandedVolumes] = useState(new Set());

  // 数据处理
  const volumeList = useMemo(() => {
    return Array.isArray(volumesData) ? volumesData : [];
  }, [volumesData]);

  const chaptersByVolume = useMemo(() => {
    if (!Array.isArray(chaptersData)) return {};

    // 将摘要映射为标题
    const summaryMap = {};
    if (Array.isArray(summariesData)) {
      summariesData.forEach((s) => {
        if (s.chapter) summaryMap[s.chapter] = s;
      });
    }

    const grouped = {};

    // 解析分卷 ID 的辅助函数
    const getVolumeId = (chapterId, summary) => {
      if (summary?.volume_id) return summary.volume_id;
      const match = chapterId.match(/^V(\d+)/i);
      return match ? `V${match[1]}` : 'V1';
    };

    chaptersData.forEach((chapterId) => {
      const summary = summaryMap[chapterId];
      const volId = getVolumeId(chapterId, summary);

      if (!grouped[volId]) grouped[volId] = [];

      grouped[volId].push({
        chapter: chapterId,
        title: summary?.title || '',
        word_count: summary?.word_count || 0,
        order_index: typeof summary?.order_index === 'number' ? summary.order_index : null,
        // 需要时可增加排序权重
      });
    });

    // 分卷内章节排序：优先使用 order_index（用户自定义），否则回退到章节号排序。
    Object.keys(grouped).forEach((key) => {
      grouped[key].sort((a, b) => {
        const aOrder = typeof a.order_index === 'number' ? a.order_index : Number.POSITIVE_INFINITY;
        const bOrder = typeof b.order_index === 'number' ? b.order_index : Number.POSITIVE_INFINITY;
        if (aOrder !== bOrder) return aOrder - bOrder;
        return a.chapter.localeCompare(b.chapter, undefined, { numeric: true });
      });
    });

    return grouped;
  }, [chaptersData, summariesData]);

  // 副作用
  useEffect(() => {
    // 选中章节时自动展开所在分卷
    if (selectedChapter) {
      const match = selectedChapter.match(/^V(\d+)/i);
      const volId = match ? `V${match[1]}` : 'V1'; // 简化推断
      // 如需更准确可利用章节 -> 分卷映射
      // 当前确保选中章节所在分卷展开
      setExpandedVolumes((prev) => {
        const next = new Set(prev);
        next.add(volId);
        return next;
      });
    }
  }, [selectedChapter]);

  // 事件处理
  const toggleVolume = (volId) => {
    setExpandedVolumes((prev) => {
      const next = new Set(prev);
      if (next.has(volId)) next.delete(volId);
      else next.add(volId);
      return next;
    });
  };

  const handleDeleteChapter = async (chapterId, title) => {
    if (!projectId || !chapterId) return;
    const isActive = state.activeDocument?.type === 'chapter' && String(state.activeDocument?.id) === String(chapterId);
    const hasUnsaved = Boolean(state.unsavedChanges) && isActive;
    const displayTitle = title ? `（${title}）` : '';
    const mainMsg = t('chapter.deleteConfirmSimple').replace('{id}', chapterId).replace('{title}', displayTitle);
    const extraWarn = hasUnsaved ? '\n\n' + t('common.unsavedNote') : '';

    const ok = window.confirm(mainMsg + extraWarn);
    if (!ok) return;

    try {
      await draftsAPI.deleteChapter(projectId, chapterId);
      // 章节已不存在：同步关掉它的标签，否则会留下点开即报错的死标签。
      dispatch({ type: 'CLOSE_TAB', payload: `chapter:${chapterId}` });
      await mutate(`/drafts/${projectId}/chapters`);
      await mutate(`/drafts/${projectId}/summaries`);
    } catch (e) {
      window.alert(t('chapter.deleteFailed').replace('{message}', e?.message || t('common.unknown')));
    }
  };

  const handleReorderChapter = async (volumeId, chapterIndex, direction) => {
    if (!projectId) return;
    const list = chaptersByVolume?.[volumeId] || [];
    if (!Array.isArray(list) || list.length < 2) return;

    const delta = direction === 'up' ? -1 : 1;
    const nextIndex = chapterIndex + delta;
    if (nextIndex < 0 || nextIndex >= list.length) return;

    const reordered = list.slice();
    const tmp = reordered[chapterIndex];
    reordered[chapterIndex] = reordered[nextIndex];
    reordered[nextIndex] = tmp;

    try {
      await draftsAPI.reorderChapters(projectId, {
        volume_id: volumeId,
        chapter_order: reordered.map((item) => item.chapter),
      });
      await mutate(`/drafts/${projectId}/chapters`);
      await mutate(`/drafts/${projectId}/summaries`);
    } catch (e) {
      window.alert(
        t('chapter.reorderFailed').replace('{message}', e?.response?.data?.detail || e?.message || t('common.unknown')),
      );
    }
  };

  return (
    <div className="w-full text-[13px] font-sans text-[var(--vscode-fg)] select-none">
      {/* 分卷列表 */}
      {volumeList.map((volume) => {
        const isExpanded = expandedVolumes.has(volume.id);
        const isSelected = state.selectedVolumeId === volume.id; // 分卷选中逻辑（如有）
        const chapters = chaptersByVolume[volume.id] || [];

        return (
          <div key={volume.id}>
            {/* 分卷标题行 */}
            <div
              className={cn(
                'vscode-tree-item font-bold flex items-center gap-0.5',
                isSelected && 'selected', // 可选：分卷可选中
              )}
              onClick={(e) => {
                toggleVolume(volume.id);
                // 可选：派发分卷选择
                dispatch({ type: 'SET_SELECTED_VOLUME_ID', payload: volume.id });
              }}
            >
              <span className="flex items-center justify-center w-5 h-5 opacity-70">
                {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </span>
              <span className="truncate">{volume.title}</span>
            </div>

            {/* 章节列表（展开时渲染，不加动画） */}
            {isExpanded && (
              <div className="relative">
                {chapters.map((chapter, idx) => {
                  const isChapterSelected = selectedChapter === chapter.chapter;

                  return (
                    <div
                      key={chapter.chapter}
                      className={cn(
                        'vscode-tree-item pl-8 gap-2 relative group', // 缩进：20px 图标 + 12px，相对定位便于层级控制
                        isChapterSelected && 'selected',
                      )}
                      onClick={() => {
                        onChapterSelect({ id: chapter.chapter, title: chapter.title });
                      }}
                      title={chapter.title}
                      style={{ cursor: 'pointer' }}
                    >
                      <span className="truncate flex-1">
                        <span className="opacity-70 font-mono text-xs mr-2">{chapter.chapter}</span>
                        {chapter.title || t('chapter.noTitle')}
                      </span>

                      {/* 右侧元信息 */}
                      {chapter.word_count > 0 && (
                        <span className="text-[10px] opacity-40 font-mono pr-1">{chapter.word_count}</span>
                      )}

                      {reorderMode ? (
                        <>
                          <button
                            type="button"
                            title={t('common.moveUp')}
                            aria-label={t('common.moveUp')}
                            disabled={idx === 0}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleReorderChapter(volume.id, idx, 'up');
                            }}
                            className={cn(
                              'p-1 rounded-[6px] hover:bg-[var(--vscode-list-hover)] transition-all duration-150 active:scale-90 outline-none focus:ring-1 focus:ring-[var(--vscode-focus-border)]',
                              'text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)]',
                              'opacity-80 hover:opacity-100 disabled:opacity-20 disabled:pointer-events-none',
                            )}
                          >
                            <ArrowUp size={14} strokeWidth={1.5} />
                          </button>

                          <button
                            type="button"
                            title={t('common.moveDown')}
                            aria-label={t('common.moveDown')}
                            disabled={idx === chapters.length - 1}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleReorderChapter(volume.id, idx, 'down');
                            }}
                            className={cn(
                              'p-1 rounded-[6px] hover:bg-[var(--vscode-list-hover)] transition-all duration-150 active:scale-90 outline-none focus:ring-1 focus:ring-[var(--vscode-focus-border)]',
                              'text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)]',
                              'opacity-80 hover:opacity-100 disabled:opacity-20 disabled:pointer-events-none',
                            )}
                          >
                            <ArrowDown size={14} strokeWidth={1.5} />
                          </button>
                        </>
                      ) : null}

                      <button
                        type="button"
                        title={t('chapter.delete')}
                        aria-label={t('chapter.delete')}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteChapter(chapter.chapter, chapter.title);
                        }}
                        className={cn(
                          'p-1 rounded-[6px] hover:bg-[var(--vscode-list-hover)] transition-all duration-150 active:scale-90 outline-none focus:ring-1 focus:ring-[var(--vscode-focus-border)]',
                          'text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)]',
                          'opacity-0 group-hover:opacity-80 hover:opacity-100',
                        )}
                      >
                        <Trash2 size={14} strokeWidth={1.5} />
                      </button>
                    </div>
                  );
                })}

                {/* 分卷空状态 */}
                {chapters.length === 0 && (
                  <div className="vscode-tree-item pl-8 text-xs opacity-40 italic cursor-default hover:bg-transparent">
                    {t('chapter.noChapters')}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

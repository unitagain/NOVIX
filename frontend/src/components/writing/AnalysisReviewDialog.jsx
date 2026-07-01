/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 */

import { useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Button, Card, Input } from '../ui/core';
import { X, Trash2, Plus, CheckSquare } from 'lucide-react';
import { useLocale } from '../../i18n';

/**
 * 分析结果确认对话框 - 批量同步后用于校对章节摘要与事实
 *
 * Modal dialog for reviewing and confirming chapter summaries and extracted facts
 * before persisting them to the canon. Allows inline editing of summaries and facts,
 * but does NOT manage new character card proposals (those are handled separately).
 *
 * @component
 * @example
 * return (
 *   <AnalysisReviewDialog
 *     open={true}
 *     analyses={[
 *       {
 *         chapter: 'Ch001',
 *         analysis: {
 *           summary: { brief_summary: '...' },
 *           facts: [{ statement: '...', confidence: 1.0 }]
 *         }
 *       }
 *     ]}
 *     onSave={handleSave}
 *     onCancel={handleCancel}
 *   />
 * )
 *
 * @param {Object} props - Component props
 * @param {boolean} [props.open=false] - 对话框是否打开 / Whether dialog is open
 * @param {Array} [props.analyses=[]] - 分析结果数组 / Array of analysis results
 * @param {string} [props.error=''] - 错误信息 / Error message to display
 * @param {Function} [props.onCancel] - 取消回调 / Callback when user cancels
 * @param {Function} [props.onSave] - 保存回调 / Callback when user confirms save
 * @param {boolean} [props.saving=false] - 是否正在保存中 / Whether save operation is in progress
 * @returns {JSX.Element} 分析结果确认对话框 / Analysis review dialog element
 */
const emptySummary = {
  chapter: '',
  volume_id: 'V1',
  title: '',
  word_count: 0,
  key_events: [],
  new_facts: [],
  character_state_changes: [],
  open_loops: [],
  brief_summary: '',
};

export default function AnalysisReviewDialog({ open, analyses = [], error = '', onCancel, onSave, saving = false }) {
  const { t } = useLocale();
  const [currentChapter, setCurrentChapter] = useState('');
  const [analysisMap, setAnalysisMap] = useState({});

  // ========================================================================
  // 初始化分析数据 / Initialize analysis data
  // ========================================================================
  useEffect(() => {
    if (!open) return;
    const map = {};
    analyses.forEach((item) => {
      if (!item?.chapter) return;
      map[item.chapter] = {
        summary: { ...emptySummary, ...(item.analysis?.summary || {}), chapter: item.chapter },
        facts: item.analysis?.facts ? [...item.analysis.facts] : [],
        timeline_events: item.analysis?.timeline_events || [],
        character_states: item.analysis?.character_states || [],
      };
    });
    setAnalysisMap(map);
    setCurrentChapter(analyses[0]?.chapter || '');
  }, [open, analyses]);

  const current = analysisMap[currentChapter] || {
    summary: { ...emptySummary, chapter: currentChapter },
    facts: [],
    timeline_events: [],
    character_states: [],
  };

  const chapterList = useMemo(() => {
    return analyses
      .map((item) => ({
        chapter: item.chapter,
        title: item.analysis?.summary?.title || '',
      }))
      .filter((item) => item.chapter);
  }, [analyses]);

  /**
   * 更新当前章节的数据 / Update current chapter data
   * @param {Object} patch - 要合并的更新内容 / Updates to merge
   */
  const updateCurrent = (patch) => {
    setAnalysisMap((prev) => ({
      ...prev,
      [currentChapter]: {
        ...prev[currentChapter],
        ...patch,
      },
    }));
  };

  /**
   * 更新指定索引的事实 / Update fact at specified index
   * @param {number} index - 事实索引 / Fact index
   * @param {string} value - 新的事实陈述 / New fact statement
   */
  const updateFact = (index, value) => {
    const next = current.facts.map((item, idx) => (idx === index ? { ...item, statement: value } : item));
    updateCurrent({ facts: next });
  };

  /**
   * 移除指定索引的事实 / Remove fact at specified index
   * @param {number} index - 要移除的事实索引 / Index of fact to remove
   */
  const removeFact = (index) => {
    const next = current.facts.filter((_, idx) => idx !== index);
    updateCurrent({ facts: next });
  };

  /**
   * 添加新事实 / Add a new fact entry
   */
  const addFact = () => {
    if (current.facts.length >= 5) return;
    updateCurrent({ facts: [...current.facts, { statement: '', confidence: 1.0 }] });
  };

  /**
   * 处理保存操作 / Handle save operation
   * 清理并验证所有事实后提交
   */
  const handleSave = () => {
    if (!onSave) return;
    const payload = Object.entries(analysisMap).map(([chapter, data]) => {
      const cleanedFacts = (data.facts || [])
        .map((fact) => ({ ...fact, statement: (fact.statement || '').trim() }))
        .filter((fact) => fact.statement)
        .slice(0, 5);

      return {
        chapter,
        analysis: {
          summary: {
            ...data.summary,
            chapter,
            brief_summary: (data.summary?.brief_summary || '').trim(),
          },
          facts: cleanedFacts,
          timeline_events: data.timeline_events || [],
          character_states: data.character_states || [],
        },
      };
    });
    onSave(payload);
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* 背景遮罩 / Modal backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/20 z-40"
          />
          {/*
            ======================================================================
            对话框主容器 / Modal container
            ======================================================================
            使用 Framer Motion 动画，带有淡入和缩放效果
            Uses Framer Motion animations with fade-in and scale effects
          */}
          <motion.div
            initial={{ opacity: 0, y: 8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
            className="fixed inset-0 z-50 flex items-center justify-center p-4 anti-theme"
          >
            <Card className="w-full max-w-6xl max-h-[85vh] p-0 flex flex-col overflow-hidden bg-[var(--vscode-bg)] text-[var(--vscode-fg)] border border-[var(--vscode-sidebar-border)] rounded-[6px] shadow-none">
              {/* 对话框头部 / Dialog header */}
              <div className="px-6 py-5 border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)] flex items-start justify-between gap-4">
                <div className="space-y-1">
                  <h2 className="text-xl font-bold text-[var(--vscode-fg)]">{t('analysisReview.title')}</h2>
                  <p className="text-sm text-[var(--vscode-fg-subtle)]">{t('analysisReview.subtitle')}</p>
                </div>
                <button
                  onClick={onCancel}
                  className="p-2 rounded-[6px] hover:bg-[var(--vscode-list-hover)] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] transition-none"
                  title={t('common.close')}
                >
                  <X size={16} />
                </button>
              </div>

              {/* 内容区域 / Content area */}
              <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[220px_1fr] gap-4 overflow-hidden p-6">
                {/* 章节列表侧栏 / Chapter list sidebar */}
                <div className="border border-[var(--vscode-sidebar-border)] rounded-[6px] bg-[var(--vscode-bg)] p-2 overflow-y-auto custom-scrollbar">
                  <div className="text-xs font-bold text-[var(--vscode-fg-subtle)] px-2 py-1">
                    {t('analysisReview.chapterSummary')}
                  </div>
                  <div className="space-y-1 mt-2">
                    {chapterList.map((item) => {
                      const active = item.chapter === currentChapter;
                      return (
                        <button
                          key={item.chapter}
                          onClick={() => setCurrentChapter(item.chapter)}
                          className={
                            `w-full flex items-center gap-2 px-2 py-1.5 rounded-[6px] text-xs transition-none ` +
                            (active
                              ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
                              : 'text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)]')
                          }
                        >
                          <CheckSquare
                            size={12}
                            className={
                              active ? 'text-[var(--vscode-list-active-fg)]' : 'text-[var(--vscode-fg-subtle)]'
                            }
                          />
                          <span className="font-mono text-[11px]">{item.chapter}</span>
                          <span className="truncate">{item.title || t('chapter.noTitle')}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* 编辑区域 / Edit area */}
                <div className="grid grid-cols-1 gap-4 overflow-hidden">
                  <div className="space-y-4 overflow-y-auto pr-2 min-h-0 custom-scrollbar">
                    {/* 章节摘要编辑 / Chapter summary editor */}
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <h3 className="text-sm font-bold text-[var(--vscode-fg)]">
                          {t('analysisReview.chapterSummary')}
                        </h3>
                        <span className="text-[10px] text-[var(--vscode-fg-subtle)]">{t('common.default')}</span>
                      </div>
                      <textarea
                        value={current.summary?.brief_summary || ''}
                        onChange={(e) =>
                          updateCurrent({ summary: { ...current.summary, brief_summary: e.target.value } })
                        }
                        placeholder={t('fact.summaryEmpty')}
                        className="w-full min-h-[140px] text-sm bg-[var(--vscode-input-bg)] border border-[var(--vscode-input-border)] rounded-[6px] px-3 py-2 text-[var(--vscode-fg)] focus:outline-none focus:ring-2 focus:ring-[var(--vscode-focus-border)] focus:border-[var(--vscode-focus-border)] resize-none"
                      />
                    </div>

                    {/* 事实编辑 / Facts editor */}
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <h3 className="text-sm font-bold text-[var(--vscode-fg)]">{t('fact.title')}</h3>
                          <span className="text-[10px] text-[var(--vscode-fg-subtle)]"></span>
                        </div>
                        <button
                          onClick={addFact}
                          className="inline-flex items-center gap-1 text-xs text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] px-2 py-1 rounded-[4px] disabled:opacity-40"
                          disabled={current.facts.length >= 5}
                        >
                          <Plus size={12} />
                          {t('fact.addFact')} ({current.facts.length}/5)
                        </button>
                      </div>
                      {current.facts.length === 0 ? (
                        <div className="text-xs text-[var(--vscode-fg-subtle)] border border-dashed border-[var(--vscode-sidebar-border)] rounded-[6px] px-3 py-2">
                          {t('fact.noFacts')}
                        </div>
                      ) : (
                        <div className="space-y-2">
                          {current.facts.map((fact, idx) => (
                            <div key={`${fact.id || 'fact'}-${idx}`} className="flex items-start gap-2">
                              <Input
                                value={fact.statement || ''}
                                onChange={(e) => updateFact(idx, e.target.value)}
                                placeholder={t('fact.contentPlaceholder')}
                                className="bg-[var(--vscode-input-bg)] border-[var(--vscode-input-border)] text-sm text-[var(--vscode-fg)] focus-visible:border-[var(--vscode-focus-border)] focus-visible:ring-[var(--vscode-focus-border)]"
                              />
                              <button
                                onClick={() => removeFact(idx)}
                                className="p-2 rounded-[6px] hover:bg-red-50 text-red-500"
                                title={t('common.delete')}
                              >
                                <Trash2 size={14} />
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              {/* 对话框底部操作 / Dialog footer actions */}
              <div className="flex justify-end gap-3 px-6 py-4 border-t border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)]">
                {error ? <div className="mr-auto text-xs text-red-500 flex items-center">{error}</div> : null}
                <Button
                  variant="ghost"
                  onClick={onCancel}
                  disabled={saving}
                  className="h-8 px-3 text-xs rounded-[4px] border border-[var(--vscode-input-border)] text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] shadow-none"
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  onClick={handleSave}
                  disabled={saving}
                  className="h-8 px-3 text-xs rounded-[4px] bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] hover:opacity-90 shadow-none"
                >
                  {saving ? t('common.processing') : t('analysisReview.confirm')}
                </Button>
              </div>
            </Card>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

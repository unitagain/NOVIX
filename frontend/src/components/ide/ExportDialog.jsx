import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Download, FileText, FileType2, FileCode2, X, Upload, Users, Globe } from 'lucide-react';
import { cardsAPI, draftsAPI, exportAPI } from '../../api';
import { Button } from '../ui/core';
import { useLocale } from '../../i18n';
import { extractErrorDetail } from '../../utils/extractError';

const FORMAT_OPTIONS = [
  { id: 'txt', icon: FileText },
  { id: 'md', icon: FileCode2 },
  { id: 'docx', icon: FileType2 },
];

function parseFilename(response, fallbackName) {
  const disposition = response?.headers?.['content-disposition'] || '';
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }
  const plainMatch = disposition.match(/filename="([^"]+)"/i);
  return plainMatch?.[1] || fallbackName;
}

function triggerBlobDownload(blob, filename) {
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(objectUrl);
}

function downloadJson(data, filename) {
  triggerBlobDownload(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }), filename);
}

export default function ExportDialog({ open, onClose, projectId, currentChapter }) {
  const { t } = useLocale();
  const [tab, setTab] = useState('manuscript');
  const [chapters, setChapters] = useState([]);
  const [selectedChapters, setSelectedChapters] = useState([]);
  const [format, setFormat] = useState('txt');
  const [includeTitles, setIncludeTitles] = useState(true);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);

  // Tavern 卡片互操作状态
  const [tavernCharacters, setTavernCharacters] = useState([]);
  const [tavernCharacter, setTavernCharacter] = useState('');
  const [tavernIncludeWorld, setTavernIncludeWorld] = useState(true);
  const [tavernBusy, setTavernBusy] = useState(false);
  const [tavernImportPlan, setTavernImportPlan] = useState(null);
  const [tavernImportFile, setTavernImportFile] = useState(null);
  const [tavernImportResult, setTavernImportResult] = useState(null);
  const [tavernOverwrite, setTavernOverwrite] = useState(false);
  const tavernFileInputRef = useRef(null);

  const chapterCount = chapters.length;
  const allSelected = chapterCount > 0 && selectedChapters.length === chapterCount;

  const fallbackFilename = useMemo(() => {
    if (selectedChapters.length === 1) {
      return `${selectedChapters[0]}.${format}`;
    }
    return `wenshape_export.${format}`;
  }, [format, selectedChapters]);

  useEffect(() => {
    if (!open || !projectId) return;
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const [chaptersRes, summariesRes] = await Promise.all([
          draftsAPI.listChapters(projectId),
          draftsAPI.listSummaries(projectId),
        ]);
        if (cancelled) return;
        const summaryMap = new Map(
          (summariesRes.data || []).map((item) => [String(item.chapter), String(item.title || '').trim()]),
        );
        const chapterItems = (chaptersRes.data || []).map((chapterId) => ({
          id: String(chapterId),
          title: summaryMap.get(String(chapterId)) || String(chapterId),
        }));
        setChapters(chapterItems);
        const preferred =
          currentChapter && chapterItems.some((item) => item.id === currentChapter)
            ? [String(currentChapter)]
            : chapterItems.slice(0, 1).map((item) => item.id);
        setSelectedChapters(preferred);
      } catch (error) {
        alert(t('exportDialog.loadFailed', { message: extractErrorDetail(error) }));
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [open, projectId, currentChapter, t]);

  useEffect(() => {
    if (!open) return;
    const handleEsc = (event) => {
      if (event.key === 'Escape') {
        onClose?.();
      }
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [open, onClose]);

  // 打开对话框时重置 Tavern 导入导出状态
  useEffect(() => {
    if (!open) return;
    setTab('manuscript');
    setTavernImportPlan(null);
    setTavernImportFile(null);
    setTavernImportResult(null);
    setTavernOverwrite(false);
  }, [open]);

  // Tavern 页：加载角色卡列表供导出选择
  useEffect(() => {
    if (!open || !projectId || tab !== 'tavern') return;
    let cancelled = false;
    const load = async () => {
      try {
        const res = await cardsAPI.listCharactersIndex(projectId);
        if (cancelled) return;
        const names = (res.data || []).map((card) => card.name).filter(Boolean);
        setTavernCharacters(names);
        setTavernCharacter((prev) => (prev && names.includes(prev) ? prev : names[0] || ''));
      } catch {
        if (!cancelled) setTavernCharacters([]);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [open, projectId, tab]);

  const handleTavernExportCharacter = async () => {
    if (!projectId || !tavernCharacter) return;
    setTavernBusy(true);
    try {
      const res = await cardsAPI.exportTavernCharacter(projectId, tavernCharacter, tavernIncludeWorld);
      downloadJson(res.data, `${tavernCharacter}.json`);
    } catch (error) {
      alert(t('exportDialog.tavernExportFailed', { message: extractErrorDetail(error) }));
    } finally {
      setTavernBusy(false);
    }
  };

  const handleTavernExportLorebook = async () => {
    if (!projectId) return;
    setTavernBusy(true);
    try {
      const res = await cardsAPI.exportTavernLorebook(projectId);
      downloadJson(res.data, `${projectId}_lorebook.json`);
    } catch (error) {
      alert(t('exportDialog.tavernExportFailed', { message: extractErrorDetail(error) }));
    } finally {
      setTavernBusy(false);
    }
  };

  // 第一步：上传文件做 dry-run，返回预案（含丢弃字段清单）供用户确认
  const handleTavernFileChosen = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file || !projectId) return;
    setTavernBusy(true);
    setTavernImportPlan(null);
    setTavernImportResult(null);
    try {
      const res = await cardsAPI.importTavern(projectId, file, { commit: false });
      setTavernImportFile(file);
      setTavernImportPlan(res.data?.plan || null);
    } catch (error) {
      alert(t('exportDialog.tavernImportFailed', { message: extractErrorDetail(error) }));
    } finally {
      setTavernBusy(false);
    }
  };

  // 第二步：用户确认预案后重放同一文件落盘（import_tavern_card = ask 的前端实现）
  const handleTavernConfirmImport = async () => {
    if (!projectId || !tavernImportFile) return;
    setTavernBusy(true);
    try {
      const res = await cardsAPI.importTavern(projectId, tavernImportFile, {
        commit: true,
        overwrite: tavernOverwrite,
      });
      setTavernImportResult(res.data);
      setTavernImportPlan(null);
    } catch (error) {
      alert(t('exportDialog.tavernImportFailed', { message: extractErrorDetail(error) }));
    } finally {
      setTavernBusy(false);
    }
  };

  const toggleChapter = (chapterId) => {
    setSelectedChapters((prev) =>
      prev.includes(chapterId) ? prev.filter((item) => item !== chapterId) : [...prev, chapterId],
    );
  };

  const handleToggleAll = () => {
    setSelectedChapters(allSelected ? [] : chapters.map((item) => item.id));
  };

  const handleExport = async () => {
    if (!projectId || selectedChapters.length === 0) return;
    setExporting(true);
    try {
      const response = await exportAPI.download(projectId, {
        chapter_ids: selectedChapters,
        format,
        include_chapter_titles: includeTitles,
      });
      const filename = parseFilename(response, fallbackFilename);
      triggerBlobDownload(response.data, filename);
      onClose?.();
    } catch (error) {
      alert(t('exportDialog.exportFailed', { message: extractErrorDetail(error) }));
    } finally {
      setExporting(false);
    }
  };

  if (!open) return null;

  return createPortal(
    <>
      <div className="fixed inset-0 z-[100] bg-black/35" onClick={onClose} />
      <div className="fixed inset-0 z-[101] flex items-center justify-center p-4">
        <div className="w-full max-w-2xl rounded-[10px] border border-[var(--vscode-input-border)] bg-[var(--vscode-sidebar-bg)] text-[var(--vscode-fg)] shadow-2xl overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--vscode-input-border)]">
            <div>
              <div className="text-sm font-semibold">{t('exportDialog.title')}</div>
              <div className="text-xs text-[var(--vscode-fg-subtle)] mt-1">{t('exportDialog.subtitle')}</div>
            </div>
            <button
              onClick={onClose}
              className="h-8 w-8 inline-flex items-center justify-center rounded-[6px] text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)]"
              aria-label={t('common.close')}
            >
              <X size={16} />
            </button>
          </div>

          <div className="px-4 pt-3">
            <div className="inline-flex rounded-[8px] border border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] p-0.5">
              {[
                { id: 'manuscript', label: t('exportDialog.tabManuscript') },
                { id: 'tavern', label: t('exportDialog.tabTavern') },
              ].map((item) => (
                <button
                  key={item.id}
                  onClick={() => setTab(item.id)}
                  className={[
                    'px-3 py-1.5 rounded-[6px] text-sm transition-colors',
                    tab === item.id
                      ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
                      : 'text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)]',
                  ].join(' ')}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          {tab === 'tavern' ? (
            <div className="p-4 space-y-5 max-h-[60vh] overflow-y-auto">
              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-[var(--vscode-fg-subtle)] mb-3">
                  {t('exportDialog.tavernExportTitle')}
                </div>
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <Users size={14} className="text-[var(--vscode-fg-subtle)] shrink-0" />
                    <select
                      value={tavernCharacter}
                      onChange={(e) => setTavernCharacter(e.target.value)}
                      disabled={tavernCharacters.length === 0}
                      className="flex-1 min-w-0 rounded-[6px] border border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] px-2 py-1.5 text-sm"
                    >
                      {tavernCharacters.length === 0 ? (
                        <option value="">{t('exportDialog.tavernNoCharacters')}</option>
                      ) : (
                        tavernCharacters.map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))
                      )}
                    </select>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={handleTavernExportCharacter}
                      disabled={tavernBusy || !tavernCharacter}
                      className="gap-1.5 shrink-0"
                    >
                      <Download size={13} />
                      {t('exportDialog.tavernExportCharacter')}
                    </Button>
                  </div>
                  <label className="flex items-center gap-2 text-xs text-[var(--vscode-fg-subtle)]">
                    <input
                      type="checkbox"
                      checked={tavernIncludeWorld}
                      onChange={(e) => setTavernIncludeWorld(e.target.checked)}
                    />
                    <span>{t('exportDialog.tavernIncludeWorld')}</span>
                  </label>
                  <div className="flex items-center gap-2">
                    <Globe size={14} className="text-[var(--vscode-fg-subtle)] shrink-0" />
                    <span className="flex-1 text-sm">{t('exportDialog.tavernLorebookLabel')}</span>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={handleTavernExportLorebook}
                      disabled={tavernBusy}
                      className="gap-1.5 shrink-0"
                    >
                      <Download size={13} />
                      {t('exportDialog.tavernExportLorebook')}
                    </Button>
                  </div>
                </div>
              </div>

              <div className="border-t border-[var(--vscode-input-border)] pt-4">
                <div className="text-xs font-semibold uppercase tracking-wide text-[var(--vscode-fg-subtle)] mb-1">
                  {t('exportDialog.tavernImportTitle')}
                </div>
                <p className="text-xs text-[var(--vscode-fg-subtle)] mb-3">{t('exportDialog.tavernImportHint')}</p>
                <input
                  ref={tavernFileInputRef}
                  type="file"
                  accept=".json,.png"
                  className="hidden"
                  onChange={handleTavernFileChosen}
                />
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => tavernFileInputRef.current?.click()}
                  disabled={tavernBusy}
                  className="gap-1.5"
                >
                  <Upload size={13} />
                  {tavernBusy ? t('exportDialog.tavernImporting') : t('exportDialog.tavernChooseFile')}
                </Button>

                {tavernImportPlan && (
                  <div className="mt-3 rounded-[8px] border border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] p-3 space-y-2">
                    <div className="text-sm font-medium">
                      {t('exportDialog.tavernPreviewTitle')}（{tavernImportPlan.source_format}）
                    </div>
                    <div className="text-xs text-[var(--vscode-fg-subtle)]">
                      {t('exportDialog.tavernPreviewSummary', {
                        characters: tavernImportPlan.characters.length,
                        world: tavernImportPlan.world_cards.length,
                      })}
                    </div>
                    {(tavernImportPlan.characters.length > 0 || tavernImportPlan.world_cards.length > 0) && (
                      <div className="text-xs max-h-24 overflow-y-auto">
                        {[...tavernImportPlan.characters, ...tavernImportPlan.world_cards].map((card) => (
                          <div key={card.name} className="truncate py-0.5">
                            {card.name}
                          </div>
                        ))}
                      </div>
                    )}
                    {tavernImportPlan.injection_detected && (
                      <div className="text-xs text-amber-500">{t('exportDialog.tavernInjectionWarning')}</div>
                    )}
                    {tavernImportPlan.dropped.length > 0 && (
                      <div>
                        <div className="text-xs font-medium mt-1">{t('exportDialog.tavernDroppedTitle')}</div>
                        <div className="text-xs text-[var(--vscode-fg-subtle)] max-h-20 overflow-y-auto">
                          {tavernImportPlan.dropped.map((item) => (
                            <div key={item.field} className="py-0.5">
                              {item.field}
                              {item.count > 1 ? ` ×${item.count}` : ''}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    <label className="flex items-center gap-2 text-xs text-[var(--vscode-fg-subtle)]">
                      <input
                        type="checkbox"
                        checked={tavernOverwrite}
                        onChange={(e) => setTavernOverwrite(e.target.checked)}
                      />
                      <span>{t('exportDialog.tavernOverwrite')}</span>
                    </label>
                    <div className="flex items-center gap-2 pt-1">
                      <Button size="sm" onClick={handleTavernConfirmImport} disabled={tavernBusy} className="gap-1.5">
                        <Upload size={13} />
                        {t('exportDialog.tavernConfirmImport')}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setTavernImportPlan(null);
                          setTavernImportFile(null);
                        }}
                        disabled={tavernBusy}
                      >
                        {t('common.cancel')}
                      </Button>
                    </div>
                  </div>
                )}

                {tavernImportResult && (
                  <div className="mt-3 rounded-[8px] border border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] p-3 text-xs space-y-1">
                    <div className="text-sm font-medium text-[var(--vscode-fg)]">
                      {t('exportDialog.tavernImportDone')}
                    </div>
                    <div>
                      {t('exportDialog.tavernImportCreated', {
                        characters: tavernImportResult.created_characters.length,
                        world: tavernImportResult.created_world_cards.length,
                      })}
                    </div>
                    {tavernImportResult.skipped_existing.length > 0 && (
                      <div className="text-[var(--vscode-fg-subtle)]">
                        {t('exportDialog.tavernImportSkipped', {
                          names: tavernImportResult.skipped_existing.join('、'),
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          ) : (
          <div className="grid grid-cols-1 md:grid-cols-[1.15fr_0.85fr] gap-0">
            <div className="p-4 border-b md:border-b-0 md:border-r border-[var(--vscode-input-border)]">
              <div className="flex items-center justify-between mb-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-[var(--vscode-fg-subtle)]">
                  {t('exportDialog.chapterScope')}
                </div>
                <button
                  onClick={handleToggleAll}
                  disabled={loading || chapterCount === 0}
                  className="text-xs px-2 py-1 rounded-[6px] border border-[var(--vscode-input-border)] hover:bg-[var(--vscode-list-hover)] disabled:opacity-50"
                >
                  {allSelected ? t('common.deselectAll') : t('common.selectAll')}
                </button>
              </div>

              <div className="max-h-72 overflow-y-auto rounded-[8px] border border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)]">
                {loading ? (
                  <div className="px-4 py-6 text-sm text-[var(--vscode-fg-subtle)]">{t('common.loading')}</div>
                ) : chapters.length === 0 ? (
                  <div className="px-4 py-6 text-sm text-[var(--vscode-fg-subtle)]">{t('exportDialog.noChapters')}</div>
                ) : (
                  chapters.map((chapter) => (
                    <label
                      key={chapter.id}
                      className="flex items-start gap-3 px-4 py-3 border-b last:border-b-0 border-[var(--vscode-input-border)] hover:bg-[var(--vscode-list-hover)] cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={selectedChapters.includes(chapter.id)}
                        onChange={() => toggleChapter(chapter.id)}
                        className="mt-0.5"
                      />
                      <div className="min-w-0">
                        <div className="text-sm font-medium truncate">{chapter.title}</div>
                        <div className="text-xs text-[var(--vscode-fg-subtle)] mt-1">{chapter.id}</div>
                      </div>
                    </label>
                  ))
                )}
              </div>
            </div>

            <div className="p-4 space-y-5">
              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-[var(--vscode-fg-subtle)] mb-3">
                  {t('exportDialog.format')}
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {FORMAT_OPTIONS.map((option) => {
                    const Icon = option.icon;
                    const active = format === option.id;
                    return (
                      <button
                        key={option.id}
                        onClick={() => setFormat(option.id)}
                        className={[
                          'rounded-[8px] border px-3 py-3 text-sm transition-colors',
                          active
                            ? 'border-[var(--vscode-focus-border)] bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
                            : 'border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] hover:bg-[var(--vscode-list-hover)]',
                        ].join(' ')}
                      >
                        <div className="flex flex-col items-center gap-2">
                          <Icon size={16} />
                          <span className="uppercase">{option.id}</span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-[var(--vscode-fg-subtle)] mb-3">
                  {t('exportDialog.options')}
                </div>
                <label className="flex items-center gap-3 text-sm">
                  <input type="checkbox" checked={includeTitles} onChange={(e) => setIncludeTitles(e.target.checked)} />
                  <span>{t('exportDialog.includeTitles')}</span>
                </label>
                <p className="text-xs text-[var(--vscode-fg-subtle)] mt-3">
                  {t('exportDialog.selectionHint', { count: selectedChapters.length })}
                </p>
              </div>
            </div>
          </div>
          )}

          {tab === 'manuscript' && (
          <div className="flex items-center justify-between gap-3 px-4 py-3 border-t border-[var(--vscode-input-border)] bg-[var(--vscode-bg)]">
            <div className="text-xs text-[var(--vscode-fg-subtle)]">{t('exportDialog.footerHint')}</div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={onClose}>
                {t('common.cancel')}
              </Button>
              <Button
                size="sm"
                onClick={handleExport}
                disabled={exporting || selectedChapters.length === 0}
                className="gap-2"
              >
                <Download size={14} />
                {exporting ? t('exportDialog.exporting') : t('common.export')}
              </Button>
            </div>
          </div>
          )}
        </div>
      </div>
    </>,
    document.body,
  );
}

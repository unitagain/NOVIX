import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Download, FileText, FileType2, FileCode2, X } from 'lucide-react';
import { draftsAPI, exportAPI } from '../../api';
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

export default function ExportDialog({ open, onClose, projectId, currentChapter }) {
  const { t } = useLocale();
  const [chapters, setChapters] = useState([]);
  const [selectedChapters, setSelectedChapters] = useState([]);
  const [format, setFormat] = useState('txt');
  const [includeTitles, setIncludeTitles] = useState(true);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);

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
        </div>
      </div>
    </>,
    document.body,
  );
}

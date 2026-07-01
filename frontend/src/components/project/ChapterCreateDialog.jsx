import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLocale } from '../../i18n';

/**
 * ChapterCreateDialog - 新建章节弹窗
 * 仅做视觉一致性优化，不改变数据与交互逻辑。
 */
export function ChapterCreateDialog({
  open,
  onClose,
  onConfirm,
  existingChapters = [],
  volumes = [],
  defaultVolumeId = 'V1',
}) {
  const { t } = useLocale();
  const [selectedVolume, setSelectedVolume] = useState('V1');
  const [suggestedId, setSuggestedId] = useState('');
  const [customId, setCustomId] = useState('');
  const [title, setTitle] = useState('');

  // 逻辑保持不变
  const availableVolumes = useMemo(
    () => (volumes.length ? volumes : [{ id: 'V1', title: t('volume.defaultV1') }]),
    [t, volumes],
  );

  const normalizeToVolume = (chapterId, volumeId) => {
    const trimmed = (chapterId || '').trim().toUpperCase();
    if (!trimmed) return '';
    if (trimmed.startsWith('V')) return trimmed;
    if (trimmed.startsWith('C')) return `${volumeId}${trimmed}`;
    return trimmed;
  };

  const parseVolumeId = (chapterId) => {
    const match = (chapterId || '').toUpperCase().match(/^V(\d+)/);
    return match ? `V${match[1]}` : 'V1';
  };

  const normalizedChapters = existingChapters.map((chapter) => {
    const volumeId = parseVolumeId(chapter.id);
    const normalizedId = normalizeToVolume(chapter.id, volumeId);
    return { ...chapter, volumeId, normalizedId };
  });

  useEffect(() => {
    if (!open) return;
    const currentVolumeChapters = normalizedChapters.filter(
      (chapter) => chapter.volumeId === selectedVolume && /C\d+$/i.test(chapter.normalizedId),
    );
    let maxChapter = 0;
    currentVolumeChapters.forEach((chapter) => {
      const match = chapter.normalizedId.match(/C(\d+)/i);
      if (match) maxChapter = Math.max(maxChapter, Number.parseInt(match[1], 10));
    });
    setSuggestedId(`${selectedVolume}C${maxChapter + 1}`);
    setCustomId('');
  }, [normalizedChapters, open, selectedVolume]);

  useEffect(() => {
    if (open) {
      setTitle('');
      setCustomId('');
      const fallback = availableVolumes[0]?.id || 'V1';
      const target = availableVolumes.find((v) => v.id === defaultVolumeId) ? defaultVolumeId : fallback;
      setSelectedVolume(target);
    }
  }, [availableVolumes, defaultVolumeId, open]);

  const rawId = customId || suggestedId;
  const finalId = normalizeToVolume(rawId, selectedVolume);
  const canCreate = Boolean(title && finalId);
  const _normalChapters = normalizedChapters.filter(
    (chapter) => chapter.volumeId === selectedVolume && /C\d+$/i.test(chapter.normalizedId),
  );

  if (!open) return null;

  return createPortal(
    <>
      {/* 简洁遮罩 */}
      <div className="fixed inset-0 z-[100]" onClick={onClose} />

      {/* 命令面板 */}
      <div className="vscode-command-palette anti-theme z-[101]">
        <div className="bg-[var(--vscode-input-bg)] p-1">
          <div className="px-2 py-1.5 text-xs text-[var(--vscode-fg-subtle)] font-bold uppercase tracking-wider border-b border-[var(--vscode-input-border)] mb-2">
            {t('chapter.newChapterDialog')}
          </div>

          <div className="space-y-3 px-2 pb-3">
            {/* 分卷选择 */}
            <div className="grid grid-cols-[80px_1fr] items-center gap-2">
              <label className="text-[11px] text-right text-[var(--vscode-fg-subtle)]">
                {t('chapter.volumeLabel')}
              </label>
              <select
                value={selectedVolume}
                onChange={(e) => setSelectedVolume(e.target.value)}
                className="w-full text-xs bg-[var(--vscode-input-bg)] border border-[var(--vscode-input-border)] px-2 py-1 outline-none focus:border-[var(--vscode-focus-border)]"
              >
                {availableVolumes.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.id} - {v.title}
                  </option>
                ))}
              </select>
            </div>

            {/* 编号输入 */}
            <div className="grid grid-cols-[80px_1fr] items-center gap-2">
              <label className="text-[11px] text-right text-[var(--vscode-fg-subtle)]">
                {t('chapter.numberLabel')}
              </label>
              <div className="flex items-center gap-2">
                <input
                  value={customId}
                  onChange={(e) => setCustomId(e.target.value.toUpperCase())}
                  placeholder={suggestedId}
                  className="w-24 text-xs font-mono bg-[var(--vscode-input-bg)] border border-[var(--vscode-input-border)] px-2 py-1 outline-none focus:border-[var(--vscode-focus-border)]"
                />
                <span className="text-[10px] text-[var(--vscode-fg-subtle)]">
                  {t('chapter.resultLabel')}
                  {finalId}
                </span>
              </div>
            </div>

            {/* 标题输入 */}
            <div className="grid grid-cols-[80px_1fr] items-center gap-2">
              <label className="text-[11px] text-right text-[var(--vscode-fg-subtle)]">
                {t('chapter.titleFieldLabel')}
              </label>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={t('chapter.titlePlaceholder')}
                className="w-full text-xs font-bold bg-[var(--vscode-input-bg)] border border-[var(--vscode-input-border)] px-2 py-1 outline-none focus:border-[var(--vscode-focus-border)]"
                autoFocus
              />
            </div>
          </div>

          {/* 底部操作 */}
          <div className="flex justify-end gap-2 p-2 border-t border-[var(--vscode-input-border)] bg-[var(--vscode-sidebar-bg)]">
            <button
              onClick={onClose}
              className="px-3 py-1 text-xs border border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] hover:bg-[var(--vscode-list-hover)]"
            >
              {t('common.cancel')}
            </button>
            <button
              onClick={() => {
                if (canCreate) {
                  onConfirm({ id: finalId, title, type: 'normal' });
                  onClose();
                }
              }}
              disabled={!canCreate}
              className="px-3 py-1 text-xs text-white bg-[var(--vscode-list-active)] hover:opacity-90 disabled:opacity-50"
            >
              {t('common.create')}
            </button>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}

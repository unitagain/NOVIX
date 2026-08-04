const normalizeChapter = (value) => String(value || '').trim();

export const lastChapterStorageKey = (projectId) => `wenshape.last-chapter.${String(projectId || '')}`;

export function resolveRestoredChapter(chapters = [], storedChapter = '') {
  const normalized = (Array.isArray(chapters) ? chapters : []).map(normalizeChapter).filter(Boolean);
  const stored = normalizeChapter(storedChapter);
  return stored && normalized.includes(stored) ? stored : normalized[0] || '';
}

export function shouldApplyLoadedChapter({ selectedChapter, loadState, loadedContent, hasUnsavedChanges }) {
  const selected = normalizeChapter(selectedChapter);
  if (!selected || loadedContent === undefined) return false;
  if (normalizeChapter(loadState?.chapter) !== selected) return false;
  return !loadState?.ready || !hasUnsavedChanges;
}

export function canAutosaveLoadedChapter({ projectId, chapter, loadState, hasUnsavedChanges, blocked }) {
  const selected = normalizeChapter(chapter);
  return Boolean(
    projectId &&
      selected &&
      hasUnsavedChanges &&
      !blocked &&
      loadState?.ready &&
      normalizeChapter(loadState.chapter) === selected,
  );
}


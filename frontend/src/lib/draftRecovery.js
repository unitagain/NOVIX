const RECOVERY_SCHEMA_VERSION = 1;

export const draftRecoveryStorageKey = (projectId, chapter) =>
  `wenshape.draft-recovery.${encodeURIComponent(String(projectId || ''))}.${encodeURIComponent(String(chapter || ''))}`;

export function writeDraftRecovery(storage, snapshot) {
  const projectId = String(snapshot?.projectId || '');
  const chapter = String(snapshot?.chapter || '');
  if (!storage || !projectId || !chapter) return false;
  try {
    storage.setItem(
      draftRecoveryStorageKey(projectId, chapter),
      JSON.stringify({
        schemaVersion: RECOVERY_SCHEMA_VERSION,
        projectId,
        chapter,
        content: String(snapshot?.content ?? ''),
        title: snapshot?.title ? String(snapshot.title) : null,
        savedContent: String(snapshot?.savedContent ?? ''),
        turnEffect:
          snapshot?.turnEffect && typeof snapshot.turnEffect === 'object' ? snapshot.turnEffect : null,
        needsCanonSync:
          snapshot?.needsCanonSync === true || Boolean(snapshot?.turnEffect && typeof snapshot.turnEffect === 'object'),
        updatedAt: Number(snapshot?.updatedAt || Date.now()),
      }),
    );
    return true;
  } catch {
    return false;
  }
}

export function readDraftRecovery(storage, projectId, chapter) {
  if (!storage || !projectId || !chapter) return null;
  try {
    const raw = storage.getItem(draftRecoveryStorageKey(projectId, chapter));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed?.schemaVersion !== RECOVERY_SCHEMA_VERSION) return null;
    if (String(parsed.projectId || '') !== String(projectId) || String(parsed.chapter || '') !== String(chapter)) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function clearDraftRecovery(storage, projectId, chapter) {
  if (!storage || !projectId || !chapter) return;
  try {
    storage.removeItem(draftRecoveryStorageKey(projectId, chapter));
  } catch {
    // Storage cleanup is best-effort.
  }
}

export function resolveDraftRecovery(snapshot, serverContent) {
  if (!snapshot || typeof snapshot.content !== 'string') {
    return { action: 'none', content: String(serverContent ?? ''), needsCanonSync: false, turnEffect: null };
  }
  const persisted = String(serverContent ?? '');
  if (snapshot.content === persisted) {
    const shouldSyncCanon = snapshot.needsCanonSync === true && Boolean(snapshot.turnEffect);
    return {
      action: shouldSyncCanon ? 'sync_canon' : 'clear',
      content: persisted,
      needsCanonSync: shouldSyncCanon,
      turnEffect: snapshot.turnEffect || null,
    };
  }
  return {
    action: 'restore',
    content: snapshot.content,
    title: snapshot.title || null,
    needsCanonSync: snapshot.needsCanonSync === true,
    turnEffect: snapshot.turnEffect || null,
  };
}

export function canSendKeepaliveDraft(payload, maxBytes = 60000) {
  if (!payload?.projectId || !payload?.chapter) return false;
  const body = JSON.stringify({ content: String(payload.content ?? ''), ...(payload.title ? { title: payload.title } : {}) });
  return new TextEncoder().encode(body).length <= maxBytes;
}

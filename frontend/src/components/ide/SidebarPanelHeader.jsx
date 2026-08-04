import React from 'react';

export function SidebarPanelHeader({ title, meta, actions, className = '' }) {
  return (
    <header className={`sidebar-panel-header flex min-h-10 shrink-0 items-center gap-2 px-3 ${className}`}>
      <h2 className="ui-panel-title min-w-0 truncate text-[var(--vscode-fg)]">{title}</h2>
      {meta ? <span className="ui-caption truncate text-[var(--vscode-fg-subtle)]">{meta}</span> : null}
      {actions ? <div className="ml-auto flex items-center gap-0.5">{actions}</div> : null}
    </header>
  );
}

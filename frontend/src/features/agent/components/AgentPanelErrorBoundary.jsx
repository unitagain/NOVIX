import React from 'react';
import { AlertTriangle } from 'lucide-react';

export class AgentPanelErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('Agent panel render failed', error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex h-full items-center justify-center p-6 text-center" role="alert">
        <div className="max-w-xs">
          <AlertTriangle className="mx-auto text-amber-600" size={22} />
          <h2 className="mt-3 text-sm font-semibold text-[var(--vscode-fg)]">Agent 面板暂时无法显示</h2>
          <p className="mt-1 text-xs leading-5 text-[var(--vscode-fg-subtle)]">编辑器内容未受影响，可重新加载面板恢复。</p>
          <button
            type="button"
            onClick={() => this.setState({ error: null })}
            className="mt-3 rounded-[7px] border border-[var(--vscode-input-border)] px-3 py-1.5 text-xs hover:bg-[var(--vscode-list-hover)]"
          >
            重试
          </button>
        </div>
      </div>
    );
  }
}

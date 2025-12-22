import { useEffect, useMemo, useState } from 'react';
import { Button } from './ui/Button';
import { Card, CardHeader, CardTitle, CardContent } from './ui/Card';
import { Settings, Shield, Key } from 'lucide-react';

function LLMSetupModal({ open, status, onClose, onSave }) {
  const [defaultProvider, setDefaultProvider] = useState('mock');
  const [agentOverrides, setAgentOverrides] = useState({
    archivist: '',
    writer: '',
    reviewer: '',
    editor: '',
  });
  const [openaiKey, setOpenaiKey] = useState('');
  const [anthropicKey, setAnthropicKey] = useState('');
  const [deepseekKey, setDeepseekKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setError('');
    const initialDefault = status?.default_provider || status?.selected_provider || 'mock';
    setDefaultProvider(initialDefault);
    const overrides = status?.agent_overrides;
    if (overrides && typeof overrides === 'object') {
      setAgentOverrides({
        archivist: overrides.archivist || '',
        writer: overrides.writer || '',
        reviewer: overrides.reviewer || '',
        editor: overrides.editor || '',
      });
    } else {
      setAgentOverrides({ archivist: '', writer: '', reviewer: '', editor: '' });
    }
  }, [open, status]);

  const providers = useMemo(() => status?.providers || [], [status]);
  const configured = useMemo(() => status?.configured || {}, [status]);

  const fallbackProviders = useMemo(() => ([
    { id: 'openai', label: 'OpenAI', requires_key: true },
    { id: 'anthropic', label: 'Anthropic (Claude)', requires_key: true },
    { id: 'deepseek', label: 'DeepSeek', requires_key: true },
    { id: 'mock', label: 'Mock (Demo)', requires_key: false },
  ]), []);

  const providerMeta = (id) => {
    const fromApi = providers.find((x) => x.id === id);
    if (fromApi) return fromApi;
    return fallbackProviders.find((x) => x.id === id);
  };

  const effectiveProviders = useMemo(() => {
    return {
      archivist: agentOverrides.archivist || defaultProvider,
      writer: agentOverrides.writer || defaultProvider,
      reviewer: agentOverrides.reviewer || defaultProvider,
      editor: agentOverrides.editor || defaultProvider,
    };
  }, [agentOverrides, defaultProvider]);

  const requiredProviders = useMemo(() => {
    const set = new Set(Object.values(effectiveProviders));
    return Array.from(set);
  }, [effectiveProviders]);

  const requiredKeyProviders = useMemo(() => {
    return requiredProviders.filter((p) => providerMeta(p)?.requires_key);
  }, [requiredProviders, providers]);

  if (!open) return null;

  const submit = async () => {
    setSaving(true);
    setError('');
    try {
      const missing = requiredKeyProviders.filter((p) => !configured?.[p]);
      const missingNeedInput = [];
      for (const p of missing) {
        if (p === 'openai' && !openaiKey.trim()) missingNeedInput.push('OpenAI API Key');
        if (p === 'anthropic' && !anthropicKey.trim()) missingNeedInput.push('Anthropic API Key');
        if (p === 'deepseek' && !deepseekKey.trim()) missingNeedInput.push('DeepSeek API Key');
      }
      if (missingNeedInput.length) {
        setError(`缺少必要的密钥：${missingNeedInput.join('、')}`);
        setSaving(false);
        return;
      }

      const payload = {
        provider: defaultProvider,
        default_provider: defaultProvider,
        agent_providers: {
          archivist: agentOverrides.archivist,
          writer: agentOverrides.writer,
          reviewer: agentOverrides.reviewer,
          editor: agentOverrides.editor,
        },
      };
      if (openaiKey.trim()) payload.openai_api_key = openaiKey.trim();
      if (anthropicKey.trim()) payload.anthropic_api_key = anthropicKey.trim();
      if (deepseekKey.trim()) payload.deepseek_api_key = deepseekKey.trim();

      await onSave(payload);
      onClose();
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || '保存失败';
      setError(String(msg));
    } finally {
      setSaving(false);
    }
  };

  const keyInput = () => {
    if (!requiredKeyProviders.length) {
      return (
        <div className="text-sm text-muted-foreground bg-primary/5 p-3 rounded border border-primary/10">
          当前提供商无需 API 密钥（或已配置）。可用于演示/测试。
        </div>
      );
    }

    return (
      <div className="space-y-3">
        {requiredKeyProviders.includes('openai') && (
          <div>
            <label className="block text-xs font-mono text-muted-foreground uppercase mb-1">OpenAI API Key</label>
            <input
              type="password"
              value={openaiKey}
              onChange={(e) => setOpenaiKey(e.target.value)}
              className="w-full px-3 py-2 bg-black/50 border border-border rounded-md focus:outline-none focus:border-primary text-white text-sm"
              placeholder={configured?.openai ? '(已配置)' : 'sk-...'}
            />
          </div>
        )}
        {requiredKeyProviders.includes('anthropic') && (
          <div>
            <label className="block text-xs font-mono text-muted-foreground uppercase mb-1">Anthropic API Key</label>
            <input
              type="password"
              value={anthropicKey}
              onChange={(e) => setAnthropicKey(e.target.value)}
              className="w-full px-3 py-2 bg-black/50 border border-border rounded-md focus:outline-none focus:border-primary text-white text-sm"
              placeholder={configured?.anthropic ? '(已配置)' : 'sk-ant-...'}
            />
          </div>
        )}
        {requiredKeyProviders.includes('deepseek') && (
          <div>
            <label className="block text-xs font-mono text-muted-foreground uppercase mb-1">DeepSeek API Key</label>
            <input
              type="password"
              value={deepseekKey}
              onChange={(e) => setDeepseekKey(e.target.value)}
              className="w-full px-3 py-2 bg-black/50 border border-border rounded-md focus:outline-none focus:border-primary text-white text-sm"
              placeholder={configured?.deepseek ? '(已配置)' : '...'}
            />
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
      <Card className="w-full max-w-lg border-primary/20 shadow-2xl bg-card">
        <CardHeader className="border-b border-white/10 pb-4">
          <CardTitle className="text-xl flex items-center gap-2">
            <Settings className="text-primary" /> 
            <span>LLM 配置</span>
          </CardTitle>
          <p className="text-sm text-muted-foreground mt-1">为写作系统选择模型提供商</p>
        </CardHeader>

        <CardContent className="space-y-6 pt-6">
          <div>
            <div className="text-sm font-bold text-white mb-3 flex items-center gap-2">
              <Shield size={14} className="text-primary" /> 默认提供商
            </div>
            <div className="space-y-2">
              {(providers.length ? providers : [
                { id: 'openai', label: 'OpenAI', requires_key: true },
                { id: 'anthropic', label: 'Anthropic (Claude)', requires_key: true },
                { id: 'deepseek', label: 'DeepSeek', requires_key: true },
                { id: 'mock', label: 'Mock (Demo)', requires_key: false },
              ]).map((p) => (
                <label key={p.id} className={`flex items-center justify-between p-3 border rounded-md cursor-pointer transition-all ${
                  defaultProvider === p.id 
                    ? 'bg-primary/10 border-primary text-white' 
                    : 'bg-black/20 border-border text-muted-foreground hover:border-white/20 hover:text-white'
                }`}>
                  <div className="flex items-center gap-3">
                    <input
                      type="radio"
                      name="defaultProvider"
                      className="accent-primary"
                      checked={defaultProvider === p.id}
                      onChange={() => setDefaultProvider(p.id)}
                    />
                    <div>
                      <div className="font-bold text-sm">{p.label}</div>
                      <div className="text-xs font-mono opacity-70">{p.requires_key ? '需要密钥' : '无需密钥'}</div>
                    </div>
                  </div>
                </label>
              ))}
            </div>
          </div>

          <div>
            <div className="text-sm font-bold text-white mb-3">智能体覆盖配置 (可选)</div>
            <div className="space-y-3 bg-black/20 p-4 rounded-lg border border-white/5">
              {[
                { id: 'archivist', title: '档案员' },
                { id: 'writer', title: '作家' },
                { id: 'reviewer', title: '审阅员' },
                { id: 'editor', title: '编辑' },
              ].map((row) => (
                <div key={row.id} className="grid grid-cols-3 gap-3 items-center">
                  <div className="text-sm font-mono text-muted-foreground uppercase">{row.title}</div>
                  <select
                    value={agentOverrides[row.id]}
                    onChange={(e) => setAgentOverrides((prev) => ({ ...prev, [row.id]: e.target.value }))}
                    className="col-span-2 px-3 py-1.5 border border-border rounded bg-black/50 text-white text-sm focus:outline-none focus:border-primary"
                  >
                    <option value="">默认 ({defaultProvider})</option>
                    {(providers.length ? providers : [
                      { id: 'openai', label: 'OpenAI' },
                      { id: 'anthropic', label: 'Anthropic' },
                      { id: 'deepseek', label: 'DeepSeek' },
                      { id: 'mock', label: 'Mock (Demo)' },
                    ]).map((p) => (
                      <option key={p.id} value={p.id}>{p.label}</option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
            <div className="mt-2 text-xs font-mono text-muted-foreground opacity-50 truncate">
              Active: {effectiveProviders.archivist} / {effectiveProviders.writer} / {effectiveProviders.reviewer} / {effectiveProviders.editor}
            </div>
          </div>

          <div className="border-t border-white/10 pt-4">
             <div className="text-sm font-bold text-white mb-3 flex items-center gap-2">
              <Key size={14} className="text-primary" /> API 密钥
            </div>
             {keyInput()}
          </div>

          {error && (
            <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded p-3 font-mono">
              错误: {error}
            </div>
          )}

          <div className="flex items-center justify-end gap-3 pt-2">
            <Button
              onClick={submit}
              isLoading={saving}
              className="w-full md:w-auto font-bold"
            >
              {saving ? '保存中...' : '保存配置'}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export default LLMSetupModal;

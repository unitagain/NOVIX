import { useEffect, useMemo, useState } from 'react';
import { Cpu, Key, Shield, Save, RefreshCw } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { configAPI } from '../api';

function Agents() {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const [status, setStatus] = useState(null);
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

  const providers = useMemo(() => status?.providers || [], [status]);
  const configured = useMemo(() => status?.configured || {}, [status]);

  const providerMeta = (id) => providers.find((x) => x.id === id);

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

  const load = async () => {
    setLoading(true);
    setError('');
    setSuccess('');
    try {
      const resp = await configAPI.getLLM();
      const data = resp?.data || null;
      setStatus(data);

      const initialDefault = data?.default_provider || data?.selected_provider || 'mock';
      setDefaultProvider(initialDefault);

      const overrides = data?.agent_overrides;
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
    } catch (e) {
      setError(String(e?.response?.data?.detail || e?.message || '加载失败'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    if (saving) return;
    setSaving(true);
    setError('');
    setSuccess('');

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

      await configAPI.updateLLM(payload);
      setSuccess('配置已保存');
      setOpenaiKey('');
      setAnthropicKey('');
      setDeepseekKey('');
      await load();
    } catch (e) {
      setError(String(e?.response?.data?.detail || e?.message || '保存失败'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-6">
      <Card className="border-primary/20 bg-card/60 backdrop-blur-sm">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>
            <Cpu size={18} /> 智能体 · 模型配置
          </CardTitle>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
              <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
            </Button>
          </div>
        </CardHeader>

        <CardContent className="space-y-6">
          <div className="text-sm text-muted-foreground">
            为每个智能体选择默认模型提供商，并按需覆盖到单个角色。保存后会写入后端 <code>.env</code> 并热更新配置。
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="p-4 rounded-lg border border-border bg-black/20">
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
                  <label
                    key={p.id}
                    className={`flex items-center justify-between p-3 border rounded-md cursor-pointer transition-all ${
                      defaultProvider === p.id
                        ? 'bg-primary/10 border-primary text-white'
                        : 'bg-black/20 border-border text-muted-foreground hover:border-white/20 hover:text-white'
                    }`}
                  >
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
                    <div className="text-xs font-mono opacity-60">
                      {configured?.[p.id] ? '已配置' : p.requires_key ? '未配置' : '可用'}
                    </div>
                  </label>
                ))}
              </div>
            </div>

            <div className="p-4 rounded-lg border border-border bg-black/20">
              <div className="text-sm font-bold text-white mb-3">角色覆盖（可选）</div>
              <div className="space-y-3">
                {[
                  { id: 'archivist', title: '资料管理员' },
                  { id: 'writer', title: '撰稿人' },
                  { id: 'reviewer', title: '审稿人' },
                  { id: 'editor', title: '编辑' },
                ].map((row) => (
                  <div key={row.id} className="grid grid-cols-3 gap-3 items-center">
                    <div className="text-sm font-medium text-muted-foreground">{row.title}</div>
                    <select
                      value={agentOverrides[row.id]}
                      onChange={(e) => setAgentOverrides((prev) => ({ ...prev, [row.id]: e.target.value }))}
                      className="col-span-2 px-3 py-2 border border-border rounded bg-black/50 text-white text-sm focus:outline-none focus:border-primary"
                    >
                      <option value="">默认（{defaultProvider}）</option>
                      {(providers.length ? providers : [
                        { id: 'openai', label: 'OpenAI' },
                        { id: 'anthropic', label: 'Anthropic (Claude)' },
                        { id: 'deepseek', label: 'DeepSeek' },
                        { id: 'mock', label: 'Mock (Demo)' },
                      ]).map((p) => (
                        <option key={p.id} value={p.id}>{p.label}</option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
              <div className="mt-3 text-xs font-mono text-muted-foreground opacity-60 truncate">
                Effective: {effectiveProviders.archivist} / {effectiveProviders.writer} / {effectiveProviders.reviewer} / {effectiveProviders.editor}
              </div>
            </div>
          </div>

          <div className="p-4 rounded-lg border border-border bg-black/20">
            <div className="text-sm font-bold text-white mb-3 flex items-center gap-2">
              <Key size={14} className="text-primary" /> API 密钥
            </div>

            {!requiredKeyProviders.length ? (
              <div className="text-sm text-muted-foreground bg-primary/5 p-3 rounded border border-primary/10">
                当前选择无需 API 密钥（或已配置）。可用于演示/测试。
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {requiredKeyProviders.includes('openai') && (
                  <div>
                    <label className="block text-xs font-mono text-muted-foreground uppercase mb-1">OpenAI API Key</label>
                    <input
                      type="password"
                      value={openaiKey}
                      onChange={(e) => setOpenaiKey(e.target.value)}
                      className="w-full px-3 py-2 bg-black/50 border border-border rounded-md focus:outline-none focus:border-primary text-white text-sm"
                      placeholder={configured?.openai ? '(已配置，留空则不修改)' : 'sk-...'}
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
                      placeholder={configured?.anthropic ? '(已配置，留空则不修改)' : 'sk-ant-...'}
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
                      placeholder={configured?.deepseek ? '(已配置，留空则不修改)' : '...'}
                    />
                  </div>
                )}
              </div>
            )}
          </div>

          {error && (
            <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded p-3 font-mono">
              错误: {error}
            </div>
          )}
          {success && (
            <div className="text-sm text-green-400 bg-green-500/10 border border-green-500/20 rounded p-3 font-mono">
              {success}
            </div>
          )}

          <div className="flex items-center justify-end gap-3">
            <Button onClick={save} isLoading={saving} className="font-bold">
              <Save size={16} className="mr-2" /> 保存配置
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export default Agents;

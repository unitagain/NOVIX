/** LLM profile modal for creating and editing provider configurations. */

import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Save, X, RefreshCw, Trash2 } from 'lucide-react';
import { Button, Input } from './ui/core';
import { configAPI } from '../api';
import logger from '../utils/logger';
import { extractErrorDetail } from '../utils/extractError';
import { useLocale } from '../i18n';

const PROVIDER_DEFAULT_BASE_URLS = {
  openai: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com',
  deepseek: 'https://api.deepseek.com/v1',
  gemini: 'https://generativelanguage.googleapis.com/v1beta/openai/',
  qwen: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  kimi: 'https://api.moonshot.cn/v1',
  glm: 'https://open.bigmodel.cn/api/paas/v4',
  grok: 'https://api.x.ai/v1',
  wenxin: 'https://qianfan.baidubce.com/v2',
  aistudio: 'https://aistudio.baidu.com/llm/lmapi/v3',
  custom: '',
};

const getDefaultBaseUrl = (provider) => PROVIDER_DEFAULT_BASE_URLS[provider] || '';

/**
 * LLM 配置对话框 - 创建和编辑 LLM 提供商配置
 *
 * Modal for creating and editing LLM provider profiles (API keys, model selection, parameters).
 * Preserves data structure and interaction semantics without modification.
 *
 * @component
 * @example
 * return (
 *   <LLMProfileModal
 *     open={true}
 *     profile={existingProfile}
 *     onClose={handleClose}
 *     onSave={handleSave}
 *     onDelete={handleDelete}
 *   />
 * )
 *
 * @param {Object} props - Component props
 * @param {boolean} [props.open=false] - 对话框是否打开 / Whether dialog is open
 * @param {Object} [props.profile=null] - 现有配置文件 / Existing profile to edit
 * @param {Function} [props.onClose] - 关闭回调 / Close callback
 * @param {Function} [props.onSave] - 保存回调 / Save callback
 * @param {Function} [props.onDelete] - 删除回调 / Delete callback
 * @returns {JSX.Element} LLM 配置对话框 / LLM profile modal element
 */
export default function LLMProfileModal({ open, profile, onClose, onSave, onDelete }) {
  const { t } = useLocale();
  const [fetchedModels, setFetchedModels] = useState([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [testingModel, setTestingModel] = useState(false);
  const [fetchWarning, setFetchWarning] = useState('');
  const [actionFeedback, setActionFeedback] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [customModelInput, setCustomModelInput] = useState(false);

  const [formData, setFormData] = useState({
    name: '',
    provider: 'openai',
    api_key: '',
    model: '',
    base_url: '',
    deployed_models: [],
    temperature: 0.7,
    max_tokens: 8000,
    max_context_tokens: '',
    has_api_key: false,
    api_key_mask: '',
    clear_api_key: false,
  });

  useEffect(() => {
    if (open) {
      const persistedModels = Array.isArray(profile?.deployed_models)
        ? profile.deployed_models.filter((m) => typeof m === 'string' && m.trim())
        : [];
      setFetchedModels(persistedModels);
      setFetchWarning('');
      setActionFeedback(null);
      setCustomModelInput(false);
      if (profile) {
        setFormData({
          id: profile.id,
          name: profile.name || '',
          provider: profile.provider || 'openai',
          api_key: profile.api_key || '',
          model: profile.model || '',
          base_url: profile.base_url || getDefaultBaseUrl(profile.provider),
          deployed_models: persistedModels,
          temperature: profile.temperature || 0.7,
          max_tokens: profile.max_tokens || 8000,
          max_context_tokens: profile.max_context_tokens || '',
          has_api_key: Boolean(profile.has_api_key),
          api_key_mask: profile.api_key_mask || '',
          clear_api_key: false,
        });
      } else {
        setFormData({
          name: t('llmModal.defaultName'),
          provider: 'openai',
          api_key: '',
          model: '',
          base_url: getDefaultBaseUrl('openai'),
          deployed_models: [],
          temperature: 0.7,
          max_tokens: 8000,
          max_context_tokens: '',
          has_api_key: false,
          api_key_mask: '',
          clear_api_key: false,
        });
      }
    }
  }, [open, profile, t]);

  if (!open) return null;

  const showFeedback = (type, message) => {
    setActionFeedback({ type, message: String(message || '').trim() });
  };

  const PROVIDERS = [
    { id: 'anthropic', label: 'Anthropic' },
    { id: 'deepseek', label: 'DeepSeek \u6df1\u5ea6\u6c42\u7d22' },
    { id: 'gemini', label: 'Gemini' },
    { id: 'glm', label: 'GLM \u667a\u8c31' },
    { id: 'grok', label: 'Grok' },
    { id: 'kimi', label: 'Kimi \u6708\u4e4b\u6697\u9762' },
    { id: 'openai', label: 'OpenAI' },
    { id: 'qwen', label: 'Qwen \u901a\u4e49\u5343\u95ee' },
    { id: 'wenxin', label: 'Wenxin \u6587\u5fc3\u4e00\u8a00' },
    { id: 'aistudio', label: 'AI Studio \u98de\u6868' },
    { id: 'custom', label: t('llmModal.providerCustom') },
  ].sort((a, b) => a.label.localeCompare(b.label));

  const OPENAI_MODELS = [
    { id: 'gpt-5.5', label: 'GPT-5.5' },
    { id: 'gpt-5.5-thinking', label: 'GPT-5.5 Thinking' },
    { id: 'gpt-5.5-instant', label: 'GPT-5.5 Instant' },
    { id: 'gpt-5.4', label: 'GPT-5.4' },
    { id: 'gpt-5.4-mini', label: 'GPT-5.4 Mini' },
    { id: 'o3', label: 'o3' },
    { id: 'o3-pro', label: 'o3-pro' },
    { id: 'o4-mini', label: 'o4-mini' },
    { id: 'gpt-4.1', label: 'GPT-4.1' },
    { id: 'gpt-4o', label: 'GPT-4o' },
  ];

  const ANTHROPIC_MODELS = [
    { id: 'claude-opus-4-8', label: 'Claude Opus 4.8' },
    { id: 'claude-mythos-preview', label: 'Claude Mythos Preview' },
    { id: 'claude-opus-4-6', label: 'Claude Opus 4.6' },
    { id: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6' },
    { id: 'claude-sonnet-4-5', label: 'Claude Sonnet 4.5' },
    { id: 'claude-haiku-4-5', label: 'Claude Haiku 4.5' },
  ];

  const DEEPSEEK_MODELS = [
    { id: 'deepseek-chat', label: 'DeepSeek-V4 (chat)' },
    { id: 'deepseek-reasoner', label: 'DeepSeek-R1 (reasoner)' },
    { id: 'deepseek-v3.2', label: 'DeepSeek-V3.2' },
    { id: 'deepseek-v3.1', label: 'DeepSeek-V3.1' },
  ];

  const GEMINI_MODELS = [
    { id: 'gemini-3.5-pro', label: 'Gemini 3.5 Pro' },
    { id: 'gemini-3.5-flash', label: 'Gemini 3.5 Flash' },
    { id: 'gemini-3.1-pro', label: 'Gemini 3.1 Pro' },
    { id: 'gemini-3-pro', label: 'Gemini 3 Pro' },
    { id: 'gemini-3-flash', label: 'Gemini 3 Flash' },
    { id: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
    { id: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
  ];

  const GROK_MODELS = [
    { id: 'grok-4.3', label: 'Grok 4.3' },
    { id: 'grok-4.2', label: 'Grok 4.2' },
    { id: 'grok-4-fast', label: 'Grok 4 Fast' },
    { id: 'grok-4', label: 'Grok 4' },
    { id: 'grok-3', label: 'Grok 3' },
    { id: 'grok-3-mini', label: 'Grok 3 Mini' },
  ];

  const KIMI_MODELS = [
    { id: 'kimi-k2.5', label: 'Kimi K2.5' },
    { id: 'kimi-k2-turbo-preview', label: 'Kimi K2 Turbo' },
    { id: 'kimi-k2-thinking', label: 'Kimi K2 Thinking' },
  ];

  const GLM_MODELS = [
    { id: 'glm-5.2', label: 'GLM-5.2' },
    { id: 'glm-5.1', label: 'GLM-5.1' },
    { id: 'glm-5', label: 'GLM-5' },
    { id: 'glm-4.7-flash', label: 'GLM-4.7 Flash' },
    { id: 'glm-4.7', label: 'GLM-4.7' },
  ];

  const QWEN_MODELS = [
    { id: 'qwen3.7-max', label: 'Qwen 3.7 Max' },
    { id: 'qwen3.7-plus', label: 'Qwen 3.7 Plus' },
    { id: 'qwen3.6-plus', label: 'Qwen 3.6 Plus' },
    { id: 'qwen3.5-plus', label: 'Qwen 3.5 Plus' },
    { id: 'qwen3-max-thinking', label: 'Qwen 3 Max Thinking' },
    { id: 'qwen-plus', label: 'Qwen Plus' },
  ];

  const WENXIN_MODELS = [
    { id: 'ernie-5.0', label: 'ERNIE 5.0' },
    { id: 'ernie-x1-turbo-32k', label: 'ERNIE X1 Turbo 32K' },
    { id: 'ernie-4.5-turbo-32k', label: 'ERNIE 4.5 Turbo 32K' },
    { id: 'ernie-4.5-8k', label: 'ERNIE 4.5 8K' },
  ];

  const AISTUDIO_MODELS = [
    { id: 'ernie-5.0-thinking-preview', label: 'ERNIE 5.0 Thinking Preview' },
    { id: 'ernie-5.0', label: 'ERNIE 5.0' },
  ];

  const handleFetchModels = async () => {
    const canUseCredential = Boolean(formData.api_key || (formData.id && formData.has_api_key && !formData.clear_api_key));
    if (!canUseCredential) {
      showFeedback('error', t('llmModal.pleaseEnterKey'));
      return;
    }
    if (!formData.base_url) {
      showFeedback('error', t('llmModal.fillRequiredBeforeTest'));
      return;
    }

    setFetchingModels(true);
    setActionFeedback(null);
    try {
      const res = await configAPI.fetchModels({
        provider: formData.provider,
        api_key: formData.api_key || undefined,
        profile_id: formData.id || undefined,
        base_url: formData.base_url,
      });
      if (res.data && res.data.models) {
        const models = Array.from(new Set(res.data.models.map((m) => String(m || '').trim()).filter(Boolean)));
        setFetchedModels(models);
        setFormData((prev) => ({ ...prev, deployed_models: models }));
        setFetchWarning(res.data.warning ? String(res.data.warning) : '');
        // Auto-select the first fetched model when the form has no current model.
        if (!formData.model && models.length > 0) {
          setFormData((prev) => ({ ...prev, model: models[0] }));
        }
        if (models.length > 0) {
          showFeedback('success', `${t('llmModal.fetchModels')}：${models.length}`);
        } else {
          showFeedback('info', t('llmModal.fetching'));
        }
      }
    } catch (error) {
      logger.error('Failed to fetch models', error);
      setFetchWarning('');
      showFeedback('error', t('llmModal.fetchFailed', { message: extractErrorDetail(error) }));
    } finally {
      setFetchingModels(false);
    }
  };

  const canTestModel = Boolean(
    String(formData.provider || '').trim() &&
    (String(formData.api_key || '').trim() || (formData.id && formData.has_api_key && !formData.clear_api_key)) &&
    String(formData.model || '').trim() &&
    String(formData.base_url || '').trim(),
  );

  const handleTestModel = async () => {
    if (!canTestModel) {
      showFeedback('error', t('llmModal.fillRequiredBeforeTest'));
      return;
    }

    setTestingModel(true);
    setActionFeedback(null);
    try {
      const res = await configAPI.testModel({
        provider: formData.provider,
        api_key: formData.api_key || undefined,
        profile_id: formData.id || undefined,
        base_url: formData.base_url,
        model: formData.model,
      });
      const message = res?.data?.message ? `\n${String(res.data.message)}` : '';
      showFeedback('success', t('llmModal.testSuccess') + message);
    } catch (error) {
      logger.error('Failed to test model', error);
      showFeedback('error', t('llmModal.testFailed', { message: extractErrorDetail(error) }));
    } finally {
      setTestingModel(false);
    }
  };

  /**
   * 合并预设模型列表与用户已保存的模型 ID。
   * 解决问题：用户通过"获取模型"选了一个不在预设列表中的模型并保存后，
   * 再次打开编辑时 <select> 找不到匹配的 option 导致显示空白。
   * Merge preset models with the user's persisted model to ensure <select> always shows it.
   */
  const mergeWithCurrent = (presetModels) => {
    if (!formData.model) return presetModels;
    const ids = new Set(presetModels.map((m) => m.id || m));
    if (ids.has(formData.model)) return presetModels;
    return [{ id: formData.model, label: formData.model }, ...presetModels];
  };

  const handleSave = () => {
    const payload = { ...formData };
    delete payload.has_api_key;
    delete payload.api_key_mask;
    // 空字符串转 null，后端 Optional[int] 需要 null 而非空串
    if (!payload.max_context_tokens) {
      payload.max_context_tokens = null;
    } else {
      payload.max_context_tokens = parseInt(payload.max_context_tokens, 10) || null;
    }
    onSave(payload);
  };

  const handleDelete = async () => {
    const profileId = formData.id || profile?.id;
    if (!profileId) return;

    if (!window.confirm(t('llmModal.deleteConfirm'))) return;

    try {
      setDeleting(true);
      if (onDelete) {
        await onDelete(profileId);
      } else {
        await configAPI.deleteProfile(profileId);
      }
      onClose();
    } catch (e) {
      showFeedback('error', t('llmModal.deleteFailed'));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2 }}
      className="anti-theme fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/20"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.9, opacity: 0, y: 20 }}
        animate={{ scale: 1, opacity: 1, y: 0 }}
        exit={{ scale: 0.9, opacity: 0, y: 20 }}
        transition={{ type: 'spring', damping: 25, stiffness: 300 }}
        onClick={(e) => e.stopPropagation()}
        className="bg-[var(--vscode-bg)] text-[var(--vscode-fg)] w-full max-w-2xl rounded-[6px] border border-[var(--vscode-sidebar-border)] flex flex-col max-h-[90vh] shadow-none"
      >
        {/* 头部 */}
        <div className="flex items-center justify-between p-6 border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)]">
          <h2 className="text-xl font-bold text-[var(--vscode-fg)] flex items-center gap-2">
            {profile ? t('llmModal.editTitle') : t('llmModal.createTitle')}
          </h2>
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            className="text-[var(--vscode-fg)] border border-[var(--vscode-input-border)] hover:bg-[var(--vscode-list-hover)] shadow-none"
          >
            <X size={20} />
          </Button>
        </div>

        {/* 内容 */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* 基础信息 */}
          <div className="grid grid-cols-2 gap-4">
            <motion.div
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.1 }}
              className="space-y-2"
            >
              <label className="text-xs font-semibold text-[var(--vscode-fg-subtle)] uppercase">
                {t('llmModal.nameLabel')}
              </label>
              <Input
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder={t('llmModal.namePlaceholder')}
                className="bg-[var(--vscode-input-bg)] border-[var(--vscode-input-border)] text-[var(--vscode-fg)] focus-visible:border-[var(--vscode-focus-border)] focus-visible:ring-[var(--vscode-focus-border)]"
              />
            </motion.div>
            <motion.div
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.15 }}
              className="space-y-2"
            >
              <label className="text-xs font-semibold text-[var(--vscode-fg-subtle)] uppercase">
                {t('llmModal.providerLabel')}
              </label>
              <select
                value={formData.provider}
                onChange={(e) => {
                  const newProvider = e.target.value;
                  let defaultModel = '';
                  if (newProvider === 'openai') defaultModel = 'gpt-5.4-mini';
                  if (newProvider === 'anthropic') defaultModel = 'claude-sonnet-4-6';
                  if (newProvider === 'deepseek') defaultModel = 'deepseek-chat';
                  if (newProvider === 'gemini') defaultModel = 'gemini-3.5-flash';
                  if (newProvider === 'grok') defaultModel = 'grok-4';
                  if (newProvider === 'kimi') defaultModel = 'kimi-k2.5';
                  if (newProvider === 'glm') defaultModel = 'glm-5';
                  if (newProvider === 'qwen') defaultModel = 'qwen3.5-plus';
                  if (newProvider === 'wenxin') defaultModel = 'ernie-4.5-turbo-32k';
                  if (newProvider === 'aistudio') defaultModel = 'ernie-5.0-thinking-preview';
                  const defaultBaseUrl = getDefaultBaseUrl(newProvider);

                  setFormData({
                    ...formData,
                    provider: newProvider,
                    model: defaultModel,
                    base_url: defaultBaseUrl,
                    deployed_models: [],
                  });
                  setCustomModelInput(false);
                  setFetchedModels([]);
                }}
                className="w-full px-3 py-2 border border-[var(--vscode-input-border)] rounded-[6px] bg-[var(--vscode-input-bg)] text-[var(--vscode-fg)] focus:outline-none focus:border-[var(--vscode-focus-border)] transition-none"
              >
                {PROVIDERS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </motion.div>
          </div>

          {/* 动态字段 */}
          <div className="space-y-4 pt-4 border-t border-[var(--vscode-sidebar-border)]">
            {actionFeedback ? (
              <div
                className={[
                  'rounded-[6px] border px-3 py-2 text-sm whitespace-pre-wrap',
                  actionFeedback.type === 'error'
                    ? 'border-red-500/40 bg-red-500/10 text-red-300'
                    : actionFeedback.type === 'success'
                      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                      : 'border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] text-[var(--vscode-fg)]',
                ].join(' ')}
              >
                {actionFeedback.message}
              </div>
            ) : null}
            {/* 接口密钥 */}
            <AnimatePresence>
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.2 }}
                className="space-y-2"
              >
                <label className="text-xs font-semibold text-[var(--vscode-fg-subtle)] uppercase">
                  {t('llmModal.apiKeyLabel')}
                </label>
                <Input
                  type="password"
                  value={formData.api_key}
                  onChange={(e) => setFormData({ ...formData, api_key: e.target.value, clear_api_key: false })}
                  placeholder={formData.has_api_key ? formData.api_key_mask : 'sk-...'}
                  disabled={formData.clear_api_key}
                  className="bg-[var(--vscode-input-bg)] border-[var(--vscode-input-border)] text-[var(--vscode-fg)] focus-visible:border-[var(--vscode-focus-border)] focus-visible:ring-[var(--vscode-focus-border)]"
                />
                <p className="text-xs text-[var(--vscode-fg-subtle)]">{t('llmModal.apiKeyNote')}</p>
                {formData.has_api_key ? (
                  <label className="flex items-center gap-2 text-xs text-[var(--vscode-fg-subtle)]">
                    <input
                      type="checkbox"
                      checked={formData.clear_api_key}
                      onChange={(e) => setFormData({ ...formData, clear_api_key: e.target.checked, api_key: '' })}
                    />
                    {t('llmModal.clearApiKey')}
                  </label>
                ) : null}
              </motion.div>
            </AnimatePresence>

            {/* 接口地址（仅自定义） */}
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="space-y-2"
            >
              <label className="text-xs font-semibold text-[var(--vscode-fg-subtle)] uppercase">
                {t('llmModal.baseUrlLabel')}
              </label>
              <Input
                value={formData.base_url}
                onChange={(e) => setFormData({ ...formData, base_url: e.target.value })}
                placeholder="https://api.example.com/v1"
                className="bg-[var(--vscode-input-bg)] border-[var(--vscode-input-border)] text-[var(--vscode-fg)] focus-visible:border-[var(--vscode-focus-border)] focus-visible:ring-[var(--vscode-focus-border)]"
              />
            </motion.div>

            {/* 模型选择 */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.2 }}
              className="space-y-2"
            >
              <div className="flex items-center justify-between">
                <label className="text-xs font-semibold text-[var(--vscode-fg-subtle)] uppercase">
                  {t('llmModal.modelLabel')}
                </label>
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-[10px] text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] border border-[var(--vscode-input-border)] shadow-none"
                    onClick={handleTestModel}
                    disabled={testingModel || !canTestModel}
                  >
                    {testingModel ? <RefreshCw size={10} className="animate-spin mr-1" /> : null}
                    {testingModel ? t('llmModal.testingModel') : t('llmModal.testModel')}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-[10px] text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] border border-[var(--vscode-input-border)] shadow-none"
                    onClick={handleFetchModels}
                    disabled={
                      fetchingModels ||
                      !(formData.api_key || (formData.id && formData.has_api_key && !formData.clear_api_key)) ||
                      !formData.base_url
                    }
                  >
                    {fetchingModels ? (
                      <RefreshCw size={10} className="animate-spin mr-1" />
                    ) : (
                      <RefreshCw size={10} className="mr-1" />
                    )}
                    {fetchingModels ? t('llmModal.fetching') : t('llmModal.fetchModels')}
                  </Button>
                </div>
              </div>
              {(() => {
                const PRESET_MAP = {
                  openai: OPENAI_MODELS,
                  anthropic: ANTHROPIC_MODELS,
                  deepseek: DEEPSEEK_MODELS,
                  gemini: GEMINI_MODELS,
                  grok: GROK_MODELS,
                  kimi: KIMI_MODELS,
                  glm: GLM_MODELS,
                  qwen: QWEN_MODELS,
                  wenxin: WENXIN_MODELS,
                  aistudio: AISTUDIO_MODELS,
                };
                const presetModels = PRESET_MAP[formData.provider];
                const selectCls =
                  'w-full px-3 py-2 border border-[var(--vscode-input-border)] rounded-[6px] bg-[var(--vscode-input-bg)] text-[var(--vscode-fg)] focus:outline-none focus:border-[var(--vscode-focus-border)] transition-none';
                const inputCls =
                  'bg-[var(--vscode-input-bg)] border-[var(--vscode-input-border)] text-[var(--vscode-fg)] focus-visible:border-[var(--vscode-focus-border)] focus-visible:ring-[var(--vscode-focus-border)]';

                if (!presetModels || customModelInput) {
                  return (
                    <div className="flex gap-2">
                      <Input
                        value={formData.model}
                        onChange={(e) => setFormData({ ...formData, model: e.target.value })}
                        placeholder={t('llmModal.customModelPlaceholder')}
                        className={inputCls + ' flex-1'}
                      />
                      {presetModels && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-9 px-2 text-xs text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] border border-[var(--vscode-input-border)] shadow-none whitespace-nowrap"
                          onClick={() => {
                            setCustomModelInput(false);
                          }}
                        >
                          {t('llmModal.backToList')}
                        </Button>
                      )}
                    </div>
                  );
                }

                const models = fetchedModels.length > 0 ? fetchedModels : presetModels;
                return (
                  <select
                    value={formData.model}
                    onChange={(e) => {
                      if (e.target.value === '__custom__') {
                        setCustomModelInput(true);
                        setFormData({ ...formData, model: '' });
                      } else {
                        setFormData({ ...formData, model: e.target.value });
                      }
                    }}
                    className={selectCls}
                  >
                    <option value="">{t('llmModal.modelPlaceholder')}</option>
                    {mergeWithCurrent(models).map((m) => (
                      <option key={m.id || m} value={m.id || m}>
                        {m.label || m}
                      </option>
                    ))}
                    <option value="__custom__">{t('llmModal.customModelOption')}</option>
                  </select>
                );
              })()}
              {fetchWarning ? (
                <div className="text-[11px] leading-snug text-[var(--vscode-fg-subtle)]">{fetchWarning}</div>
              ) : null}
            </motion.div>

            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.25 }}
              className="grid grid-cols-2 gap-4"
            >
              <div className="space-y-2">
                <label className="text-xs font-semibold text-[var(--vscode-fg-subtle)] uppercase">
                  {t('llmModal.temperatureLabel', { value: formData.temperature })}
                </label>
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.1"
                  value={formData.temperature}
                  onChange={(e) => setFormData({ ...formData, temperature: parseFloat(e.target.value) })}
                  className="w-full accent-[var(--vscode-focus-border)]"
                />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-semibold text-[var(--vscode-fg-subtle)] uppercase">
                  {t('llmModal.maxContextTokensLabel')}
                </label>
                <Input
                  type="number"
                  value={formData.max_context_tokens}
                  onChange={(e) => setFormData({ ...formData, max_context_tokens: e.target.value })}
                  placeholder={t('llmModal.maxContextTokensPlaceholder')}
                  min="0"
                  step="1000"
                  className="bg-[var(--vscode-input-bg)] border-[var(--vscode-input-border)] text-[var(--vscode-fg)] focus-visible:border-[var(--vscode-focus-border)] focus-visible:ring-[var(--vscode-focus-border)]"
                />
                <p className="text-xs text-[var(--vscode-fg-subtle)]">{t('llmModal.maxContextTokensNote')}</p>
              </div>
            </motion.div>
          </div>
        </div>

        {/* 底部操作 */}
        <div className="p-6 border-t border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)] flex items-center justify-between gap-3">
          {formData.id || profile?.id ? (
            <Button
              variant="ghost"
              onClick={handleDelete}
              disabled={deleting}
              className="border border-[var(--vscode-input-border)] text-red-400 hover:bg-[var(--vscode-list-hover)] shadow-none"
            >
              <Trash2 size={16} className="mr-2" />
              {t('llmModal.deleteBtn')}
            </Button>
          ) : (
            <span />
          )}

          <div className="flex justify-end gap-3">
            <Button
              variant="ghost"
              onClick={onClose}
              disabled={deleting}
              className="border border-[var(--vscode-input-border)] text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] shadow-none"
            >
              {t('llmModal.cancelBtn')}
            </Button>
            <Button
              onClick={handleSave}
              disabled={deleting}
              className="bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] hover:opacity-90 shadow-none"
            >
              <Save size={16} className="mr-2" />
              {t('llmModal.saveBtn')}
            </Button>
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}

import React, { useState, useEffect } from 'react';
import { cardsAPI } from '../../api';
import { Button } from '../ui/Button';
import { Card, CardHeader, CardTitle, CardContent } from '../ui/Card';
import { FileText, Save, RefreshCw } from 'lucide-react';

export function StyleView({ projectId }) {
  const [loading, setLoading] = useState(false);
  const [formData, setFormData] = useState({
    narrative_distance: '',
    pacing: '',
    sentence_structure: '',
    vocabulary_constraints: [],
    example_passages: []
  });

  useEffect(() => {
    loadStyle();
  }, [projectId]);

  const loadStyle = async () => {
    setLoading(true);
    try {
      const response = await cardsAPI.getStyle(projectId);
      if (response.data) {
        setFormData(response.data);
      }
    } catch (error) {
      console.error('Failed to load style:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      await cardsAPI.updateStyle(projectId, formData);
      alert('文风设定更新成功');
    } catch (error) {
      alert('更新风格失败: ' + (error.response?.data?.detail || error.message));
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 h-[calc(100vh-140px)]">
      <div className="lg:col-span-8 lg:col-start-3 flex flex-col gap-6">
        <Card className="flex-1 flex flex-col overflow-hidden">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle><FileText size={18} /> 文风设定</CardTitle>
            <Button variant="ghost" size="sm" onClick={loadStyle} disabled={loading}>
              <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
            </Button>
          </CardHeader>
          <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
            <form id="style-form" onSubmit={handleSubmit} className="space-y-8">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="space-y-2">
                  <label className="text-xs font-mono text-muted-foreground uppercase">叙事距离</label>
                  <input
                    type="text"
                    value={formData.narrative_distance || ''}
                    onChange={(e) => setFormData({ ...formData, narrative_distance: e.target.value })}
                    className="w-full bg-black/50 border border-border rounded px-3 py-2 text-white focus:border-primary focus:outline-none"
                    placeholder="例如: 贴近第三人称, 疑离视角"
                  />
                  <p className="text-xs text-muted-foreground">叙述者与主角的距离感</p>
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-mono text-muted-foreground uppercase">节奏</label>
                  <input
                    type="text"
                    value={formData.pacing || ''}
                    onChange={(e) => setFormData({ ...formData, pacing: e.target.value })}
                    className="w-full bg-black/50 border border-border rounded px-3 py-2 text-white focus:border-primary focus:outline-none"
                    placeholder="例如: 快节奏, 动作向, 慢热型"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-xs font-mono text-muted-foreground uppercase">句式结构</label>
                <textarea
                  value={formData.sentence_structure || ''}
                  onChange={(e) => setFormData({ ...formData, sentence_structure: e.target.value })}
                  className="w-full bg-black/50 border border-border rounded px-3 py-2 text-white focus:border-primary focus:outline-none h-24"
                  placeholder="描述期望的句子复杂度、长度和节奏..."
                />
              </div>

              <div className="space-y-2">
                <label className="text-xs font-mono text-muted-foreground uppercase">词汇约束 (逗号分隔)</label>
                <input
                  type="text"
                  value={Array.isArray(formData.vocabulary_constraints) ? formData.vocabulary_constraints.join(', ') : ''}
                  onChange={(e) => setFormData({ ...formData, vocabulary_constraints: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
                  className="w-full bg-black/50 border border-border rounded px-3 py-2 text-white focus:border-primary focus:outline-none"
                  placeholder="例如: 禁用现代俗语, 多用专业术语"
                />
              </div>

               <div className="space-y-2">
                <label className="text-xs font-mono text-muted-foreground uppercase">示例段落</label>
                 <div className="text-xs text-muted-foreground mb-2">添加示例以引导 AI 的语调</div>
                 {/* Simplified array editor for examples could go here, for now just a placeholder or single text area if simplified */}
                 <textarea
                    value={Array.isArray(formData.example_passages) ? formData.example_passages.join('\n---\n') : ''}
                    onChange={(e) => setFormData({ ...formData, example_passages: e.target.value.split('\n---\n').filter(Boolean) })}
                    className="w-full bg-black/50 border border-border rounded px-3 py-2 text-white font-serif text-sm focus:border-primary focus:outline-none h-40"
                    placeholder="粘贴示例文本。多个示例请用 '---' 分隔。"
                  />
              </div>
            </form>
          </div>
          <div className="p-4 border-t border-border bg-black/20 flex justify-end">
            <Button form="style-form" type="submit" disabled={loading} className="w-full md:w-auto">
              <Save size={16} className="mr-2" /> 保存文风设定
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}

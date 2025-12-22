import React, { useState, useEffect } from 'react';
import { cardsAPI } from '../../api';
import { Button } from '../ui/Button';
import { Card, CardHeader, CardTitle } from '../ui/Card';
import { Plus, Globe, Save, X, Box } from 'lucide-react';

export function WorldView({ projectId }) {
  const [worldCards, setWorldCards] = useState([]);
  const [editing, setEditing] = useState(null);
  const [formData, setFormData] = useState({
    name: '',
    category: '',
    description: '',
    rules: [],
    immutable: false
  });

  useEffect(() => {
    loadWorldCards();
  }, [projectId]);

  useEffect(() => {
    if (editing) {
      if (editing.name) {
        setFormData(editing);
      } else {
        // New card
        setFormData({
          name: '',
          category: '',
          description: '',
          rules: [],
          immutable: false
        });
      }
    }
  }, [editing]);

  const loadWorldCards = async () => {
    try {
      const namesResp = await cardsAPI.listWorld(projectId);
      const items = [];
      for (const name of namesResp.data) {
        const resp = await cardsAPI.getWorld(projectId, name);
        items.push(resp.data);
      }
      setWorldCards(items);
    } catch (error) {
      console.error('Failed to load world cards:', error);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      if (editing.name && editing.name !== '') {
        await cardsAPI.updateWorld(projectId, editing.name, formData);
      } else {
        await cardsAPI.createWorld(projectId, formData);
      }
      await loadWorldCards();
      setEditing(null);
    } catch (error) {
      alert('Error saving world card: ' + (error.response?.data?.detail || error.message));
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 h-[calc(100vh-140px)]">
      {/* Left List */}
      <div className="lg:col-span-4 flex flex-col gap-4 overflow-hidden">
        <div className="flex justify-between items-center">
          <h3 className="text-lg font-bold text-white">世界观设定</h3>
          <Button size="sm" onClick={() => setEditing({})}>
            <Plus size={16} className="mr-2" /> 新建条目
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto space-y-3 pr-2 custom-scrollbar">
          {worldCards.length === 0 && (
             <div className="text-sm text-muted-foreground p-2 font-mono">暂无条目</div>
          )}
          {worldCards.map((card) => (
            <div 
              key={card.name} 
              onClick={() => setEditing(card)}
              className={`p-4 rounded-lg border cursor-pointer transition-all ${
                editing?.name === card.name 
                  ? 'bg-primary/10 border-primary text-white' 
                  : 'bg-card border-border text-muted-foreground hover:border-primary/50 hover:text-white'
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-bold font-mono">{card.name}</span>
                <Globe size={14} className="opacity-50" />
              </div>
              <div className="text-xs opacity-70 flex items-center gap-2">
                <span className="uppercase tracking-wider text-[10px] bg-white/10 px-1.5 py-0.5 rounded">{card.category}</span>
              </div>
              <div className="text-xs mt-2 line-clamp-2 opacity-60 font-mono">
                {card.description}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Right Editor */}
      <div className="lg:col-span-8 bg-card border border-border rounded-lg overflow-hidden flex flex-col">
        {editing ? (
          <div className="flex-1 flex flex-col">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle><Box size={18} /> {editing.name ? `编辑: ${editing.name}` : '新建世界条目'}</CardTitle>
              <div className="flex gap-2">
                 <Button variant="ghost" size="sm" onClick={() => setEditing(null)}>
                   <X size={16} />
                 </Button>
              </div>
            </CardHeader>
            <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
              <form id="world-form" onSubmit={handleSubmit} className="space-y-6">
                <div className="grid grid-cols-2 gap-6">
                  <div className="space-y-2">
                    <label className="text-xs font-mono text-muted-foreground uppercase">名称</label>
                    <input
                      type="text"
                      value={formData.name}
                      onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                      className="w-full bg-black/50 border border-border rounded px-3 py-2 text-white focus:border-primary focus:outline-none"
                      placeholder="例如: 铁城"
                      required
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-xs font-mono text-muted-foreground uppercase">类别</label>
                    <input
                      type="text"
                      value={formData.category}
                      onChange={(e) => setFormData({ ...formData, category: e.target.value })}
                      className="w-full bg-black/50 border border-border rounded px-3 py-2 text-white focus:border-primary focus:outline-none"
                      placeholder="例如: 地点, 势力, 法则"
                      required
                    />
                  </div>
                </div>
                
                <div className="space-y-2">
                  <label className="text-xs font-mono text-muted-foreground uppercase">描述</label>
                  <textarea
                    value={formData.description}
                    onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                    className="w-full bg-black/50 border border-border rounded px-3 py-2 text-white focus:border-primary focus:outline-none min-h-[100px]"
                    placeholder="详细描述..."
                    required
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-xs font-mono text-muted-foreground uppercase">规则/属性 (逗号分隔)</label>
                  <input
                    type="text"
                    value={Array.isArray(formData.rules) ? formData.rules.join(', ') : ''}
                    onChange={(e) => setFormData({ ...formData, rules: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
                    className="w-full bg-black/50 border border-border rounded px-3 py-2 text-white focus:border-primary focus:outline-none"
                    placeholder="例如: 低重力, 高犯罪率"
                  />
                </div>

                <div className="flex items-center gap-2 pt-2">
                  <input
                    type="checkbox"
                    id="immutable"
                    checked={formData.immutable}
                    onChange={(e) => setFormData({ ...formData, immutable: e.target.checked })}
                    className="rounded border-border bg-black/50 text-primary focus:ring-primary"
                  />
                  <label htmlFor="immutable" className="text-sm font-mono text-muted-foreground">不可变 (核心设定)</label>
                </div>
              </form>
            </div>
            <div className="p-4 border-t border-border bg-black/20 flex justify-end gap-3">
              <Button variant="ghost" onClick={() => setEditing(null)}>取消</Button>
              <Button form="world-form" type="submit">
                <Save size={16} className="mr-2" /> 保存条目
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground opacity-50">
            <Globe size={48} className="mb-4" />
            <div className="font-mono">选择或创建世界条目</div>
          </div>
        )}
      </div>
    </div>
  );
}

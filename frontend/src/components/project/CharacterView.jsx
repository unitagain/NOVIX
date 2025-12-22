import React, { useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '../ui/Card';
import { Button } from '../ui/Button';
import { Plus, Edit2, User, Globe, Save, X } from 'lucide-react';

export function CharacterView({ characters, onEdit, onSave, editing, onCancel }) {
  const [formData, setFormData] = useState({
    name: '',
    identity: '',
    motivation: '',
    personality: [],
    speech_pattern: '',
    relationships: [],
    boundaries: [],
    arc: ''
  });

  React.useEffect(() => {
    if (editing) {
      setFormData(editing);
    } else {
       // Reset form when not editing
       setFormData({
        name: '',
        identity: '',
        motivation: '',
        personality: [],
        speech_pattern: '',
        relationships: [],
        boundaries: [],
        arc: ''
      });
    }
  }, [editing]);

  const handleSubmit = (e) => {
    e.preventDefault();
    const payload = {
      ...formData,
      identity: (formData.identity || '').trim() || '未填写',
      motivation: (formData.motivation || '').trim() || '未填写',
    };
    onSave(payload);
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 h-[calc(100vh-140px)]">
      {/* Left List */}
      <div className="lg:col-span-4 flex flex-col gap-4 overflow-hidden">
        <div className="flex justify-between items-center">
          <h3 className="text-lg font-bold text-white">角色列表</h3>
          <Button size="sm" onClick={() => onEdit({})}>
            <Plus size={16} className="mr-2" /> 新建
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto space-y-3 pr-2 custom-scrollbar">
          {characters.map((char) => (
            <div 
              key={char.name} 
              onClick={() => onEdit(char)}
              className={`p-4 rounded-lg border cursor-pointer transition-all ${
                editing?.name === char.name 
                  ? 'bg-primary/10 border-primary text-white' 
                  : 'bg-card border-border text-muted-foreground hover:border-primary/50 hover:text-white'
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-bold font-mono">{char.name}</span>
                <User size={14} className="opacity-50" />
              </div>
              <div className="text-xs opacity-70 line-clamp-2">{char.identity}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Right Editor */}
      <div className="lg:col-span-8 bg-card border border-border rounded-lg overflow-hidden flex flex-col">
        {editing ? (
          <div className="flex-1 flex flex-col">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>{editing.name ? `编辑: ${editing.name}` : '新建角色'}</CardTitle>
              <div className="flex gap-2">
                 <Button variant="ghost" size="sm" onClick={onCancel}>
                   <X size={16} />
                 </Button>
              </div>
            </CardHeader>
            <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
              <form id="char-form" onSubmit={handleSubmit} className="space-y-6">
                <div className="grid grid-cols-2 gap-6">
                  <div className="space-y-2">
                    <label className="text-xs font-mono text-muted-foreground uppercase">名称</label>
                    <input
                      type="text"
                      value={formData.name}
                      onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                      className="w-full bg-black/50 border border-border rounded px-3 py-2 text-white focus:border-primary focus:outline-none"
                      required
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-xs font-mono text-muted-foreground uppercase">身份</label>
                    <input
                      type="text"
                      value={formData.identity}
                      onChange={(e) => setFormData({ ...formData, identity: e.target.value })}
                      className="w-full bg-black/50 border border-border rounded px-3 py-2 text-white focus:border-primary focus:outline-none"
                    />
                  </div>
                </div>
                
                <div className="space-y-2">
                  <label className="text-xs font-mono text-muted-foreground uppercase">动机</label>
                  <input
                    type="text"
                    value={formData.motivation}
                    onChange={(e) => setFormData({ ...formData, motivation: e.target.value })}
                    className="w-full bg-black/50 border border-border rounded px-3 py-2 text-white focus:border-primary focus:outline-none"
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-xs font-mono text-muted-foreground uppercase">性格特征 (逗号分隔)</label>
                  <input
                    type="text"
                    value={Array.isArray(formData.personality) ? formData.personality.join(', ') : ''}
                    onChange={(e) => setFormData({ ...formData, personality: e.target.value.split(',').map(s => s.trim()) })}
                    className="w-full bg-black/50 border border-border rounded px-3 py-2 text-white focus:border-primary focus:outline-none"
                  />
                </div>
                
                {/* Add more fields as needed */}
              </form>
            </div>
            <div className="p-4 border-t border-border bg-black/20 flex justify-end gap-3">
              <Button variant="ghost" onClick={onCancel}>取消</Button>
              <Button form="char-form" type="submit">
                <Save size={16} className="mr-2" /> 保存角色
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-muted-foreground font-mono">
            选择或创建角色
          </div>
        )}
      </div>
    </div>
  );
}

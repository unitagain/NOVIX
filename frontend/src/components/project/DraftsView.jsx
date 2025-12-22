import React, { useState, useEffect } from 'react';
import { draftsAPI } from '../../api';
import { Button } from '../ui/Button';
import { Card, CardHeader, CardTitle, CardContent } from '../ui/Card';
import { FileText, Trash2, Eye, EyeOff, BookOpen, Clock } from 'lucide-react';

export function DraftsView({ projectId }) {
  const [chapters, setChapters] = useState([]);
  const [selectedChapter, setSelectedChapter] = useState('');
  const [versions, setVersions] = useState([]);
  const [selectedVersion, setSelectedVersion] = useState('');
  const [draftContent, setDraftContent] = useState('');
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadChapters();
  }, [projectId]);

  useEffect(() => {
    if (selectedChapter) loadChapterData();
  }, [selectedChapter]);

  useEffect(() => {
    if (selectedChapter && selectedVersion) loadDraftContent();
  }, [selectedChapter, selectedVersion]);

  const loadChapters = async () => {
    try {
      const resp = await draftsAPI.listChapters(projectId);
      setChapters(Array.isArray(resp.data) ? resp.data : []);
    } catch (e) {
      console.error(e);
    }
  };

  const loadChapterData = async () => {
    try {
      const vResp = await draftsAPI.listVersions(projectId, selectedChapter);
      const vItems = Array.isArray(vResp.data) ? vResp.data : [];
      setVersions(vItems);
      setSelectedVersion(vItems[vItems.length - 1] || '');

      try {
        const sResp = await draftsAPI.getSummary(projectId, selectedChapter);
        setSummary(sResp?.data || null);
      } catch {
        setSummary(null);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const loadDraftContent = async () => {
    setLoading(true);
    try {
      const dResp = await draftsAPI.getDraft(projectId, selectedChapter, selectedVersion);
      setDraftContent(dResp?.data?.content || '');
    } catch (e) {
      setDraftContent('Error loading content');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 h-[calc(100vh-140px)]">
      {/* Sidebar List */}
      <div className="lg:col-span-3 flex flex-col gap-4 overflow-hidden">
        <h3 className="text-lg font-bold text-white px-1">内容管理</h3>
        <div className="flex-1 overflow-y-auto space-y-2 pr-2 custom-scrollbar">
          {chapters.length === 0 && <div className="text-sm text-muted-foreground p-2">暂无章节</div>}
          {chapters.map((ch) => (
            <div
              key={ch}
              onClick={() => setSelectedChapter(ch)}
              className={`p-3 rounded-md border cursor-pointer transition-colors font-mono text-sm ${
                selectedChapter === ch
                  ? 'bg-primary/10 border-primary text-primary'
                  : 'bg-card border-border text-muted-foreground hover:text-white hover:border-white/20'
              }`}
            >
              {ch}
            </div>
          ))}
        </div>
      </div>

      {/* Main Content */}
      <div className="lg:col-span-9 flex flex-col gap-6 overflow-hidden">
        {!selectedChapter ? (
           <div className="flex-1 flex items-center justify-center text-muted-foreground border border-dashed border-border rounded-lg">
             选择章节查看详情
           </div>
        ) : (
          <>
            {/* Top Meta */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
               <Card className="md:col-span-2">
                 <CardHeader className="py-3">
                   <CardTitle className="text-sm"><BookOpen size={14} /> 摘要</CardTitle>
                 </CardHeader>
                 <CardContent className="py-4">
                   {summary ? (
                     <div className="space-y-2">
                       <div className="font-bold text-white">{summary.title}</div>
                       <div className="text-sm text-muted-foreground line-clamp-2">{summary.brief_summary}</div>
                     </div>
                   ) : (
                     <span className="text-xs text-muted-foreground">暂无摘要</span>
                   )}
                 </CardContent>
               </Card>
               
               <Card>
                 <CardHeader className="py-3">
                   <CardTitle className="text-sm"><Clock size={14} /> 版本</CardTitle>
                 </CardHeader>
                 <CardContent className="py-4 space-y-3">
                   <select 
                     className="w-full bg-black/50 border border-border rounded p-2 text-sm text-white focus:outline-none focus:border-primary"
                     value={selectedVersion}
                     onChange={(e) => setSelectedVersion(e.target.value)}
                   >
                     {versions.map(v => <option key={v} value={v}>{v}</option>)}
                   </select>
                   <Button variant="destructive" size="sm" className="w-full text-xs">
                     <Trash2 size={12} className="mr-2" /> 删除章节
                   </Button>
                 </CardContent>
               </Card>
            </div>

            {/* Content Viewer */}
            <Card className="flex-1 overflow-hidden flex flex-col">
              <CardHeader className="py-3 flex flex-row justify-between items-center">
                <CardTitle className="text-sm">内容预览: {selectedVersion}</CardTitle>
                <div className="text-xs font-mono text-muted-foreground">
                  {loading ? '加载中...' : `${draftContent.length} 字符`}
                </div>
              </CardHeader>
              <div className="flex-1 overflow-y-auto p-6 custom-scrollbar bg-black/20">
                 <pre className="whitespace-pre-wrap font-sans text-gray-300 leading-relaxed max-w-none">
                   {draftContent}
                 </pre>
              </div>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}

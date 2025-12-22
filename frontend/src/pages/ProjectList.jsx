import { useState, useEffect } from 'react';
import { projectsAPI } from '../api';
import { Button } from '../components/ui/Button';
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card';
import { Plus, FolderOpen, Clock, ChevronRight, Terminal, Command } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

function ProjectList({ onSelectProject }) {
  const navigate = useNavigate();
  const [projects, setProjects] = useState([]);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newProject, setNewProject] = useState({ name: '', description: '' });
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadProjects();
  }, []);

  const loadProjects = async () => {
    try {
      const response = await projectsAPI.list();
      setProjects(response.data);
    } catch (error) {
      console.error('Failed to load projects:', error);
    }
  };

  const handleCreate = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const response = await projectsAPI.create(newProject);
      await loadProjects();
      setShowCreateForm(false);
      setNewProject({ name: '', description: '' });
      if (onSelectProject) {
        onSelectProject(response.data);
      } else {
        navigate(`/project/${response.data.id}`);
      }
    } catch (error) {
      alert('Failed: ' + (error.response?.data?.detail || error.message));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-full p-8 max-w-7xl mx-auto flex flex-col gap-8">
      <div className="flex justify-between items-end border-b border-white/10 pb-6">
        <div>
          <div className="flex items-center gap-3 text-primary mb-2">
            <Terminal size={24} />
            <span className="text-xs font-mono uppercase tracking-[0.2em] opacity-70">Novix 系统</span>
          </div>
          <h2 className="text-4xl font-bold text-white tracking-tight">项目索引</h2>
          <p className="text-muted-foreground mt-2 font-mono text-sm">选择项目以初始化工作区</p>
        </div>
        <Button
          onClick={() => setShowCreateForm(true)}
          className="font-mono text-xs"
        >
          <Plus size={16} className="mr-2" />
          新建项目
        </Button>
      </div>

      {/* Create Form */}
      {showCreateForm && (
        <Card className="border-primary/50 relative overflow-hidden animate-in fade-in slide-in-from-top-4 duration-300">
          <div className="absolute top-0 left-0 w-1 h-full bg-primary" />
          <CardHeader>
            <CardTitle className="text-primary">
              <Command size={18} /> 初始化新项目
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleCreate} className="space-y-4 max-w-lg">
              <div className="space-y-1">
                <label className="text-xs font-mono text-muted-foreground uppercase">项目名称</label>
                <input
                  type="text"
                  value={newProject.name}
                  onChange={(e) => setNewProject({ ...newProject, name: e.target.value })}
                  className="w-full px-4 py-2 bg-black/50 border border-border rounded-md focus:outline-none focus:border-primary text-white font-mono placeholder:text-gray-700"
                  placeholder="例如: 赛博朋克舞者"
                  required
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs font-mono text-muted-foreground uppercase">项目描述</label>
                <textarea
                  value={newProject.description}
                  onChange={(e) => setNewProject({ ...newProject, description: e.target.value })}
                  className="w-full px-4 py-2 bg-black/50 border border-border rounded-md focus:outline-none focus:border-primary text-white font-sans placeholder:text-gray-700 resize-none"
                  rows="3"
                  placeholder="简要项目概述..."
                />
              </div>
              <div className="flex space-x-3 pt-2">
                <Button type="submit" isLoading={loading} className="font-bold">
                  初始化
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setShowCreateForm(false)}
                >
                  取消
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {/* Projects Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {projects.length === 0 ? (
          <div className="col-span-full flex flex-col items-center justify-center py-24 border border-dashed border-border rounded-lg bg-card/10">
            <FolderOpen className="h-12 w-12 text-muted-foreground mb-4 opacity-50" />
            <p className="text-muted-foreground font-mono">暂无项目</p>
            <Button variant="ghost" onClick={() => setShowCreateForm(true)} className="mt-2 text-primary hover:text-primary">
              创建第一个项目
            </Button>
          </div>
        ) : (
          projects.map((project) => (
            <Card
              key={project.id}
              onClick={() => onSelectProject ? onSelectProject(project) : navigate(`/project/${project.id}`)}
              className="group cursor-pointer hover:border-primary/50 transition-all duration-300 hover:shadow-[0_0_20px_rgba(0,255,148,0.05)]"
            >
              <CardContent className="p-6 h-full flex flex-col relative">
                 <div className="absolute top-4 right-4 opacity-0 group-hover:opacity-100 transition-opacity -translate-x-2 group-hover:translate-x-0 duration-300">
                  <ChevronRight className="text-primary" />
                </div>
                
                <h3 className="text-xl font-bold text-white mb-2 group-hover:text-primary transition-colors pr-6">
                  {project.name}
                </h3>
                <p className="text-sm text-muted-foreground mb-6 line-clamp-2 flex-1">
                  {project.description || '暂无描述'}
                </p>
                <div className="flex items-center text-xs text-zinc-500 font-mono mt-auto pt-4 border-t border-white/5">
                  <Clock size={12} className="mr-2" />
                  {new Date(project.created_at).toLocaleDateString('zh-CN')}
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>
    </div>
  );
}

export default ProjectList;

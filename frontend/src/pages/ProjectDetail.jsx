import React, { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { projectsAPI, cardsAPI } from '../api';
import { DashboardView } from '../components/project/DashboardView';
import { CharacterView } from '../components/project/CharacterView';
import { WritingView } from '../components/project/WritingView';
import { DraftsView } from '../components/project/DraftsView';
import { WorldView } from '../components/project/WorldView';
import { StyleView } from '../components/project/StyleView';
import { Button } from '../components/ui/Button';
import { 
  LayoutDashboard, 
  Users, 
  BookOpen, 
  PenTool, 
  Globe, 
  FileText,
  ArrowLeft
} from 'lucide-react';
import { cn } from '../lib/utils';

function ProjectDetail() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState('dashboard');
  const [project, setProject] = useState(null);

  const tabLabels = {
    dashboard: '仪表盘',
    writing: '写作会话',
    characters: '角色',
    world: '世界观',
    style: '文风设定',
    drafts: '档案库'
  };
  
  // Data States
  const [dashboard, setDashboard] = useState(null);
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardError, setDashboardError] = useState('');
  
  const [characters, setCharacters] = useState([]);
  const [editingCharacter, setEditingCharacter] = useState(null);

  useEffect(() => {
    loadProject();
  }, [projectId]);

  useEffect(() => {
    if (activeTab === 'dashboard') loadDashboard();
    if (activeTab === 'characters') loadCharacters();
  }, [projectId, activeTab]);

  const loadProject = async () => {
    try {
      const response = await projectsAPI.get(projectId);
      setProject(response.data);
    } catch (error) {
      console.error('Failed to load project:', error);
    }
  };

  const loadDashboard = async () => {
    setDashboardLoading(true);
    try {
      const response = await projectsAPI.getDashboard(projectId);
      setDashboard(response.data);
    } catch (error) {
      setDashboardError(error.message);
    } finally {
      setDashboardLoading(false);
    }
  };

  const loadCharacters = async () => {
    try {
      const names = await cardsAPI.listCharacters(projectId);
      const chars = [];
      for (const name of names.data) {
        const char = await cardsAPI.getCharacter(projectId, name);
        chars.push(char.data);
      }
      setCharacters(chars);
    } catch (error) {
      console.error('Failed to load characters:', error);
    }
  };

  const saveCharacter = async (character) => {
    try {
      if (editingCharacter?.name && editingCharacter.name !== '') {
        await cardsAPI.updateCharacter(projectId, editingCharacter.name, character);
      } else {
        await cardsAPI.createCharacter(projectId, character);
      }
      await loadCharacters();
      setEditingCharacter(null);
    } catch (error) {
      alert('Failed: ' + error.message);
    }
  };

  if (!project) return <div className="p-8 text-white font-mono animate-pulse">系统初始化中...</div>;

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Secondary Sidebar (Project Nav) */}
      <div className="w-56 bg-card/50 border-r border-border flex flex-col backdrop-blur-sm">
        <div className="p-6 border-b border-border">
          <div className="text-sm font-semibold text-primary mb-1 tracking-wide">项目工作区</div>
          <h2 className="text-xl font-bold text-white truncate" title={project.name}>{project.name}</h2>
          <p className="text-sm text-muted-foreground mt-1 line-clamp-2">{project.description}</p>
        </div>
        
        <div className="p-4 space-y-1 overflow-y-auto flex-1 custom-scrollbar">
          <div className="text-sm font-mono text-muted-foreground px-3 mb-2 uppercase tracking-wider opacity-70">控制台</div>
          <NavButton 
            active={activeTab === 'dashboard'} 
            onClick={() => setActiveTab('dashboard')} 
            icon={<LayoutDashboard size={18} />} 
            label="仪表盘" 
          />
          <NavButton 
            active={activeTab === 'writing'} 
            onClick={() => setActiveTab('writing')} 
            icon={<PenTool size={18} />} 
            label="写作会话" 
            highlight
          />
          
          <div className="text-sm font-mono text-muted-foreground px-3 mt-6 mb-2 uppercase tracking-wider opacity-70">数据库</div>
          <NavButton 
            active={activeTab === 'characters'} 
            onClick={() => setActiveTab('characters')} 
            icon={<Users size={18} />} 
            label="角色" 
          />
          <NavButton 
            active={activeTab === 'world'} 
            onClick={() => setActiveTab('world')} 
            icon={<Globe size={18} />} 
            label="世界观" 
          />
          <NavButton 
            active={activeTab === 'style'} 
            onClick={() => setActiveTab('style')} 
            icon={<FileText size={18} />} 
            label="文风设定" 
          />

           <div className="text-sm font-mono text-muted-foreground px-3 mt-6 mb-2 uppercase tracking-wider opacity-70">存储</div>
           <NavButton 
            active={activeTab === 'drafts'} 
            onClick={() => setActiveTab('drafts')} 
            icon={<BookOpen size={18} />} 
            label="档案库" 
          />
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 bg-background overflow-hidden flex flex-col relative">
         {/* Background Grid */}
         <div className="absolute inset-0 bg-grid-pattern opacity-[0.03] pointer-events-none" />

         {/* Top Bar */}
         <div className="h-14 border-b border-border flex items-center justify-between px-6 bg-card/20 backdrop-blur-sm z-10">
            <div className="flex items-center gap-3 text-sm font-semibold">
               <Button
                 variant="ghost"
                 size="sm"
                 onClick={() => navigate('/')}
                 className="px-2"
               >
                 <ArrowLeft size={16} className="mr-2" /> 返回
               </Button>
               <span className="text-muted-foreground font-medium">{project.name}</span>
               <span className="text-border">/</span>
               <span className="text-primary font-semibold">{tabLabels[activeTab] || activeTab}</span>
            </div>
            <div className="flex items-center gap-3">
               <div className="h-2 w-2 rounded-full bg-primary animate-pulse-slow"></div>
               <span className="text-sm text-muted-foreground font-medium">系统就绪</span>
            </div>
         </div>

         {/* Viewport */}
         <div className="flex-1 overflow-y-auto p-6 relative z-0 custom-scrollbar">
            {activeTab === 'dashboard' && (
              <DashboardView 
                dashboard={dashboard} 
                loading={dashboardLoading} 
                error={dashboardError}
                onRefresh={loadDashboard}
              />
            )}
            {activeTab === 'characters' && (
              <CharacterView 
                characters={characters}
                editing={editingCharacter}
                onEdit={setEditingCharacter}
                onSave={saveCharacter}
                onCancel={() => setEditingCharacter(null)}
              />
            )}
            
            {activeTab === 'writing' && (
              <WritingView projectId={projectId} />
            )}

            {activeTab === 'drafts' && (
              <DraftsView projectId={projectId} />
            )}
            
            {activeTab === 'world' && (
              <WorldView projectId={projectId} />
            )}

            {activeTab === 'style' && (
              <StyleView projectId={projectId} />
            )}
         </div>
      </div>
    </div>
  );
}

function NavButton({ active, onClick, icon, label, highlight }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-sm font-medium duration-200 group relative overflow-hidden",
        active 
          ? "bg-primary/10 text-primary border border-primary/20" 
          : "text-muted-foreground hover:bg-white/5 hover:text-white",
        highlight && !active && "text-primary hover:bg-primary/5 border border-primary/10"
      )}
    >
      {active && <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-primary" />}
      <span className={cn("transition-transform duration-300", active ? "translate-x-1" : "")}>{icon}</span>
      <span className={cn("transition-transform duration-300", active ? "translate-x-1" : "")}>{label}</span>
    </button>
  );
}

export default ProjectDetail;

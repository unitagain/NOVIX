import React, { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { 
  Layout, 
  BookOpen, 
  PenTool, 
  Settings, 
  ChevronLeft, 
  ChevronRight,
  Cpu
} from 'lucide-react';
import { cn } from '../../lib/utils';
import { Logo } from '../ui/Logo';

export function AppShell({ children }) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const location = useLocation();

  return (
    <div className="min-h-screen bg-background text-foreground flex overflow-hidden font-sans">
      {/* Sidebar */}
      <aside 
        className={cn(
          "relative border-r border-border bg-card/30 backdrop-blur-sm transition-all duration-300 ease-in-out flex flex-col",
          sidebarCollapsed ? "w-16" : "w-56"
        )}
      >
        {/* Logo Area */}
        <div className="h-16 flex items-center px-4 border-b border-border">
          <Logo size={sidebarCollapsed ? 'small' : 'default'} showText={!sidebarCollapsed} />
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-6 px-2 space-y-2">
          <NavItem 
            to="/" 
            icon={<Layout size={20} />} 
            label="项目" 
            collapsed={sidebarCollapsed}
            active={location.pathname === '/' || location.pathname.startsWith('/project')}
          />
          <div className="my-4 border-t border-border/50 mx-2" />
          <NavItem 
            to="/agents" 
            icon={<Cpu size={20} />} 
            label="智能体" 
            collapsed={sidebarCollapsed}
            active={location.pathname.startsWith('/agents')}
          />
          <NavItem 
            to="/system" 
            icon={<Settings size={20} />} 
            label="系统" 
            collapsed={sidebarCollapsed}
            active={location.pathname.startsWith('/system')}
          />
        </nav>

        {/* Collapse Toggle */}
        <button
          onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          className="absolute -right-3 top-20 bg-card border border-border rounded-full p-1 text-muted-foreground hover:text-primary transition-colors"
        >
          {sidebarCollapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>

        {/* Footer Status */}
        <div className="p-4 border-t border-border bg-card/50">
          <div className="flex items-center gap-3 overflow-hidden">
            <div className="h-2 w-2 rounded-full bg-primary animate-pulse-slow flex-shrink-0" />
            <span className={cn(
              "text-sm text-muted-foreground font-mono transition-opacity duration-300 whitespace-nowrap",
              sidebarCollapsed ? "opacity-0 w-0" : "opacity-100"
            )}>
              系统在线
            </span>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden bg-background relative">
        <div className="absolute inset-0 bg-grid-pattern opacity-[0.02] pointer-events-none" />
        {children}
      </main>
    </div>
  );
}

function NavItem({ to, icon, label, collapsed, active }) {
  return (
    <Link
      to={to}
      className={cn(
        "flex items-center gap-3 px-3 py-2 rounded-md transition-all duration-200 group",
        active 
          ? "bg-primary/10 text-primary border border-primary/20" 
          : "text-muted-foreground hover:text-foreground hover:bg-white/5"
      )}
      title={collapsed ? label : undefined}
    >
      <div className={cn(
        "flex-shrink-0 transition-colors duration-200",
        active ? "text-primary" : "group-hover:text-foreground"
      )}>
        {icon}
      </div>
      <span className={cn(
        "font-medium whitespace-nowrap transition-all duration-300 overflow-hidden",
        collapsed ? "w-0 opacity-0" : "w-auto opacity-100"
      )}>
        {label}
      </span>
    </Link>
  );
}

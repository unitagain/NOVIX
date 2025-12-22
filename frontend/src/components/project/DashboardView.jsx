import React from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '../ui/Card';
import { Activity, Users, BookOpen, AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from '../ui/Button';

export function DashboardView({ dashboard, loading, error, onRefresh }) {
  const stats = dashboard?.stats || {};
  const chapters = dashboard?.chapters || [];
  const recentFacts = dashboard?.recent?.facts || [];
  const recentEvents = dashboard?.recent?.timeline_events || [];

  if (loading) {
    return <div className="text-sm text-muted-foreground p-8">加载系统指标中...</div>;
  }

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white">系统状态</h2>
          <p className="text-muted-foreground text-sm">概览与指标</p>
        </div>
        <Button variant="outline" size="sm" onClick={onRefresh}>
          <RefreshCw className="mr-2 h-4 w-4" />
          刷新
        </Button>
      </div>

      {error && (
        <div className="p-4 rounded border border-destructive/50 bg-destructive/10 text-destructive text-sm font-mono">
          错误: {error}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard 
          icon={<BookOpen className="text-primary" />}
          label="总字数"
          value={stats.total_word_count ?? '-'}
          subValue={`已完成: ${stats.completed_chapters || 0}`}
        />
        <StatCard 
          icon={<Users className="text-blue-400" />}
          label="角色数"
          value={stats.character_count ?? '-'}
        />
        <StatCard 
          icon={<Activity className="text-yellow-400" />}
          label="实体数"
          value={(stats.fact_count || 0) + (stats.timeline_event_count || 0) + (stats.character_state_count || 0)}
          subValue="事实 / 事件 / 状态"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle><BookOpen size={18} /> 章节状态</CardTitle>
          </CardHeader>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="bg-white/5 text-muted-foreground font-mono text-sm uppercase">
                <tr>
                  <th className="px-6 py-3">章节</th>
                  <th className="px-6 py-3">字数</th>
                  <th className="px-6 py-3">摘要</th>
                  <th className="px-6 py-3">冲突</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {chapters.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-6 py-8 text-center text-muted-foreground font-mono">
                      暂无数据
                    </td>
                  </tr>
                ) : (
                  chapters.map((ch) => (
                    <tr key={ch.chapter} className="hover:bg-white/5 transition-colors">
                      <td className="px-6 py-4 font-medium text-white font-mono">{ch.chapter}</td>
                      <td className="px-6 py-4 text-muted-foreground font-mono">
                        {ch.has_final ? ch.final_word_count : '-'}
                      </td>
                      <td className="px-6 py-4">
                        {ch.has_summary ? (
                          <div className="max-w-[200px]">
                            <div className="font-medium text-white truncate">{ch.summary_title || ch.chapter}</div>
                          </div>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </td>
                      <td className="px-6 py-4">
                        {(ch.conflict_count || 0) > 0 ? (
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-bold bg-destructive/20 text-destructive border border-destructive/30">
                            {ch.conflict_count} ERR
                          </span>
                        ) : (
                          <span className="text-green-500/50 text-xs font-mono">OK</span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle><Activity size={18} /> 近期事实</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {recentFacts.length === 0 ? (
                <div className="text-sm text-muted-foreground font-mono">暂无数据</div>
              ) : (
                recentFacts.map((f, idx) => (
                  <div key={idx} className="text-sm border-l-2 border-primary/30 pl-3 py-1">
                    <div className="font-mono text-sm text-primary mb-1">{f.id}</div>
                    <div className="text-muted-foreground text-sm line-clamp-2">{f.statement}</div>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function StatCard({ icon, label, value, subValue }) {
  return (
    <div className="bg-card border border-border p-6 rounded-lg relative overflow-hidden group hover:border-primary/50 transition-colors">
      <div className="absolute top-0 right-0 p-4 opacity-50 group-hover:opacity-100 transition-opacity">
        {icon}
      </div>
      <div className="text-sm font-mono text-muted-foreground uppercase tracking-wider mb-2">{label}</div>
      <div className="text-3xl font-bold text-white font-mono">{value}</div>
      {subValue && <div className="text-sm text-muted-foreground mt-2">{subValue}</div>}
    </div>
  );
}

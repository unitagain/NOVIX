/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   角色关系图谱画布 - 拖动角色卡摆位、在两卡之间连线并标注「关系 / 称呼」。
 *   Character relation canvas: arrange character cards and draw labelled relations.
 *
 * 语义约定（与后端一致，不可含糊）：
 *   边 from -> to 读作「from 是 to 的 {relation}」；appellation 是「to 对 from 的称呼」，
 *   reverseAppellation 是「from 对 to 的称呼」。两个称呼各自独立填写，不自动互推。
 *   关系边是**作者设定**，与角色卡同层；Canon 中从正文抽取的关系事实是另一层资产。
 *
 * 本组件经 React.lazy 懒加载（独立 chunk），不打开关系图时主包零成本。
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  getBezierPath,
  useEdgesState,
  useNodesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { cardsAPI } from '../../api';
import { useLocale } from '../../i18n';
import logger from '../../utils/logger';
import { cn } from '../ui/core';

const SAVE_DEBOUNCE_MS = 500;
const MAX_EDGES = 500; // 与后端 storage/character_relations.py 的上限保持一致
const MAX_LABEL_CHARS = 20;
const GRID_COLUMNS = 4;

/** 无坐标的新角色按网格落位，避免全部堆在原点。 */
const gridPosition = (index) => ({
  x: 40 + (index % GRID_COLUMNS) * 220,
  y: 60 + Math.floor(index / GRID_COLUMNS) * 120,
});

const newEdgeId = () => Math.random().toString(16).slice(2, 10);

const normalizeStars = (value) => {
  const parsed = Number.parseInt(value, 10);
  if (Number.isNaN(parsed)) return 1;
  return Math.max(1, Math.min(parsed, 3));
};

/** 边标签点击 → 打开编辑表单；用 context 传递避免把回调塞进 edge.data 造成重建。 */
const EdgeEditContext = createContext(() => {});

function CharacterNode({ data }) {
  return (
    <div className="rounded-[8px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)] px-3 py-2 text-[var(--vscode-fg)] shadow-sm">
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-none !bg-[var(--vscode-fg-subtle)]" />
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium">{data.label}</span>
        <span className="text-[10px] opacity-60">{'★'.repeat(normalizeStars(data.stars))}</span>
      </div>
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-none !bg-[var(--vscode-fg-subtle)]" />
    </div>
  );
}

function RelationEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd }) {
  const onEdit = useContext(EdgeEditContext);
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });
  const label = [data?.relation, data?.appellation, data?.reverseAppellation].filter(Boolean).join(' · ');

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={{ stroke: 'var(--vscode-fg-subtle)' }} />
      <EdgeLabelRenderer>
        <button
          type="button"
          onClick={() => onEdit(id)}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`, pointerEvents: 'all' }}
          className="nodrag nopan absolute rounded-[4px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-bg)] px-1.5 py-0.5 text-[10px] text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)]"
        >
          {label}
        </button>
      </EdgeLabelRenderer>
    </>
  );
}

const NODE_TYPES = { character: CharacterNode };
const EDGE_TYPES = { relation: RelationEdge };
const EDGE_MARKER = { type: MarkerType.ArrowClosed, width: 16, height: 16, color: 'var(--vscode-fg-subtle)' };

const toFlowEdge = (edge) => ({
  id: edge.id || newEdgeId(),
  source: edge.from,
  target: edge.to,
  type: 'relation',
  markerEnd: EDGE_MARKER,
  data: {
    relation: edge.relation || '',
    appellation: edge.appellation || '',
    reverseAppellation: edge.reverse_appellation || '',
  },
});

const buildDocument = (nodes, edges) => ({
  edges: edges.map((edge) => ({
    id: edge.id,
    from: edge.source,
    to: edge.target,
    relation: edge.data?.relation || '',
    ...(edge.data?.appellation ? { appellation: edge.data.appellation } : {}),
    ...(edge.data?.reverseAppellation ? { reverse_appellation: edge.data.reverseAppellation } : {}),
  })),
  layout: Object.fromEntries(
    nodes.map((node) => [node.id, { x: Math.round(node.position.x), y: Math.round(node.position.y) }]),
  ),
});

export default function RelationGraphView({ projectId }) {
  const { t } = useLocale();
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [loading, setLoading] = useState(true);
  const [saveState, setSaveState] = useState('idle');
  const [editing, setEditing] = useState(null);
  const [formError, setFormError] = useState('');
  const [revision, setRevision] = useState(0);

  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  const savingRef = useRef(false);
  const rerunRef = useRef(false);

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);
  useEffect(() => {
    edgesRef.current = edges;
  }, [edges]);

  // 打开时读一次；单用户桌面场景不做轮询。
  useEffect(() => {
    if (!projectId) return undefined;
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const [charactersResp, documentResp] = await Promise.all([
          cardsAPI.listCharactersIndex(projectId),
          cardsAPI.getRelations(projectId),
        ]);
        if (cancelled) return;
        const characters = Array.isArray(charactersResp.data) ? charactersResp.data : [];
        const layout = documentResp.data?.layout || {};
        const savedEdges = Array.isArray(documentResp.data?.edges) ? documentResp.data.edges : [];
        const known = new Set(characters.map((card) => card.name));
        setNodes(
          characters
            .filter((card) => card?.name)
            .map((card, index) => ({
              id: card.name,
              type: 'character',
              position: layout[card.name] || gridPosition(index),
              data: { label: card.name, stars: card.stars },
              deletable: false,
            })),
        );
        // 端点已不存在的边不渲染（后端删除级联后不会出现，这里只做防御）。
        setEdges(savedEdges.filter((edge) => known.has(edge.from) && known.has(edge.to)).map(toFlowEdge));
      } catch (error) {
        logger.error('Failed to load relation graph', error);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [projectId, setEdges, setNodes]);

  const markDirty = useCallback(() => setRevision((value) => value + 1), []);

  const persist = useCallback(async () => {
    if (savingRef.current) {
      rerunRef.current = true;
      return;
    }
    savingRef.current = true;
    setSaveState('saving');
    try {
      await cardsAPI.saveRelations(projectId, buildDocument(nodesRef.current, edgesRef.current));
      setSaveState('saved');
    } catch (error) {
      // 保存失败保留本地状态，下一次变更会重试；不静默丢弃用户操作。
      logger.error('Failed to save relation graph', error);
      setSaveState('error');
    } finally {
      savingRef.current = false;
      if (rerunRef.current) {
        rerunRef.current = false;
        markDirty();
      }
    }
  }, [markDirty, projectId]);

  useEffect(() => {
    if (revision === 0) return undefined; // 初次加载不触发保存
    const timer = setTimeout(() => {
      void persist();
    }, SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [persist, revision]);

  const openEdgeEditor = useCallback((edgeId) => {
    const edge = edgesRef.current.find((item) => item.id === edgeId);
    if (!edge) return;
    setFormError('');
    setEditing({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      relation: edge.data?.relation || '',
      appellation: edge.data?.appellation || '',
      reverseAppellation: edge.data?.reverseAppellation || '',
    });
  }, []);

  const handleConnect = useCallback(
    ({ source, target }) => {
      if (!source || !target) return;
      if (source === target) {
        setFormError(t('relationGraph.selfLoop'));
        return;
      }
      if (edgesRef.current.length >= MAX_EDGES) {
        setFormError(t('relationGraph.limitReached').replace('{max}', String(MAX_EDGES)));
        return;
      }
      setFormError('');
      setEditing({ id: '', source, target, relation: '', appellation: '', reverseAppellation: '' });
    },
    [t],
  );

  const handleSubmitEdge = useCallback(() => {
    if (!editing) return;
    const relation = editing.relation.trim();
    const appellation = editing.appellation.trim();
    const reverseAppellation = editing.reverseAppellation.trim();
    if (!relation) {
      setFormError(t('relationGraph.relationRequired'));
      return;
    }
    const duplicated = edgesRef.current.some(
      (edge) =>
        edge.id !== editing.id &&
        edge.source === editing.source &&
        edge.target === editing.target &&
        (edge.data?.relation || '') === relation,
    );
    if (duplicated) {
      setFormError(t('relationGraph.duplicate'));
      return;
    }
    setEdges((current) => {
      if (editing.id) {
        return current.map((edge) =>
          edge.id === editing.id ? { ...edge, data: { relation, appellation, reverseAppellation } } : edge,
        );
      }
      return [
        ...current,
        toFlowEdge({
          from: editing.source,
          to: editing.target,
          relation,
          appellation,
          reverse_appellation: reverseAppellation,
        }),
      ];
    });
    setEditing(null);
    setFormError('');
    markDirty();
  }, [editing, markDirty, setEdges, t]);

  const handleDeleteEdge = useCallback(() => {
    if (!editing?.id) return;
    setEdges((current) => current.filter((edge) => edge.id !== editing.id));
    setEditing(null);
    markDirty();
  }, [editing, markDirty, setEdges]);

  const statusText = useMemo(() => {
    if (saveState === 'saving') return t('relationGraph.saving');
    if (saveState === 'saved') return t('relationGraph.saved');
    if (saveState === 'error') return t('relationGraph.saveFailed');
    return '';
  }, [saveState, t]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-[var(--vscode-fg-subtle)]">
        {t('relationGraph.loading')}
      </div>
    );
  }

  if (!nodes.length) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-xs text-[var(--vscode-fg-subtle)]">
        {t('relationGraph.empty')}
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      <EdgeEditContext.Provider value={openEdgeEditor}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          edgeTypes={EDGE_TYPES}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeDragStop={markDirty}
          onEdgesDelete={markDirty}
          onEdgeClick={(_event, edge) => openEdgeEditor(edge.id)}
          onConnect={handleConnect}
          defaultEdgeOptions={{ type: 'relation', markerEnd: EDGE_MARKER }}
          deleteKeyCode={['Backspace', 'Delete']}
          fitView
          proOptions={{ hideAttribution: false }}
        >
          <Background gap={18} size={1} color="var(--vscode-sidebar-border)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </EdgeEditContext.Provider>

      <div className="pointer-events-none absolute left-3 top-3 flex items-center gap-3 text-[10px] text-[var(--vscode-fg-subtle)]">
        <span>{t('relationGraph.hint')}</span>
        {statusText && (
          <span className={cn(saveState === 'error' && 'text-red-500')}>{statusText}</span>
        )}
      </div>

      {(editing || formError) && (
        <div className="absolute right-4 top-4 w-[260px] rounded-[8px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-bg)] p-3 shadow-lg">
          {editing ? (
            <>
              <div className="mb-1 text-xs font-medium text-[var(--vscode-fg)]">{t('relationGraph.edgeTitle')}</div>
              <div className="mb-2 text-[10px] text-[var(--vscode-fg-subtle)]">
                {t('relationGraph.directionHint').replace('{from}', editing.source).replace('{to}', editing.target)}
              </div>
              <label className="ui-caption text-[var(--vscode-fg-subtle)]">{t('relationGraph.relationLabel')}</label>
              <input
                value={editing.relation}
                maxLength={MAX_LABEL_CHARS}
                autoFocus
                onChange={(event) => setEditing((prev) => ({ ...prev, relation: event.target.value }))}
                placeholder={t('relationGraph.relationPlaceholder')}
                className="mb-2 w-full rounded-[6px] border border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] px-2 py-1 text-xs text-[var(--vscode-fg)] focus:border-[var(--vscode-focus-border)]"
              />
              <label className="ui-caption text-[var(--vscode-fg-subtle)]">{t('relationGraph.appellationLabel')}</label>
              <div className="mb-1 text-[10px] text-[var(--vscode-fg-subtle)]">
                {t('relationGraph.appellationHint').replace('{from}', editing.source).replace('{to}', editing.target)}
              </div>
              <input
                value={editing.appellation}
                maxLength={MAX_LABEL_CHARS}
                onChange={(event) => setEditing((prev) => ({ ...prev, appellation: event.target.value }))}
                placeholder={t('relationGraph.appellationPlaceholder')}
                className="mb-2 w-full rounded-[6px] border border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] px-2 py-1 text-xs text-[var(--vscode-fg)] focus:border-[var(--vscode-focus-border)]"
              />
              <label className="ui-caption text-[var(--vscode-fg-subtle)]">
                {t('relationGraph.reverseAppellationLabel')}
              </label>
              <div className="mb-1 text-[10px] text-[var(--vscode-fg-subtle)]">
                {t('relationGraph.reverseAppellationHint')
                  .replace('{from}', editing.source)
                  .replace('{to}', editing.target)}
              </div>
              <input
                value={editing.reverseAppellation}
                maxLength={MAX_LABEL_CHARS}
                onChange={(event) => setEditing((prev) => ({ ...prev, reverseAppellation: event.target.value }))}
                placeholder={t('relationGraph.reverseAppellationPlaceholder')}
                className="mb-2 w-full rounded-[6px] border border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] px-2 py-1 text-xs text-[var(--vscode-fg)] focus:border-[var(--vscode-focus-border)]"
              />
              {formError && <div className="mb-2 text-[10px] text-red-500">{formError}</div>}
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleSubmitEdge}
                  className="rounded-[4px] bg-[var(--vscode-list-active)] px-2 py-1 text-[10px] text-[var(--vscode-list-active-fg)]"
                >
                  {t('relationGraph.save')}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setEditing(null);
                    setFormError('');
                  }}
                  className="rounded-[4px] border border-[var(--vscode-input-border)] px-2 py-1 text-[10px] text-[var(--vscode-fg)]"
                >
                  {t('relationGraph.cancel')}
                </button>
                {editing.id && (
                  <button
                    type="button"
                    onClick={handleDeleteEdge}
                    className="ml-auto text-[10px] text-red-500 hover:underline"
                  >
                    {t('relationGraph.deleteEdge')}
                  </button>
                )}
              </div>
            </>
          ) : (
            <div className="flex items-start gap-2 text-[10px] text-red-500">
              <span className="flex-1">{formError}</span>
              <button type="button" onClick={() => setFormError('')} className="text-[var(--vscode-fg-subtle)]">
                {t('relationGraph.cancel')}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

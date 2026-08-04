/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   前端 TypeScript 类型定义，定义所有 API 数据模型和业务逻辑类型。
 *   与后端 Pydantic 模型对应。
 */

/**
 * 项目 / Project
 *
 * 用户创建的小说项目基本信息。
 */
export interface Project {
  id: string; // 项目唯一标识
  name: string; // 项目名称
  description?: string; // 项目描述
  language?: 'zh' | 'en'; // 写作语言（中文/英文）
  created_at: string; // 创建时间（ISO 8601）
  updated_at: string; // 更新时间（ISO 8601）
}

/**
 * 角色卡片 / Character Card
 *
 * 小说中的角色信息卡片，包含基本属性和自定义字段。
 */
export interface CharacterCard {
  name: string; // 角色名称
  description: string; // 角色描述
  aliases?: string[]; // 别名列表
  stars?: number; // 重要度评分（0-5星）
  identity?: string; // 身份/职位
  appearance?: string; // 外貌描述
  voice?: string; // 角色口吻样本（示例对白，仅描述说话方式，非故事事实）
}

/**
 * SillyTavern 卡片导入结果 / Tavern card import result
 */
export interface TavernImportPlan {
  source_format: string; // 识别到的源格式（chara_card_v1/v2/v3, lorebook_v3, world_info）
  source_filename: string;
  characters: CharacterCard[];
  world_cards: WorldCard[];
  dropped: { field: string; reason: string; count: number }[]; // 被有意剥离的字段清单
  warnings: string[];
  injection_detected: boolean; // 存留文本命中注入启发式（仅提示）
}

export interface TavernImportResponse {
  committed: boolean; // false = dry-run 预案，未落盘
  permission: string;
  plan: TavernImportPlan;
  created_characters: string[];
  created_world_cards: string[];
  skipped_existing: string[];
}

/**
 * 世界观卡片 / World Card
 *
 * 小说世界的设定信息，如地点、时代、组织等。
 */
export interface WorldCard {
  name: string; // 设定名称
  description: string; // 详细描述
  category?: string; // 分类（地点、组织、时代等）
  immutable?: boolean; // 是否不可修改
  rules?: string[]; // 相关规则列表
}

/**
 * 文风卡片 / Style Card
 *
 * 小说的整体文风和语言风格设定。
 */
export interface StyleCard {
  style: string; // 文风描述
}

/**
 * 角色关系边 / Character relation edge
 *
 * 作者设定层的人物关系（与角色卡同层，不是 Canon 中从正文抽取的关系事实）。
 * 方向语义：from 是 to 的 relation；appellation 是 to 对 from 的称呼，
 * reverse_appellation 是 from 对 to 的称呼（两者独立填写，不自动互推）。
 */
export interface RelationEdge {
  id?: string; // 稳定边 ID（缺省由服务端生成）
  from: string; // 被描述的角色（关系的主语）
  to: string; // 视角所属角色（关系的宾语）
  relation: string; // 关系（如 姐姐），≤20 字符
  appellation?: string | null; // to 对 from 的称呼（如 阿姐），≤20 字符
  reverse_appellation?: string | null; // from 对 to 的称呼（如 小河），≤20 字符
}

/**
 * 角色关系文档 / Character relation document
 *
 * cards/relations.yaml 的全量视图：设定边 + 画布坐标（坐标为纯视图状态）。
 */
export interface RelationsDocument {
  edges: RelationEdge[];
  layout: Record<string, { x: number; y: number }>;
}

/**
 * 草稿 / Draft
 *
 * 章节的草稿版本，包含内容和审核状态。
 */
export interface Draft {
  chapter: string; // 章节标识
  version: string; // 版本号
  content: string; // 文本内容
  word_count: number; // 字数统计
  pending_confirmations?: string[]; // 待确认的修改
  created_at: string; // 创建时间
}

/**
 * 章节摘要 / Chapter Summary
 *
 * 章节的结构化总结，包含关键事件、新设定、角色变化等。
 */
export interface ChapterSummary {
  chapter: string; // 章节标识
  title: string; // 章节标题
  word_count: number; // 字数统计
  key_events: string[]; // 关键事件列表
  new_facts: string[]; // 新增设定列表
  character_state_changes: string[]; // 角色变化列表
  open_loops: string[]; // 未解决的悬念列表
  brief_summary: string; // 简要摘要
}

/**
 * 卷 / Volume
 *
 * 小说的大型章节分组，用于组织结构化内容。
 */
export interface Volume {
  id: string; // 卷的唯一标识
  project_id: string; // 所属项目 ID
  title: string; // 卷的标题
  summary?: string; // 卷的摘要
  order: number; // 在项目中的顺序
  created_at: string; // 创建时间
  updated_at: string; // 更新时间
}

/**
 * 事实 / Fact
 *
 * 动态事实表中的单个事实项，记录小说中的已确立设定。
 */
export interface Fact {
  id: string; // 事实唯一标识
  statement: string; // 事实陈述
  source: string; // 信息来源（哪个章节、卡片等）
  introduced_in: string; // 首次引入位置
  confidence: number; // 置信度（0-1）
}

/**
 * LLM 配置文件 / LLM Profile
 *
 * LLM 提供商的配置信息，支持 OpenAI、Anthropic、DeepSeek 等。
 */
export interface LLMProfile {
  id: string; // 配置文件 ID
  provider: string; // 提供商名称
  api_key?: string; // 仅写入；后端不会回传明文
  has_api_key?: boolean;
  api_key_mask?: string;
  clear_api_key?: boolean;
  base_url?: string; // 自定义 Base URL
  deployed_models?: string[]; // Persisted deployed-model snapshot
  model: string; // 模型名称
  max_tokens?: number; // 最大输出 Token 数
  max_context_tokens?: number; // 上下文窗口大小（手动覆盖自动推断）
  temperature?: number; // 温度参数（0-2）
}

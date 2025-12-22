<p align="center">
  <svg width="360" height="96" viewBox="0 0 360 96" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="NOVIX">
    <defs>
      <linearGradient id="novixGradient" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0%" stop-color="#6EE7B7" />
        <stop offset="50%" stop-color="#34D399" />
        <stop offset="100%" stop-color="#22C55E" />
      </linearGradient>
    </defs>
    <text
      x="50%"
      y="58%"
      text-anchor="middle"
      dominant-baseline="middle"
      font-family="Arial Rounded MT Bold, Nunito, Poppins, Inter, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif"
      font-size="64"
      font-weight="800"
      letter-spacing="-4"
      fill="url(#novixGradient)"
    >
      NOVIX
    </text>
  </svg>
</p>

# NOVIX

NOVIX 是一个开源的 **多智能体（Multi-Agent）+ 上下文工程（Context Engineering）** 小说写作系统，用结构化知识与协作流水线支撑稳定、可追溯的长篇创作。

## 快速开始（Windows）

1. 启动后端（新开一个终端）：

```bash
cd .\NOVIX-main\backend
./run.bat
```

2. 启动前端（再开一个终端）：

```bash
cd .\NOVIX-main\frontend
./run.bat
```

3. 打开：

- 前端：`http://localhost:3000`
- 后端：`http://localhost:8000`
- API 文档：`http://localhost:8000/docs`

首次启动后端会自动创建 `.env`（由 `.env.example` 复制）。你可以填入 API Key，或使用 `mock` provider 进行演示。

## 为什么是 NOVIX（我们的优势）

- **多智能体写作流水线**
  - 四个角色分工协作：资料管理员（Archivist）/ 撰稿人（Writer）/ 审稿人（Reviewer）/ 编辑（Editor）
  - 通过调度器（Orchestrator）串联成可复用的写作工作流

- **上下文工程优先（不是“堆提示词”）**
  - 卡片系统：角色、世界观、规则、文风等可持续维护的结构化资产
  - Canon（事实/时间线/状态）让长篇一致性可管理、可审计
  - 上下文选择、压缩、隔离与预算控制，让每次调用都“带对信息、带够信息”

- **反馈闭环：每轮产出都可迭代**
  - 支持用户对草稿提出反馈并驱动下一轮修订

- **文件化存储，天然 Git 友好**
  - 核心资产以 YAML/JSON/Markdown 形式落盘
  - 可 diff、可回滚、可协作

- **LLM 网关与按角色覆盖配置**
  - 对接多家模型供应商，并支持默认配置 + 角色覆盖（例如 Writer 用 Claude，Reviewer 用 OpenAI）

## 技术栈

### 前端

- React + Vite
- TailwindCSS
- React Router
- Axios

### 后端

- FastAPI
- Pydantic
- WebSocket（写作会话实时推送）
- python-dotenv / PyYAML

### 存储

- 文件系统（YAML/JSON/Markdown）
- 面向 Git 的版本管理与协作方式

## 项目结构

```
NOVIX-main/
├── backend/              # FastAPI 后端
│   ├── app/
│   │   ├── agents/       # 多智能体
│   │   ├── context_engine/  # 上下文引擎
│   │   ├── llm_gateway/     # 大模型网关
│   │   ├── orchestrator/    # 调度器
│   │   └── routers/         # API 路由
│   ├── config.yaml
│   └── run.bat
├── frontend/             # React 前端
│   └── run.bat
└── data/                 # 写作项目数据（文件化资产）
```

## 贡献与交流（Contributing）

我们非常欢迎你加入 NOVIX：

- **提 Issue**：Bug、建议、需求、使用体验，都欢迎直接提
- **提 PR**：功能、修复、文档、示例、UI/UX 优化都很有价值
- **讨论设计**：多智能体工作流 / 上下文工程策略 / 长篇一致性治理，都欢迎一起打磨

如果你不确定从哪里开始，直接开一个 Issue 说明你的想法即可，我们会很乐意一起把它变成可落地的改进。

## License

MIT License

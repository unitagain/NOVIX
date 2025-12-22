# NOVIX 快速开始指南

## 1. 环境配置

### 安装 Python 依赖

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 配置 API Keys

```bash
# 复制配置文件
cp .env.example .env

# 编辑 .env 文件，至少配置一个 API Key
```

`.env` 文件示例：
```
OPENAI_API_KEY=sk-your-openai-key
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key
```

## 2. 启动服务器

### Windows
```bash
run.bat
```

### Linux/Mac
```bash
chmod +x run.sh
./run.sh
```

### 手动启动
```bash
cd backend
python -m app.main
```

服务器将在 `http://localhost:8000` 启动

访问 API 文档：`http://localhost:8000/docs`

## 3. 测试基本流程

### 步骤 1：创建项目

```bash
curl -X POST "http://localhost:8000/projects" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "测试小说",
    "description": "这是一个测试项目"
  }'
```

响应示例：
```json
{
  "id": "测试小说",
  "name": "测试小说",
  "description": "这是一个测试项目",
  "created_at": "2024-12-20T22:00:00"
}
```

### 步骤 2：创建角色卡

```bash
curl -X POST "http://localhost:8000/projects/测试小说/cards/characters" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "张三",
    "identity": "25岁程序员",
    "motivation": "完成一个伟大的项目",
    "personality": ["认真", "专注", "有创造力"],
    "speech_pattern": "说话简洁明了",
    "relationships": [],
    "boundaries": ["不会放弃自己的原则"],
    "arc": "从迷茫到找到人生方向"
  }'
```

### 步骤 3：创建文风卡

```bash
curl -X PUT "http://localhost:8000/projects/测试小说/cards/style" \
  -H "Content-Type: application/json" \
  -d '{
    "narrative_distance": "第三人称限知视角",
    "pacing": "节奏紧凑，张弛有度",
    "sentence_structure": "以短句为主，偶尔使用长句增加变化",
    "vocabulary_constraints": ["避免使用网络流行语"],
    "example_passages": ["他停下脚步。前方是一片未知。"]
  }'
```

### 步骤 4：开始写作会话

```bash
curl -X POST "http://localhost:8000/projects/测试小说/session/start" \
  -H "Content-Type: application/json" \
  -d '{
    "chapter": "ch01",
    "chapter_title": "开始",
    "chapter_goal": "介绍主角张三的日常生活和他面临的困境",
    "target_word_count": 2000,
    "character_names": ["张三"]
  }'
```

这个过程会执行：
1. 资料管理员生成场景简报
2. 撰稿人撰写草稿
3. 审稿人审核草稿
4. 编辑修订草稿

完成后会返回所有产出（场景简报、草稿、审稿意见、修订稿）

### 步骤 5：提交反馈（可选）

如果不满意，可以提交反馈：

```bash
curl -X POST "http://localhost:8000/projects/测试小说/session/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "chapter": "ch01",
    "feedback": "节奏需要再快一些，增加一些冲突",
    "action": "revise"
  }'
```

系统会根据反馈重新审核和修订。

### 步骤 6：确认满意

当满意后，确认完成：

```bash
curl -X POST "http://localhost:8000/projects/测试小说/session/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "chapter": "ch01",
    "feedback": "很好，满意",
    "action": "confirm"
  }'
```

系统会保存成稿到 `data/测试小说/drafts/ch01/final.md`

## 4. 查看生成的文件

所有文件保存在 `data/` 目录：

```
data/测试小说/
├── project.yaml          # 项目元数据
├── cards/
│   ├── characters/
│   │   └── 张三.yaml     # 角色卡
│   └── style.yaml        # 文风卡
├── drafts/
│   └── ch01/
│       ├── scene_brief.yaml   # 场景简报
│       ├── draft_v1.md        # 草稿 v1
│       ├── review.yaml        # 审稿意见
│       ├── draft_v2.md        # 修订稿 v2
│       └── final.md           # 成稿
└── traces/                    # 运行日志
```

## 5. WebSocket 实时推送（可选）

如果你想实时监控写作进度，可以连接 WebSocket：

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/测试小说/session');

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Progress:', data);
};
```

## 6. 常见问题

### Q: API 调用失败，返回 500 错误
A: 检查 `.env` 文件中的 API Key 是否正确配置

### Q: 如何使用不同的模型？
A: 修改 `config.yaml` 中的 `agents` 配置，为每个 Agent 指定不同的 provider 和 temperature

### Q: 文件存储在哪里？
A: 所有数据保存在 `../data/` 目录（相对于 backend 目录）

### Q: 如何重置项目？
A: 删除 `data/{project_name}` 目录即可

## 7. 下一步

- 查看 `README.md` 了解完整功能
- 查看 `plan.md` 了解项目架构设计
- 访问 `/docs` 查看完整 API 文档
- 开发前端界面（待实现）

---

**提示**：首次运行会调用 LLM API，请确保网络连接正常且有足够的 API 配额。

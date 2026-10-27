# 脑出血 RAG 平台

本目录按《医学专病知识库 / Agent / 测评平台开发规格说明》初始化为 6 个独立业务项目和 1 个集成部署项目。

| 项目 | 默认端口 | 说明 |
| --- | ---: | --- |
| `medical-knowledge-api` | 8001 | 文献、解析、检索与 Knowledge MCP |
| `medical-knowledge-web` | 5173 | 文献管理与证据审核 |
| `medical-agent-api` | 8002 | 会话、模型、多 MCP、引用与 SSE |
| `medical-agent-web` | 5174 | 对话、运行轨迹与引用展示 |
| `medical-evaluation-api` | 8003 | 题库、批量测评、指标与治理 |
| `medical-evaluation-web` | 5175 | 测评任务与结果可视化 |
| `medical-platform-deploy` | 8080 | 本地集成部署与 Nginx 入口 |

## 快速开始

完整联调：

```powershell
Set-Location .\medical-platform-deploy
Copy-Item .env.example .env
docker compose up --build
```

统一入口为 `http://localhost:8080`，三个应用分别位于 `/knowledge/`、`/agent/`、`/evaluation/`。

单个项目的开发方式见各目录内的 `README.md`。所有后端数据库迁移均通过 Alembic 执行；项目不使用 SQLite 或 `Base.metadata.create_all()` 建表。


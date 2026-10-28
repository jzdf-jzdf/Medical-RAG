# 脑出血 RAG 平台

Knowledge、Agent、Evaluation 三个业务域已收敛为一个 Monorepo。服务名、端口、API、数据库边界和部署拓扑保持不变；代码按业务域与横切能力分层，统一依赖、测试和部署入口。

## 目录结构

```text
Medical-RAG/
├─ apps/                       # 可独立发布的 Vue 前端
│  ├─ knowledge-web/           # 5173 /knowledge/
│  ├─ agent-web/               # 5174 /agent/
│  └─ evaluation-web/          # 5175 /evaluation/
├─ services/                   # 可独立部署的 FastAPI 服务
│  ├─ knowledge-api/           # 8001，文献、分块、索引、检索与 MCP
│  ├─ agent-api/               # 8002，会话、模型、MCP 与引用核验
│  └─ evaluation-api/          # 8003，题库、测试运行与指标
├─ packages/
│  ├─ web/platform-ui/         # 登录、会话与账户 UI；不含业务逻辑
│  └─ python/medical-platform-core/ # 指标等稳定横切基础设施
├─ infra/
│  ├─ docker/                  # 复用的容器构建入口
│  └─ platform/                # Compose、Nginx、Prometheus
├─ scripts/                    # 单仓初始化与全量校验
├─ docs/                       # 架构、运维和集成说明
└─ tests/                      # 跨服务端到端与烟雾测试
```

依赖方向固定为 `apps/services -> packages`。业务域之间不通过源码互相引用：Evaluation 仍通过 HTTP 调用 Knowledge/Agent，Agent 仍通过 MCP 获取 Knowledge 证据，三个后端继续使用各自的 PostgreSQL。

## 快速开始

首次初始化：

```powershell
.\scripts\bootstrap.ps1
```

完整校验：

```powershell
.\scripts\check.ps1
```

集成部署：

```powershell
Copy-Item .\infra\platform\.env.example .\infra\platform\.env
docker compose -f .\infra\platform\compose.yaml up --build
```

统一入口为 `http://localhost:8080`。

## 日常开发

```powershell
# 后端
uv run --project services\knowledge-api uvicorn knowledge_api.main:app --reload --port 8001
uv run --project services\agent-api uvicorn agent_api.main:app --reload --port 8002
uv run --project services\evaluation-api uvicorn evaluation_api.main:app --reload --port 8003

# 前端
npm run dev --workspace medical-knowledge-web
npm run dev --workspace medical-agent-web
npm run dev --workspace medical-evaluation-web
```

后端依赖由根目录 `uv.lock` 管理，前端依赖由根目录 `package-lock.json` 管理。各业务目录保留自己的 `pyproject.toml` 或 `package.json`，因此边界清晰且仍能按服务独立运行。

架构原则、目录映射和历史保留方式见 [Monorepo 架构说明](docs/monorepo-architecture.md)。

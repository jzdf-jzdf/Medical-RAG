# medical-platform-deploy

三前端、三 API 与独立 Worker 的本地集成入口。

```powershell
Copy-Item .env.example .env
docker compose config
docker compose up --build
```

仅启动/更新业务容器：

```powershell
docker compose up -d gateway knowledge-web agent-web evaluation-web knowledge-api agent-api evaluation-api knowledge-worker
```

按需启用 Prometheus（不会默认增加监控容器）：

```powershell
docker compose --profile observability up -d prometheus
```

- 平台入口：`http://localhost:8080`
- Knowledge：`/knowledge/`，API：`/api/knowledge/`
- Knowledge MCP：`http://localhost:8080/mcp/`（Streamable HTTP）
- Agent：`/agent/`，API：`/api/agent/`
- Evaluation：`/evaluation/`，API：`/api/evaluation/`
- Prometheus：`http://localhost:9090`（observability profile）
- 三个 API 的指标：`/api/{knowledge|agent|evaluation}/metrics/`

PostgreSQL、Redis 与 Qdrant 仅暴露在 Compose 内部网络；三个业务服务保持独立数据库和数据卷。

## 能力边界

- Knowledge：Qdrant dense + BM25，通过 RRF 融合，并可配置 Cross-Encoder；每次构建生成独立 Qdrant 集合和 PostgreSQL chunk snapshot，成功后原子切换 active 版本。
- Agent：使用 `[n]` 结构化引用，持久化 citation、检索 trace 和 verification；支持 API Key 角色、用户隔离、每日配额及审计日志。
- Evaluation：分别给出检索（Hit/Recall/MRR）、引用（覆盖/有效/数字落证）和回答（参考答案 token P/R/F1）三层指标。
- Worker：Knowledge API 只接收请求，耗时解析、索引和证据任务由 `knowledge-worker` 独立消费。

生产环境建议设置 `DEFAULT_KNOWLEDGE_BASE_ID`、`MODEL_API_KEY`，按需设置 `RERANKER_MODEL`，并启用 `AGENT_AUTH_ENABLED` 与稳定的 API Key 映射。


# medical-platform-deploy

七项目本地集成入口。

```powershell
Copy-Item .env.example .env
docker compose config
docker compose up --build
```

- 平台入口：`http://localhost:8080`
- Knowledge：`/knowledge/`，API：`/api/knowledge/`
- Agent：`/agent/`，API：`/api/agent/`
- Evaluation：`/evaluation/`，API：`/api/evaluation/`

PostgreSQL、Redis 与 Qdrant 仅暴露在 Compose 内部网络；三个业务服务保持独立数据库和数据卷。


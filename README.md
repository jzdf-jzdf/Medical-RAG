# medical-agent-api

医学 Agent 服务基线：会话、消息、运行、工具调用、引用、Skill 版本和多 MCP 配置。Agent 只消费 MCP 证据，不直接访问 `knowledge_db`，也不维护本地向量索引。

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8002
```

若所有 MCP 均无有效证据，回答必须携带“未经文献验证”提示、空引用和 `degraded_without_evidence=true`。


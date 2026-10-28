# medical-agent-api

医学 Agent 后端服务：使用 DeepSeek Harness SDK 驱动模型，通过一个精简的
“模型决策 → MCP 工具调用 → 模型总结”循环完成问答。Agent 只消费 MCP 证据，
不直接访问 `knowledge_db`，也不维护本地向量索引。

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
alembic upgrade head
uvicorn agent_api.main:app --reload --port 8002
```

至少配置 `MODEL_API_KEY`。如需连接当前项目的知识服务，可在 `.env` 中加入：

```dotenv
MCP_SERVERS_JSON=[{"name":"knowledge","transport":"streamable-http","url":"http://localhost:8001/mcp/","allowed_tools":["list_knowledge_bases","search_medical_knowledge","get_knowledge_chunk"]}]
```

调用示例：

```powershell
$conversation = Invoke-RestMethod -Method Post http://localhost:8002/api/v1/conversations `
  -ContentType 'application/json' -Body '{"title":"脑出血问答","user_id":"demo"}'

Invoke-RestMethod -Method Post http://localhost:8002/api/v1/chat `
  -ContentType 'application/json' `
  -Body (@{conversation_id=$conversation.id; user_id='demo'; content='急性脑出血如何降压？'} | ConvertTo-Json)
```

工具名在发送给模型前会加上 MCP Server 前缀；模型只能调用服务端实际发现且
位于 `allowed_tools` 白名单中的工具。每轮先由模型判断是否需要检索：临床事实、用药、
数值和指南类问题必须先调用知识库，问候或界面使用问题可直接回答。单个 MCP
服务故障不会阻断普通问答。

会话删除接口为 `DELETE /api/v1/conversations/{conversation_id}`，并要求
`X-User-ID` 请求头与会话所属用户一致。

模型连接可通过 `GET/POST/PATCH/DELETE /api/v1/models` 管理。API Key 使用
Fernet 加密后保存，接口只返回 `has_api_key`，不会返回明文或密文。首次部署建议
设置稳定的 `MODEL_ENCRYPTION_KEY`，否则服务会在 `WORKSPACE_ROOT/.model_key`
生成并持久化本机密钥。

若所有 MCP 均无有效证据，回答必须携带“未经文献验证”提示、空引用和 `degraded_without_evidence=true`。


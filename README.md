# medical-evaluation-api

医学 Agent 测试服务。它通过公开 HTTP API 读取 Knowledge 服务中的知识库、文档与分块，使用 Agent 服务生成基于分块的题目并执行试答；服务之间不跨库访问。

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8003
```

工作台接口位于 `/api/v1`：

- `GET /knowledge-bases`：列出可测试的知识库与文档。
- `GET /knowledge-bases/{collection_id}/documents/{document_id}/chunks`：查看持久化分块。
- `POST /questions/generate`：选择最多 8 个分块并通过 Agent 生成题目。
- `POST /agent-tests`：把生成或手工编辑的题目发送到 Agent 服务试答。


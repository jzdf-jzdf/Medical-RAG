# medical-evaluation-api

医学 Agent 评测服务。它通过公开 HTTP API 读取 Knowledge 服务中的知识库、文档与分块，使用 Agent 服务生成基于分块的题目；服务之间不跨库访问。

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
alembic upgrade head
uvicorn evaluation_api.main:app --reload --port 8003
```

工作台接口位于 `/api/v1`：

- `GET /knowledge-bases`：列出可测试的知识库与文档。
- `GET /knowledge-bases/{collection_id}/documents/{document_id}/chunks`：查看持久化分块。
- `GET/POST /question-banks`：浏览或创建评测题库。
- `GET /questions`：跨题库查看与筛选全部题目。
- `POST /question-banks/{id}/questions`：向题库手工加入问答题或单选题。
- `POST /question-banks/{id}/questions/bulk`：批量保存生成题目。
- `POST /questions/generate`：选择最多 8 个分块并生成问答题或单选题。
- `POST /test-runs`：选择已有题库，逐题调用 Agent 测试并持久化评分结果。
- `GET /test-runs`、`GET /test-runs/{id}`：查看最近测试记录和逐题回答。


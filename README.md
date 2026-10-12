# medical-knowledge-api

知识服务首版骨架，包含 PostgreSQL/Alembic、文献与集合模型、PDF 文件登记、章节和 Chunk 数据结构。

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8001
```

健康检查：`GET /health`；数据库就绪检查：`GET /ready`；OpenAPI：`/docs`。


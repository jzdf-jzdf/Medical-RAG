# medical-evaluation-api

测评与治理服务骨架，保存数据集、题目、批量运行和逐题结果，通过公开 API 调用 Knowledge/Agent 服务，不跨库访问。

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8003
```


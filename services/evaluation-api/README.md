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

另开一个终端启动后台评测 Worker（Windows 本地使用 solo pool）：

```powershell
celery -A evaluation_api.celery_app:celery_app worker --loglevel=INFO --pool=solo
```

工作台接口位于 `/api/v1`：

- `GET /knowledge-bases`：列出可测试的知识库与文档。
- `GET /knowledge-bases/{collection_id}/documents/{document_id}/chunks`：查看持久化分块。
- `GET/POST /question-banks`：浏览或创建评测题库。
- `PATCH /question-banks/{id}`：编辑题库介绍，传入空字符串或 null 可清除介绍。
- `GET /questions`：跨题库查看与筛选全部题目。
- `POST /question-banks/{id}/questions`：向题库手工加入问答题或单选题。
- `POST /question-banks/{id}/questions/bulk`：批量保存生成题目。
- `POST /questions/generate`：选择最多 8 个分块并生成问答题或单选题。
- `POST /test-runs`：创建后台测试并立即返回 `queued` 状态。
- `GET /test-runs`、`GET /test-runs/{id}`：查看最近测试记录和逐题回答。
- `POST /test-runs/{id}/cancel`：请求取消排队中或执行中的测试。
- `POST /test-runs/{id}/retry`：保留成功题目，重新执行失败或尚未完成的题目。

测试任务通过 Redis/Celery 投递，启用迟确认、Worker 丢失重投和单任务预取。每道题完成后立即写入
PostgreSQL，`(run_id, question_id)` 唯一约束保证重复投递不会生成重复结果。API 内置调度器会重新投递
未成功入队的 `queued` 记录，并把心跳过期的 `running` 记录恢复为队列状态。

有参考分块的题目使用 `reference_text_v1` 计算检索评分：从 Agent 成功的
`search_medical_knowledge` / `hybrid_search` 工具结果中，按调用和返回顺序合并分块，
按分块 ID 去重，取前 5 条。最终答案未引用的检索结果也参与评分。
仅在 Agent 未提供检索工具记录时，兼容使用其最终引用，并在结果中记录数据来源。

文本先统一全半角和大小写，去掉空白和普通标点，保留小数点、百分比与比较符号；
再统计连续两个字符的片段及其出现次数。每个参考分块与每条检索文本计算片段召回率、
精确率和 F1，选召回率最高的一条（同分时选 F1 最高者）。各参考分块的最佳召回率
平均值作为检索分，参与综合评分，并保存逐段匹配原文、排名和指标，供结果页核对。
因此参考原文完整包含在较长检索分块中时，覆盖率可得满分，精确率仍会反映额外内容。
这种文本指标不判断医学语义是否一致。

有参考分块但未检索到文本时记 0 分；没有参考分块时兼容文档 ID 评分，无任何标注则跳过。
`POST /evaluations/score` 可通过 `reference_chunks` 和 `retrieved_chunks` 单独验证评分，
其中参考分块支持字符串或含 `quote` / `content` / `text` / `context` 的对象。
历史测试保留原评分，重新执行题库测试后使用新规则。


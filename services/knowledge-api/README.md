# medical-knowledge-api

知识服务后端，包含 PostgreSQL/Alembic、文献条目、PDF/Markdown 版本、可注册分块方法、持久化 Chunk 与知识库构建。

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
alembic upgrade head
uvicorn knowledge_api.main:app --reload --port 8001
```

健康检查：`GET /health`；数据库就绪检查：`GET /ready`；OpenAPI：`/docs`。

## 文献与知识库流程

- 每个文献条目最多保存一份 PDF 原件和一份 Markdown 文本。
- 上传 PDF 后可调用 `POST /api/v1/documents/{id}/convert-pdf` 创建持久化任务；已有 MD 时需要显式传入 `replace_existing=true`。
- 知识库只接受已有 MD 的条目。创建后调用 `POST /api/v1/knowledge-bases/{id}/build`，后台任务会原子重建分块、MedCPT 向量和索引版本。
- 分块方法注册前可调用 `POST /api/v1/configurations/chunking-methods/preview`，使用真实 MD 进行无写入预览。
- `POST /api/v1/knowledge-bases/{id}/search` 使用 MedCPT Query Encoder 检索由 Article Encoder 生成的 768 维向量，并返回知识块、标题路径和文献引用。

## MedCPT

- 查询编码器：`ncbi/MedCPT-Query-Encoder`；文献编码器：`ncbi/MedCPT-Article-Encoder`。
- 调用 `POST /api/v1/configurations/embedding-models/{id}/download` 可提前下载两个模型；知识库构建也会自动补齐缺失模型。
- 模型默认保存在 `runtime/models/medcpt/query-encoder` 与 `runtime/models/medcpt/article-encoder`，无需 Windows 符号链接权限。
- `MEDCPT_DEVICE=auto` 会优先使用 CUDA，否则使用 CPU；索引文本最大 512 token，查询最大 64 token，均使用 `[CLS]` 表征并归一化。

## MCP

- Streamable HTTP MCP 地址为 `/mcp/`，提供 `list_knowledge_bases`、`search_medical_knowledge`、`get_knowledge_chunk` 三个只读工具。
- 集成部署时网关同时暴露 `http://localhost:8080/mcp/`，并保留原始 Host 以通过 MCP 的 DNS rebinding 校验。
- 可通过 `AUTH_ENABLED=true` 与 `API_KEYS_JSON` 启用 API Key 权限；读取需要 viewer，写入需要 editor。

## MinerU

当前适配 MinerU 4.x 命令行接口：

```powershell
mineru-kit parse document.pdf -o document.md --tier standard
```

Compose 镜像会安装并校验 MinerU 4.x，并把宿主机 `${USERPROFILE}/.mineru` 挂载到容器的 `/root/.mineru`，使已下载的标准档模型可复用。直接在 Windows 上运行 API 时，可使用项目隔离环境 `F:/Medical-RAG/runtime/mineru/.venv`，并通过 `.env` 的 `MINERU_COMMAND` 指向其中的 `mineru-kit.exe`。也可通过 `MINERU_TIER` 和 `MINERU_TIMEOUT_SECONDS` 调整解析档位和超时时间。未安装时转换接口会返回明确的 `503`，不会用低质量文本提取静默替代。

## 持久化任务队列

- PDF 转 Markdown、MedCPT 模型下载和向量索引构建都会先写入 PostgreSQL 的 `processing_tasks` 表，再由 FastAPI lifespan 启动的内置 Worker 领取执行。
- Worker 使用 `FOR UPDATE SKIP LOCKED` 原子领取任务，并持续写入阶段、可用的真实进度、心跳、结果或错误；进程异常退出后会自动恢复过期任务。
- 前端只在存在 `queued` 或 `running` 任务时轮询 `GET /api/v1/processing-tasks`，页面隐藏时降低频率，没有活跃任务时停止轮询。
- `DELETE /api/v1/processing-tasks/{id}` 删除单条已结束任务；`POST /api/v1/processing-tasks/bulk-delete` 原子删除多条已结束任务。两者都只清理任务记录，不删除生成的文档或知识库数据。
- `POST /api/v1/processing-tasks/remove` 可混合处理真实任务和 `document-version:*` 历史记录：真实已结束任务会被删除，Markdown 导入/移动历史只会持久化隐藏，不删除版本或文件。
- 开发环境可通过 `TASK_WORKER_*` 配置轮询、心跳和过期恢复时间。测试环境默认关闭 Worker，避免后台协程干扰用例事务。

## 模型目录

`app/models` 按职责拆分：`documents.py` 管理条目与文件版本，`collections.py` 管理知识库，`chunks.py` 管理章节和分块，`configurations.py` 管理方法与模型注册，`evidence.py` 管理证据结构，`indexes.py` 管理索引版本。统一从 `app.models` 导入领域模型。


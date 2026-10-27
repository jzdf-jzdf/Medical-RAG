# 医学专病知识库 ／ Agent ／ 测评平台开发规格说明

‍

> 状态：当前有效。本文是后续 Codex 开发、接口联调和验收的唯一规格基准。

## 1. 项目目标与仓库

构建三套独立 Web 应用，共 6 个业务仓库，并使用 1 个可选集成部署仓库：

|仓库|职责|端口|
| ------| ------------------------------------------------| -----: |
|​`medical-knowledge-api`|文献、PDF、解析、结构化、检索、MCP Server|8001|
|​`medical-knowledge-web`|文献管理、结构化审核、检索界面|5173|
|​`medical-agent-api`|LLM、会话、多 MCP Client、工具执行、引用和 SSE|8002|
|​`medical-agent-web`|对话、运行轨迹和引用展示|5174|
|​`medical-evaluation-api`|控制循环、安全治理、题库、批量测评和指标|8003|
|​`medical-evaluation-web`|题库、任务和结果可视化|5175|
|​`medical-platform-deploy`|Docker Compose、Nginx、网络、数据卷和备份|8080|

Knowledge API 和 Knowledge Web 必须是两个独立仓库。六个业务项目均须独立启动、测试和发布，禁止共享 ORM 模型或跨库直连。

## 2. 统一技术与工程约束

- Python 3.12+、FastAPI、Pydantic v2、SQLAlchemy 2、PostgreSQL 16+、Alembic、pytest。
- 三个独立数据库：`knowledge_db`​、`agent_db`​、`evaluation_db`。
- 数据库结构变更只能通过 Alembic，禁止用 `Base.metadata.create_all()` 代替正式迁移。
- 配置全部来自环境变量；每个仓库提供 `.env.example`，不得提交 API Key。
- API 统一以 `/api/v1` 为前缀。
- Router 只做协议适配；Repository 负责 CRUD；Service 负责业务；Client 负责外部服务、LLM 和 MCP。
- 关键模型、Prompt、Schema、工具结果和索引均需版本化。

## 3. 服务边界

三个服务按“知识生产、Agent 运行、质量评估”划分。每个服务只维护自己的数据和任务；服务间只通过已版本化的 HTTP API 或 MCP 契约通信，禁止跨库访问、共享 ORM 模型或绕过服务复制其核心能力。

### 3.1 Knowledge API：知识生产与检索服务

Knowledge 是 PDF、Markdown、Chunk、向量和索引的唯一事实来源，负责从原始资料到可检索知识的完整生命周期。

必须提供：

- 文献及附件管理：上传 PDF，保存原文件、SHA256、文献元数据和文件版本；
- 文档解析：将 PDF 异步解析为 Markdown，保存解析器、解析参数、产物版本和错误信息；
- Markdown 管理：导入、查看和版本化 Markdown，知识库构建只消费已持久化的 Markdown 版本；
- 分块方法注册：注册受支持策略的参数化配置，校验配置，并可在真实 Markdown 上无写入预览；
- 嵌入模型注册：注册受支持的本地模型或远程嵌入端点，校验模型维度和连接配置，密钥不得明文返回前端；
- 知识库构建：按选定 Markdown 版本、分块方法版本和嵌入模型版本生成 Chunk、向量与索引版本；
- 分块查看：支持构建前预览和构建后查看，展示标题路径、页码或字符位置、长度、来源文档及当前索引版本；
- 任务中心：展示 PDF 解析、模型下载、分块、嵌入、索引构建和证据抽取等任务的当前阶段、进度、心跳、耗时、错误及重试状态；
- 检索服务：提供公开检索 API 和只读 Knowledge MCP，返回稳定的文档、文档版本、Chunk、证据和索引版本标识。

“注册分块方法”是从服务端允许的策略实现中选择策略并保存版本化参数，不允许用户从 Web 端上传并执行任意 Python 代码。新增算法实现通过受审查的服务端插件或发布流程接入。

Knowledge 不负责：

- Agent 会话、Prompt、模型对话和工具编排；
- 题库、标准答案、测评批次、评分和排行榜；
- 汇总其他服务的运行任务。

### 3.2 Agent API：模型对话与工具执行服务

Agent 是模型连接、对话执行和工具调用轨迹的唯一事实来源，负责把一个明确的对话请求执行成可回放的 Agent Run。

必须提供：

- 模型注册：新增、修改、停用和设置默认模型；支持受支持的模型适配器及 OpenAI-compatible 端点；
- 密钥治理：API Key 加密存储，仅返回 `has_api_key` 等脱敏状态，不在日志、响应或浏览器存储中暴露密钥；
- 连接测试：注册模型后可执行最小请求，返回可用性、模型标识、延迟和规范化错误；
- 对话测试：选择模型、Prompt/Skill 和工具策略发起单轮或多轮对话；
- 工具执行：连接一个或多个经配置的 MCP Server，按工具白名单执行调用；
- 运行轨迹：实时展示模型思考阶段、工具名称、所属 MCP Server、脱敏参数、状态、耗时、结果摘要及最终引用；
- 运行回放：持久化模型版本、Prompt/Skill 版本、工具策略版本、工具调用、结果哈希、引用和降级状态；
- 流式输出：通过 SSE 输出运行状态、工具调用状态、回答增量、引用和完成或失败事件。

Agent 可以连接本项目的 Knowledge MCP、`medical-mcp`、PubMed MCP，以及其他经过配置和审核的 MCP Server。

Agent 不负责：

- 上传或解析 PDF、维护 Markdown、生成 Chunk 或维护文献向量索引；
- 维护题库、批量测评任务、评分标准和统计报表；
- 直接访问 `knowledge_db` 或在 Agent 内建立第二套知识索引。

### 3.3 Evaluation API：题库与 Agent 策略评估服务

Evaluation 是题目、数据集、测评配置、测评结果和指标的唯一事实来源，负责组织测试，但不复制 Knowledge 的知识处理能力或 Agent 的模型执行能力。

必须提供：

- 分块选题：通过 Knowledge API 浏览知识库、文档和已持久化 Chunk，并选择一个或多个 Chunk 作为出题依据；
- 题目生成：调用 Agent API 选择指定生成模型生成问题、标准答案、难度、主题、解析和原文证据；
- 题目治理：支持人工编辑、审核、启用、停用、版本化和加入数据集；
- 来源追溯：每道题保存 Knowledge 返回的稳定 `document_id`、`document_version_id`、`chunk_id` 和 `index_version`，不得仅保存易变化的分块序号；
- 策略快照：一次测评固定保存 `model_config_id`、模型名称、Prompt/Skill 版本、工具白名单、Knowledge 索引版本、温度、最大轮数和停止条件；
- Agent 测试：使用题库对指定 Agent 策略执行单题试答或批量测试，并关联 Agent 返回的 `agent_run_id`；
- 指标与分析：评估答案正确性、证据命中、引用完整性、工具成功率、无证据降级、延迟和成本，并支持逐题追踪和策略间对比；
- 测评任务：持久化排队、运行、完成、失败和取消状态，提供进度、失败原因和可重试能力。

Evaluation 通过公开 API 调用 Knowledge 和 Agent，不得跨库读取数据。题目生成和答题时的 LLM 调用由 Agent 执行；Evaluation 负责数据集、控制循环、停止条件、重试、评分和治理策略。

Evaluation 不负责：

- 保存 PDF/Markdown 正文、重新分块或自行生成文献向量；
- 保存模型密钥、直接调用模型供应商或自行实现 MCP 工具执行；
- 修改 Agent 的会话、工具调用和引用记录。

### 3.4 数据所有权

|领域对象|唯一所属服务|其他服务的使用方式|
| --- | --- | --- |
|PDF、Markdown、文档版本、Chunk、向量、索引|Knowledge|通过 Knowledge API/MCP 只读引用|
|分块方法、嵌入模型配置|Knowledge|Evaluation 只读取结果和版本|
|模型配置、Prompt/Skill、会话、消息|Agent|Evaluation 通过 Agent API 选择和调用|
|Agent Run、工具调用、回答引用|Agent|Evaluation 保存稳定 `agent_run_id` 并按需查询|
|题目、标准答案、数据集、测评配置、指标|Evaluation|Knowledge 与 Agent 不反向持有|

跨服务只保存稳定 ID 和必要快照，不建立数据库外键。上游资源删除前应检查或提示外部引用；历史测评仍以保存的版本和快照解释结果。

### 3.5 三条主业务链路

1. 知识生产：上传 PDF → 创建解析任务 → 生成并确认 Markdown → 选择或注册分块方法与嵌入模型 → 预览分块 → 构建知识库 → 分块、向量和索引持久化。
2. Agent 调试：注册并测试模型 → 选择模型与 Agent 策略 → 发起对话 → 查看实时工具轨迹和引用 → 按 `agent_run_id` 回放。
3. 测评闭环：从 Knowledge 选择 Chunk → 通过 Agent 生成并审核题目 → 固化数据集与 Agent 策略快照 → 批量调用 Agent → 评分、分析并比较策略。

### 3.6 任务与实时状态规范

各服务只维护本领域的任务：Knowledge 使用 `processing_task`，Agent 使用 `agent_run`，Evaluation 使用 `evaluation_run`。三类任务至少统一包含 `id`、`status`、`phase`、`progress`、`message`、`created_at`、`started_at`、`updated_at`、`finished_at` 和结构化 `error`。

状态值统一为 `queued`、`running`、`completed`、`failed`、`cancelled`；允许服务增加细分 `phase`，不允许用展示文案代替机器可读状态。前端 MVP 可在存在活动任务时增量轮询，稳定版应使用 SSE 推送；断线重连后必须能从持久化状态恢复，浏览器关闭不得中断后台任务。

## 4. PDF、文献与 MVP 范围

第一版 Knowledge MVP 必须包含：

- 文献 CRUD；
- PDF 上传、SHA256 去重和文件管理；
- DOI 识别、Crossref/OpenAlex/PubMed 元数据获取；
- 文件夹/集合管理；
- PDF 转 Markdown；
- Section-aware Chunking；
- 文献列表、详情、搜索、分页和 Chunk 查看。

解析器规则：

- 常规 PDF：MinerU；
- 复杂版式、复杂表格和结构恢复：Docling；
- 扫描件：OCR 后进入 MinerU/Docling 流程。

第一版产品形态固定为 **FastAPI + Vue 3 + PostgreSQL 的 Web 系统**，不采用 PySide6 + SQLite 桌面方案。

## 5. Knowledge 数据与检索

核心表至少包括：

- ​`documents`​、`document_files`​、`document_versions`；
- ​`collections`​、`collection_documents`；
- ​`sections`​、`chunks`；
- ​`diseases`​、`studies`​、`pico_elements`​、`outcomes`；
- ​`evidence_spans`​、`extraction_runs`​、`review_decisions`​、`index_versions`。

检索采用 PostgreSQL FTS + Qdrant Vector Search + RRF + Reranker。Chunk 必须保存页码、字符位置、文档版本和索引版本。

## 6. Agent 数据库 Schema

​`agent_db` 必须包含以下表：

### `conversations`

- ​`id`​、`title`​、`user_id`​、`created_at`​、`updated_at`。

### `messages`

- ​`id`​、`conversation_id`​、`role`​、`content`​、`created_at`。

### `agent_runs`

- ​`id`​、`conversation_id`​、`user_message_id`​、`model_provider`​、`model_name`​、`prompt_version`​、`skill_version`​、`status`​、`started_at`​、`finished_at`。

### `tool_calls`

- ​`id`​、`agent_run_id`​、`mcp_server`​、`tool_name`​、`arguments JSONB`​、`result JSONB`​、`result_hash`​、`latency_ms`​、`status`​、`created_at`。

### `answer_citations`

- ​`id`​、`message_id`​、`tool_call_id`​、`document_id`​、`document_version_id`​、`chunk_id`​、`evidence_span_id`​、`index_version`​、`citation_order`​、`external_source_id`​、`source_server`。

### `agent_skills`

- ​`id`​、`name`​、`version`​、`system_prompt`​、`config JSONB`​、`enabled`。

Agent 不创建到 Knowledge 数据库的真实外键，只保存稳定的跨服务 ID。

## 7. 多 MCP 与证据契约

Knowledge MCP 首批工具：

- ​`search_evidence`；
- ​`get_evidence`；
- ​`get_document`。

Agent 可并行调用多个 MCP，但必须保存每次调用的 Server、工具、参数、完整结果、耗时、状态和结果哈希。

正式引用的 Knowledge 证据必须保留：

- ​`document_id`；
- ​`document_version_id`；
- ​`chunk_id`；
- ​`evidence_span_id`；
- ​`index_version`。

外部 MCP 如果没有上述内部 ID，不得伪造。应保留 `source_server`​、`external_source_id`、原始结果和结果哈希；若外部证据需要进入正式测评或长期回放，应先通过 Knowledge API 注册证据快照，由 Knowledge 生成上述五个稳定 ID。

MCP 检索全部失败时，Agent 可以基于模型自身知识回答，但必须：

1. 明确显示“当前未检索到可核验文献证据，以下内容未经文献验证”；
2. ​`answer_citations` 为空；
3. 禁止生成虚构引用；
4. 在 `agent_runs` 和 SSE 中记录降级状态。

## 8. Agent REST API

- ​`POST /api/v1/conversations`
- ​`GET /api/v1/conversations`
- ​`GET /api/v1/conversations/{id}`
- ​`POST /api/v1/chat`
- ​`POST /api/v1/chat/stream`
- ​`GET /api/v1/runs/{id}`
- ​`GET /api/v1/runs/{id}/citations`
- ​`GET /api/v1/skills`

首次对话由 `/conversations`​ 显式创建。`/chat`​ 请求使用 `conversation_id`​，不再使用另一套 `/sessions` 资源命名。

## 9. SSE 协议

事件名称固定为：

- ​`run_started`；
- ​`tool_started`；
- ​`tool_completed`；
- ​`answer_delta`；
- ​`citations`；
- ​`run_completed`；
- ​`run_failed`。

所有事件至少包含 `run_id`​。工具事件包含 `tool_call_id`​ 和 `mcp_server`；引用事件包含稳定证据 ID；完成事件包含最终状态、模型版本、Prompt 版本和是否发生无证据降级。

## 10. 配置规则

基础设施配置来自环境变量，包括数据库地址、加密主密钥、MCP Server 允许列表、超时、历史轮数、TTL 和运行目录。

需要在运行期切换的业务配置使用各服务自己的注册表：Knowledge 管理分块方法和嵌入模型；Agent 管理对话模型和 Agent 策略；Evaluation 管理出题配置、数据集和测评策略快照。业务配置必须版本化并记录创建者、更新时间和启停状态。

模型端点与 API Key 可通过 Agent 管理接口注册。API Key 只可写入或替换，不得通过读取接口返回；服务端使用稳定的 `MODEL_ENCRYPTION_KEY` 加密保存。环境变量中的模型配置仅作为首次部署默认值和故障恢复兜底，不再是唯一切换方式。

分块和模型“自由注册”只允许选择服务端已支持的适配器并填写受校验的参数。任意代码、动态模块和未经审核的网络协议不得通过管理界面直接注入执行。

推荐变量：

- ​`DATABASE_URL`
- ​`MODEL_PROVIDER`​、`MODEL_NAME`​、`MODEL_BASE_URL`​、`MODEL_API_KEY`
- ​`MODEL_ENCRYPTION_KEY`
- ​`MCP_SERVERS_JSON`
- ​`MAX_HISTORY_TURNS`​、`CONVERSATION_TTL_HOURS`
- ​`ENCRYPTION_KEY`（只有确需应用层加密时使用）

## 11. 开发顺序与验收

1. Knowledge API：PostgreSQL/Alembic、文献、PDF、DOI、文件夹和解析。
2. Knowledge Web：上传、管理、解析状态和 Chunk。
3. 医学结构化、证据审核和混合检索。
4. Knowledge MCP。
5. Agent API：规定 Schema、REST、SSE、多 MCP、降级和引用。
6. Agent Web。
7. Evaluation：控制循环、安全治理、Gold Dataset 和指标。
8. 集成部署和端到端测试。

验收重点：Agent 无本地索引；所有 Knowledge 引用含五个稳定 ID；外部 MCP 结果可回放；无证据回答有明确警告；数据库和接口完全符合本规格。

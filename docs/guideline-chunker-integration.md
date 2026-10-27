# 医学指南原子知识分块集成

系统已内置原项目完整 Python 引擎（固定版本 `13940c9`）及原 HICH 主题体系。
分块方法策略标识为 `guideline_semantic`；数据库迁移会注册一个默认方法。
前端“模型注册 → 注册分块方法”也可配置该引擎。

## 执行链路

Markdown 严格预检 → 原子化 → 推荐候选识别 → 章节内批次 → DeepSeek 判断 →
连续原文重建 → 全文质量校验 → 原子发布 chunks / manifest / audit / report。
模型不会自由改写正文。系统保存上游 chunk_id、FACT/REC 类型、section_id、
topic_tags、summary、recommendation_levels、字符范围、运行 ID 及来源版本。

原项目适用于规范化的中文医学指南，默认标签体系是高血压性脑出血 HICH。
其结构限制、受控标签、DeepSeek 模型契约和质量门均保留，不能把预检或模型失败
静默替换为字符分块。输入应为 UTF-8 无 BOM Markdown。
接入层在摘要校验失败后的重试反馈中补充忠实于原文的具体要求，不修改模型输出，
也不放宽上游验证规则；补充说明的版本参与缓存目录隔离并写入知识块元数据。

## 后端配置

知识 API 和 Worker 均需从环境获得 `DEEPSEEK_API_KEY`。
可选 `DEEPSEEK_BASE_URL` 默认 `https://api.deepseek.com`。
本机直接启动使用进程环境或 `medical-knowledge-api/.env`；Docker Compose 从
启动命令的进程环境或部署目录 `.env` 读取并传给两个容器。密钥不进前端、
分块方法 JSON、运行配置、产物或日志。

## 使用方式

1. 上传 Markdown，或先将 PDF 转成 Markdown。
2. 选择“医学指南原子知识分块”方法；可调整语义批次、上下文、摘要、缓存和质量门。
3. 样本预览会返回 `202` 并创建可重试的后台任务，不占用长时间 HTTP 请求。
4. 创建知识库只保存配置与文献关联；点击“构建向量索引”后 Worker 执行整本文献语义切块和嵌入。
5. 在知识库分块预览或解析任务的“查看切块结果”中查看知识类型、主题和推荐等级，下载四种产物。

语义预览可展示有质量问题的部分结果。默认 `require_quality_gate=true` 会阻止
该结果发布为向量索引；用户可明确配置关闭。切块不是之前停用的“证据审核”功能。
旧字符/标题切块方法及既有索引继续可用。

## API

- `GET /api/v1/configurations/guideline-chunker`：版本、模型、标签数量、密钥是否配置、默认参数；不返回密钥。
- `POST /api/v1/configurations/chunking-methods/preview`：`strategy=guideline_semantic` 时返回 `202 ProcessingTaskRead`。
- `GET /api/v1/processing-tasks/{task_id}`：持久化状态、批次进度、失败原因。
- `GET /api/v1/processing-tasks/{task_id}/result`：已完成指南切块的结果、报告、manifest 和下载地址。
- `GET /api/v1/guideline-chunking/runs/{run_id}/artifacts/{name}`：`source.md`、`chunks.jsonl`、`manifest.json`、`audit.jsonl`、`processing_report.json`。
- 原知识库创建、预览、构建、检索接口使用相同引擎，元数据随原生知识块持久化。

运行保存在 `WORKSPACE_ROOT/guideline-chunker/runs/<uuid>/`，共享缓存位于
`WORKSPACE_ROOT/guideline-chunker/cache/`。每次运行保存不含密钥的配置，便于原
引擎复现/续跑。关闭页面不会停止后台任务；可从解析任务页重新打开结果。
运行产物按追溯数据保留，不随知识库或任务历史删除。

## 2026-10-27 验证记录

- 原项目离线测试：201 通过，1 项真实 API 测试由集成验证单独覆盖。
- 知识服务测试：35 通过，包含原文 CRLF 偏移、语义缓存、质量门与索引元数据映射。
- 24 个上游源码文件的 SHA256 与原目录逐一一致。
- 人工 Markdown 样本经真实 DeepSeek 生成 FACT/REC，通过质量门；页面注册方法、
  后台任务、MedCPT 向量化、Qdrant 混合检索及四类产物下载全部通过。
- 浏览器无页面错误，桌面与 375px 移动宽度参数面板已检查。
- 临时文献、方法、知识库和任务记录均已清理；测试运行产物按追溯策略保留。

端到端脚本：`tests/guideline_chunker_e2e.py`。
结果与截图：`test-results/guideline-chunker-e2e/`。

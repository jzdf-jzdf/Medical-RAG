# 检索MCP

‍

# 检索 MCP 实现方案

## 一、目标

Agent API 作为 MCP Host，可以同时连接多个 MCP Server：

- 项目内部 Knowledge MCP；
- ​`medical-mcp`；
- PubMed MCP；
- 其他经过白名单审核的医学 MCP。

Agent 可以直接调用这些 MCP，但禁止维护第二套文献索引、禁止直接访问 `knowledge_db`，也禁止在 Agent 内运行独立向量检索。

## 二、MCP 配置

Server 列表全部来自环境变量 `MCP_SERVERS_JSON`。每个 Server 配置包含：

- ​`name`；
- ​`transport`；
- ​`command/args`​ 或 `url`；
- ​`enabled`；
- ​`timeout_seconds`；
- 允许调用的工具白名单；
- 证据来源类型和信任级别。

Agent 启动时连接启用的 Server，运行期间复用连接。修改 MCP 配置后通过重启或滚动发布生效。

## 三、工具选择

对每个 Server 显式配置工具映射，例如：

- Knowledge MCP：`search_evidence`​、`get_evidence`​、`get_document`。
- PubMed MCP：配置其实际搜索和文献详情工具。
- ​`medical-mcp`：配置药品、指南、文献等实际工具。

禁止使用“按关键词猜工具，找不到就调用第一个工具”的策略。关键 Server 必须有契约测试。

## 四、检索流程

```text
用户问题
  → 解析证据需求
  → 根据策略选择一个或多个 MCP Server
  → 创建 agent_run
  → 为每个调用创建 tool_call
  → 并行调用、独立超时
  → 保存参数、完整结果、状态、耗时和结果哈希
  → 规范化证据
  → 构建带编号 Prompt
  → LLM 回答
  → 保存 answer_citations
```

Evaluation API 负责跨步骤控制循环、重试、停止条件和安全/治理策略；Agent API 执行原子 MCP 调用并返回可审计结果。

## 五、标准证据结构

Knowledge MCP 的每条证据必须包含：

- ​`document_id`
- ​`document_version_id`
- ​`chunk_id`
- ​`evidence_span_id`
- ​`index_version`
- ​`title`
- ​`page`
- ​`text`
- ​`score`
- ​`review_status`

Agent 必须原样保留五个稳定 ID，不得只降维成 `title/snippet/url/score`。

外部 MCP 结果必须保留：

- ​`source_server`
- ​`external_source_id`（如 PMID、DOI 或服务端稳定 ID）
- ​`title`​、`text/abstract`​、`url`
- ​`retrieved_at`
- 原始工具结果
- ​`result_hash`

外部 MCP 没有内部 ID 时不得伪造。外部证据若要作为正式引用参加长期测评或回放，应通过 Knowledge API 注册证据快照，由 Knowledge 生成 `document_id`​、`document_version_id`​、`chunk_id`​、`evidence_span_id`​ 和 `index_version`。

## 六、持久化

每次 MCP 调用写入 `tool_calls`：

- ​`agent_run_id`
- ​`mcp_server`
- ​`tool_name`
- ​`arguments JSONB`
- ​`result JSONB`
- ​`result_hash`
- ​`latency_ms`
- ​`status`

最终引用写入 `answer_citations`​，通过 `tool_call_id` 追溯原始结果。不得只把引用存放在消息 JSON 中。

## 七、并行、超时与降级

- 多 Server 可以并行调用。
- 每个 Server 独立超时、熔断和错误记录。
- 单个 Server 失败不阻断其他 Server。
- 所有 Server 都失败或没有有效证据时，进入无证据降级。

无证据降级规则：

1. 允许 LLM 基于自身知识回答。
2. 回答开头必须写：“当前未检索到可核验文献证据，以下内容未经文献验证。”
3. 不生成引用，`answer_citations` 为空。
4. 禁止编造 DOI、PMID、文献标题、作者或 URL。
5. ​`agent_runs.degraded_without_evidence = true`。
6. SSE 的 `run_completed` 返回降级标志。

## 八、Prompt 构建

有证据时，Prompt 要求模型只基于证据回答并使用 `[1]`​、`[2]`​ 标注。编号与 `answer_citations.citation_order` 一致。

历史对话和当前证据分开组织，避免把历史回答误当成新证据。检索相关性分数不等于医学证据等级或答案正确概率。

## 九、SSE

- ​`run_started`：创建运行。
- ​`tool_started`：准备调用 MCP。
- ​`tool_completed`：工具结果已保存。
- ​`answer_delta`：回答增量。
- ​`citations`：返回引用及稳定 ID。
- ​`run_completed`：成功完成，包含降级状态。
- ​`run_failed`：运行失败。

## 十、安全与质量

- MCP Server 和工具均使用白名单。
- 对外部文本执行长度限制和 Prompt Injection 防护。
- 保存原始结果和哈希，支持测评回放。
- 对正式医学结论优先使用经过审核的 Knowledge 证据。
- 为多 MCP、部分失败、全部失败、超时、错误 Schema 和引用 ID 完整性编写集成测试。

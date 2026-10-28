# Monorepo 架构说明

## 目标

本次重构只改变源码组织、依赖管理和构建入口，不改变对外行为。原七个仓库的完整 Git 历史已通过 subtree 导入，不做 squash。

## 边界与依赖

| 层 | 可以依赖 | 禁止依赖 |
| --- | --- | --- |
| `apps/*` | `packages/web/*`、对应 HTTP API | 其他应用源码、后端内部模块 |
| `services/*` | `packages/python/*`、外部协议客户端 | 其他服务的模型、仓储、迁移或内部服务类 |
| `packages/*` | 通用第三方库 | 任一业务域源码 |
| `infra/*` | 所有可部署单元的公开构建契约 | 业务实现细节 |

共享包只接纳同时满足以下条件的代码：

1. 至少被两个业务单元真实复用；
2. 语义稳定且属于横切能力；
3. 不携带 Knowledge、Agent 或 Evaluation 领域概念；
4. 变更不会要求多个业务域同步修改内部模型。

因此登录壳层和指标中间件进入共享包，而数据库模型、Pydantic Schema、API Router、Alembic 迁移和业务 Service 均留在各自领域内。

## 原仓库映射

| 原仓库 | 单仓路径 |
| --- | --- |
| `medical-knowledge-web` | `apps/knowledge-web` |
| `medical-agent-web` | `apps/agent-web` |
| `medical-evaluation-web` | `apps/evaluation-web` |
| `medical-knowledge-api` | `services/knowledge-api` |
| `medical-agent-api` | `services/agent-api` |
| `medical-evaluation-api` | `services/evaluation-api` |
| `medical-platform-deploy` | `infra/platform` |

查看某个原仓库的文件历史时使用路径过滤，例如：

```powershell
git log --follow -- services/knowledge-api/app/main.py
```

迁移前的七个工作目录另存于 `F:\Medical-RAG-legacy-repos-20261027`，用于短期回滚核对；单仓稳定后可由维护者自行归档。

# medical-agent-web

医学 Agent 的 Vue 3 前端，支持会话列表、真实问答、DeepSeek Harness 运行状态、
模型连接配置、MCP 工具轨迹、证据降级提示和响应式移动端布局。模型入口位于
页面右上角，支持新增、编辑、停用和切换模型。

```powershell
npm install
npm run dev
```

访问 `http://localhost:5174/agent/`。开发服务器会把 `/api/agent` 转发至
`http://localhost:8002`；也可以通过 `VITE_AGENT_API_BASE` 覆盖 API 前缀。

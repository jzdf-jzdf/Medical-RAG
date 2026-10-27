# medical-evaluation-web

面向医学 RAG 评测的题库管理前端，支持跨题库查看题目、手工录入问答题或单选题，以及从 Knowledge 分块生成题目并入库。

```powershell
npm install
npm run dev
```

开发服务器运行在 `http://localhost:5175/evaluation/`，并将 `/api/evaluation/*` 代理到 `http://localhost:8003`。

`npm install && npm run dev`，访问 `http://localhost:5175/evaluation/`。

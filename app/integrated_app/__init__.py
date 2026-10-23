"""VOSR2-Lite 应用包。

结构：
- app_server.py        FastAPI 入口（uvicorn）
- config.py            config.yaml 加载
- state.py             全局应用状态（引擎 / 任务管理器 / 历史库）
- task_manager.py      单 GPU 串行任务队列 + SSE 事件
- model_manager.py     模型权重盘查与加载状态
- engines/             推理引擎（vosr2_engine.py 封装上游 vendored 代码）
- routes/              UI 页面 / 修复 API / 系统状态 API
- middleware/          安全响应头 / 限流 / 请求 ID / 错误处理
- templates/ static/   Jinja2 模板与静态资源
- locales/             i18n 语言包（zh / en）
"""

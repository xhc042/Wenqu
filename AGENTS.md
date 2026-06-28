# AGENTS.md

问渠 (Wenqu) v1.1 —— 基于 FastAPI + SQLite + DeepSeek 的 AI 驱动苏格拉底式阅读学习平台。支持 EPUB / MD / TXT / URL 文本源,提供速读 / 标准 / 研读三种模式,内置 8 个 AI 教师角色与可调教学风格滑块。

## Setup commands

- 安装依赖: `pip install -r requirements.txt`
- 启动开发: `python start_server.py` (或 `python app.py`)
- 打包构建: `python build.py`
- 运行测试: `pytest tests/ -v`
- 跑快速测试(跳过 LLM): `pytest tests/ -v -m "not slow"`

## Project layout

- `app.py` — FastAPI 主应用、路由、异步任务、WebSocket(105KB,逐步拆)
- `chunker.py` — TOC-First 4 级分章引擎(EPUB 优先读 nav/toc.ncx)
- `state_machine.py` — 苏格拉底对话状态机(INIT→SHARE→PROBE→WAIT_USER→EVAL→...)
- `database.py` — SQLite CRUD(课程/章节/掌握项/事件/契约/证书)
- `config.py` — 配置中心(角色滑块/认知深度/阅读模式)
- `llm_client.py` — LLM 抽象(chat_json / multi_llm / 思考标签剥离)
- `prompts/roles/*.md` — 8 个 AI 教师角色 prompt(march7/keqing/ganyu/socrates/linmo/yunyi/zhiwei/yunxiu)
- `static/` — 前端(index.html + 原生 JS,无框架)
- `tests/` — pytest 测试(覆盖率目标 50-95%,当前 0%)
- `doc/` — 7 份历史优化记录(速读模式/深度学习模式/...)
- `wenqu_data/` — 运行时 SQLite + 上传文件(**不入仓**)

## Code style

- Python 3.10+,纯 async/await,FastAPI 装饰器路由
- 中文 docstring(产品向,不是英文翻译腔)
- 命名: 文件/函数/变量 `snake_case`,类 `PascalCase`,常量 `UPPER_SNAKE_CASE`
- 错误处理统一 `try/except` + `logger.warning`,**LLM 失败不阻塞主流程**
- 不要给用户暴露 PDF 上传(`app.py` 显式 400 拒绝)
- 详细规范见 `.harness/docs/code-style.md`

## Testing instructions

- 测试框架: pytest
- P0 优先(纯逻辑,易测): `tests/test_chunker.py`、`tests/test_state_machine.py` —— 目标 90%+
- P1(用 mock): `test_config.py`、`test_llm_client.py` —— 目标 70-80%
- P2(临时 SQLite): `test_database.py` —— 目标 75%+
- 集成(只关键路由): `app.py` —— 目标 50%+
- 新增功能**必须**补测试,文件名 `test_<module>.py`
- 慢测试标 `@pytest.mark.slow`,默认跑 `pytest -m "not slow"`

## PR & commit conventions

- 默认分支: `1.20`
- 一次提交只做一件事
- Commit 格式: `<scope>: <改动>`(如 `chunker: 修复 EPUB TOC 解析失败时回退`)
- 涉及 schema 变更必须先和 `.harness/reins/db-migrator` 对齐
- 涉及 prompt 变更必须和 `.harness/reins/prompt-engineer` 对齐
- 完整架构见 `.harness/docs/architecture.md`

## Security

- 绝不能 commit API key(用环境变量 `WENQU_API_KEY`)
- SQLite 数据库文件 `wenqu_data/*.db` 不入仓
- 上传文件统一放 `wenqu_data/uploads/`
- LLM 调用超时设置 60s(见 `llm_client.py` 默认配置),不要改成无限等待

## Do NOT

- ❌ 改 `app.py` 的模块级全局状态结构(`async_tasks`、`LLM_CONFIG`),并发会乱
- ❌ 把 LLM 调用的提示词直接拼字符串,用 `prompts/roles/*.md` + `llm_client.build_system_prompt`
- ❌ 绕过 `DialogueStateMachine` 自由发挥对话流程(状态机是强约束)
- ❌ 改 `wenqu_data/` 下任何文件(运行时数据)
- ❌ 给 Python 测试用例不接 `_temp_db` fixture(会污染开发数据库)

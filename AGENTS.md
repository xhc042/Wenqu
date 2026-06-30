# AGENTS.md

问渠 (Wenqu) v1.1 —— 基于 FastAPI + SQLite + DeepSeek 的 AI 驱动苏格拉底式阅读学习平台。支持 EPUB / MD / TXT / URL 文本源,提供速读 / 标准 / 研读三种模式,内置 8 个 AI 教师角色与可调教学风格滑块。

## Repository

- GitHub: https://github.com/xhc042/Wenqu.git
- 默认分支: `1.20`(其他详情见 "PR & commit conventions")

## 产品愿景(灵魂)

读书的价值分三层,**这是问渠产品的核心定位**,改任何功能前先问自己在哪一层:

- **L1 读完** —— 把书看完(传统电子书能做)
- **L2 读懂** —— 理解核心观点、记住关键信息(问渠标准模式)
- **L3 会用** —— 形成自己的判断、能输出(笔记 / 对话 / 应用)

**问渠主攻 L3**,不要陷在 L1/L2 的工具改进里。评估新功能时反问自己:**这个功能是帮用户"读完"还是"会用"?**

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
- `tests/` — pytest 测试(覆盖率目标 50-95%,当前 46% 生产代码, P0/P1 测试达 96-100%)
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
- 涉及 schema 变更 → Mavis 戴 db-migrator 视角(`CREATE TABLE IF NOT EXISTS` / 兼容老库 `ALTER TABLE`,不直接 `DROP TABLE`)
- 涉及 prompt 变更 → Mavis 戴 prompt-engineer 视角(8 角色 × 4 深度 = 32 组合一致,改完跑 `tests/test_prompts.py` 回归)
- 涉及产品方向 / UX 改动 → Mavis 戴 reading-mentor 视角(回答 5 问 + L1/L2/L3 定位)
- 完整架构见 `.harness/docs/architecture.md`

## Agent team

本项目有 6 个 rein(项目规范在 `.harness/reins/`,运行时副本在 `~/.mavis/agents/`)。**按 Mavis runtime 是否能真实 spawn 拆成两类**:

### Spawnable(可被真实 spawn,verifier-only)

| Rein | 职责 | 何时 spawn |
|---|---|---|
| `tester` | pytest + 覆盖率 | 见 `.harness/reins/tester/agent.md` 的 `When to spawn me` |
| `code-reviewer` | 代码审查(只看不改) | 见 `.harness/reins/code-reviewer/agent.md` 的 `When to spawn me` |

### Playbook(Mavis 内化执行,不可 spawn)

这 4 个不是被"拉起来"的独立 worker,而是 **Mavis 进入对应领域时戴上的视角 / playbook**。Mavis 自己读 `.harness/reins/<name>/agent.md`,自己执行该领域的修改。

| Rein | 视角 / playbook | 触发场景 |
|---|---|---|
| `developer` | 业务代码实现 | 改后端 Python / FastAPI / 前端 `static/` |
| `prompt-engineer` | 8 个 AI 角色 prompt 一致性 | 改 `prompts/roles/*.md` |
| `db-migrator` | SQLite schema 影响评估 | 改 `database.py` 表结构 |
| **`reading-mentor`** | **产品视角 / UX / L1/L2/L3 定位** | **新功能立项 / 改 UX / 产品方向调整前** |

> **关键约束**:Mavis runtime 的 spawn 通道是 verifier-only。`developer` / `prompt-engineer` / `db-migrator` / `reading-mentor` **永远不会被 spawn**,它们是 Mavis 的内化视角。看到"通知 developer"、"拉 reading-mentor"这种描述时,正确理解是"Mavis 自己读对应 playbook,按它的 Stop when 行事"。

`reading-mentor` 是**产品视角型 rein** —— 不写代码、不写 prompt、不写测试,产出是"需求 + 体验评估",作为 developer / prompt-engineer 的输入。**任何功能改动前问一句"用户视角要不要做",能少走很多弯路**。

### 自动触发入口(post-commit hook)

- `post-commit` hook(`.harness/hooks/post-commit`)会在 commit 后同步跑 `pytest -m "not slow"` + 打印 review checklist + 写入 `.harness/.last-commit.json`
- Mavis 下次会话开启时会读取 `.last-commit.json`,知道有未 review 的 commit,主动 spawn `tester` / `code-reviewer` 复查
- 安装方法: `cp .harness/hooks/post-commit .git/hooks/post-commit`(详见 `.harness/hooks/README.md`)

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

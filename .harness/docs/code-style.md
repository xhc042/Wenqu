# Wenqu 代码风格规范

> 适用于 问渠 (Wenqu) v1.1 所有 Python 后端代码。Prompt 风格见 `prompt-engineer/agent.md`,前端见 `static/` 目录(暂未规范化)。

## 命名

| 类型 | 规范 | 示例 |
|---|---|---|
| 文件/函数/变量 | `snake_case` | `smart_chunk.py` / `extract_text` / `course_id` |
| 类 | `PascalCase` | `DialogueStateMachine` |
| 常量 | `UPPER_SNAKE_CASE` | `LLM_CONFIG` / `DEFAULT_SLIDERS` |
| 私有方法/变量 | 前缀 `_` | `_run_speed_mode_postprocess` / `_utc_dict` |

## 注释与 docstring

- **使用中文**(产品向,不是英文翻译腔)
- 模块顶部 docstring 写明模块职责和关键设计点
- 函数 docstring 写输入/输出/副作用,不要写废话
- 复杂逻辑加行内注释解释"为什么",不是"做什么"

```python
async def _run_speed_mode_postprocess(course_id: str, ...) -> dict:
    """
    speed 模式后处理(修复 P0-①: 消除 3 处重复代码)

    流程:
    1. 准备 (chapter_idx, title, content) 三元组
    2. 并发生成快照(P0-② extract_chapter_snapshots_batch)
    ...
    """
```

## 异步规范

- 优先 `async def`,不要混用同步阻塞调用
- 异步任务统一模式:

```python
task_id = str(uuid.uuid4())[:8]
async_tasks[task_id] = {
    "task_id": task_id,
    "course_id": course_id,
    "type": "chapters_generate",
    "status": TASK_STATUS["PENDING"],
    "progress": 0,
    "steps": [...],
    "current_step": 0,
    "result": None,
    "error": None,
    "created_at": datetime.now().isoformat(),
}
asyncio.create_task(run_chapter_generation(task_id))
```

- LLM 调用必须 `try/except`,失败 `logger.warning` 不抛(允许 UI 降级)
- `httpx.AsyncClient` 用 `async with` 包,不要泄露连接

## 数据库

- 用 `db.get_conn()` 拿连接,用完 `try/finally conn.close()`
- 写操作包 `try/except`,失败 `logger.warning`
- 复杂查询抽到 `database.py`,不写在路由里
- 涉及 schema 变更先和 db-migrator rein 对齐
- **中文环境编码规范**(v1.1 第二轮审查新增)：
  - 所有文件读写必须显式指定 `encoding="utf-8"`
  - 数据库操作使用参数化查询(`?` 占位符),禁止字符串拼接中文
  - 日志输出使用 `ensure_ascii=False`
  - 统一 UTF-8,禁止 gbk/gb2312/big5 等本地编码
  - 详见 `.harness/reins/developer/agent.md` "中文环境编码规范" 条目

## Prompt

- 8 个角色 prompt 各自一个 `.md` 文件,在 `prompts/roles/`
- **不在 Python 里拼 prompt 字符串**
- 改 prompt 走 prompt-engineer rein,不在 commit 里偷偷改

## 测试

- 文件名 `test_<module>.py`,与被测模块同名
- 数据库测试用 `conftest.py` 的 `_temp_db` fixture 隔离
- LLM 调用统一 `unittest.mock.patch`
- 慢测试加 `@pytest.mark.slow`
- 跑测试默认 `pytest -m "not slow"`(CI 友好)
- **新增功能必须补测试**,改完业务代码不能直接说"完事"

## 错误处理

- LLM 失败 → `logger.warning(...)` + 降级返回值,**不抛异常**
- DB 失败 → `logger.warning(...)` + 兜底数据(默认掌握项 / 默认章节标题)
- 文件 IO 失败 → `logger.warning(...)` + 提示用户
- HTTP 4xx/5xx → 抛 `HTTPException(status_code, detail)`

## Git

- 默认分支: `1.20`
- Commit 格式: `<scope>: <改动>`(scope 用模块名,无 scope 可以省略)
- 一次提交只做一件事
- 涉及 schema / prompt 的改动必须在 commit message 里点出

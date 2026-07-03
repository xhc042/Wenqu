# Wenqu 架构说明

> 适用范围: 问渠 (Wenqu) v1.1 全栈(FastAPI + SQLite + DeepSeek + 原生 JS 前端)

## 模块依赖图

```
app.py (FastAPI 路由入口 + WebSocket + 异步任务协调)
  ├── routes/ (v1.1.3 新增: 模块化路由)
  │     ├── task_routes.py   — 异步任务管理(asyncio.Lock + threading.Lock 双重保护)
  │     ├── course_routes.py — 课程 CRUD、分章、快照、掌握项
  │     ├── chat_routes.py   — WebSocket 对话、会话管理
  │     └── defense_settings.py — 答辩、LLM 配置、日记、证书
  ├── chunker.py (文本提取 + 分章 + EPUB 解析)
  │     └── llm_client.py
  ├── state_machine.py (对话状态机)
  │     ├── llm_client.py
  │     ├── config.py
  │     └── database.py
  ├── database.py (SQLite CRUD)
  ├── config.py (常量中心,无依赖)
  └── llm_client.py (LLM 抽象,httpx 客户端)
```

**关键设计点**:
- `config.py` 是叶子节点,被所有其他模块引用,改它影响面最大
- `llm_client.py` 是 LLM 调用的统一入口,所有 AI 生成都走这里
- `state_machine.py` 不直接调 LLM 生成完整回复,而是按状态调 LLM 生成对应片段
- **v1.1.3: `routes/` 模块化拆分** — 将 `app.py` 中的路由按功能拆为 4 个模块,降低单文件复杂度
- **v1.1.3: `async_tasks` 并发安全** — 新增 `asyncio.Lock` + `threading.Lock` 双重保护,消除竞态条件

## 核心数据流

### 1. 创建课程
```
POST /api/courses
  → db.create_course
  → (异步) run_chapter_generation
       → chunker.extract_text
       → chunker.smart_chunk (TOC-First)
       → db.add_chapter
       → (speed 模式) _run_speed_mode_postprocess
            → extract_chapter_snapshots_batch
            → generate_global_highlights
            → _persist_speed_results
       → (非 speed) generate_syllabus_items
```

### 2. 对话流程
```
WebSocket /ws
  → DialogueStateMachine (状态: INIT → SHARE → PROBE → WAIT_USER → EVAL → EXPLAIN → GUIDE → ACTION → DEBATE → END)
       → llm_client.llm.chat_json
       → db.add_learning_event
       → 流式返回前端
```

### 3. 速读模式
- 全章节按"首尾采样 ≤ 600 字"切片存 `chapters.content_slice`
- 并发生成 `chapter_snapshots`(关键词/核心观点/学习目标/重要性)
- 一次性生成 `global_highlights`(key_points / 章节优先级 / 关系图)
- 用 `key_points` 按 `importance` round-robin 分配到 `syllabus_items`
- **v1.3 P0：核心知识优先级链路**
  - `syllabus_items.importance` 字段(1-5,5=最核心)
    - 老库自动迁移:`_migrate_schema` 检查字段后 ALTER TABLE
    - 写入端:`_run_speed_mode_postprocess` 按 key_points 排序生成梯度(前30%→5,中间40%→4,后30%→3)
  - `chunker.generate_global_highlights` prompt 强化"本书独有"原则,禁止生成泛化模板
  - **v1.x：核心知识点 prompt 升级 + 并发收敛**
    - 去掉 `key_points` 的 25 字字数限制（金融/医学等领域实体名长,截断会丢信息）
    - prompt 强化"动宾短语"规则（动词+对象+补语,对象要列全）
    - `extract_chapter_snapshots_batch` 默认 `concurrency` 由 3 → 2（避免瞬时打 LLM 配额）
    - `_run_speed_mode_postprocess` 默认 `concurrency` 同步收敛到 2
    - `_generate_fallback_key_points` 不再 `[:30]` 截断 viewpoint（兜底链路也保留完整对象）
    - `_build_highlights_fallback`（LLM 三次重试全失败后的终极兜底）也不再 `vp[:30]` 截断（与 _generate_fallback_key_points 是两条独立路径,wenqu-reviewer v1.x review 抓到漏改,已修复）
  - **v1.20.1：并发优化 — 30 章书导入从 ~60s → ~25s**
    - `llm_client.LLMClient` 改用实例级单例 `httpx.AsyncClient`（含连接池 `Limits(max_connections=20, max_keepalive=10)`），每次 LLM 调用省 ~1-2s TCP+TLS 握手。`aclose()` / `close_all_clients()` 提供 graceful shutdown 入口
    - `chunker.generate_syllabus_items` 由串行 for 循环改为 `asyncio.gather` 并发批（Semaphore 限流，6 章→2 批并行），单批失败 fallback 隔离
    - `config.get_generation_concurrency(override)` 统一并发度解析（默认 3，clamp 到 [1, 6]），`_run_speed_mode_postprocess` / `extract_chapter_snapshots_batch` / `generate_syllabus_items` 三处统一读此配置
    - `POST /api/courses` 支持可选 `generation_concurrency` 请求字段（前端高级设置面板透传）
  - `state_machine._get_mastery_hint/_check` 按 importance desc 取,文本含 `[重要度N/5]` 标记
  - SHARE 阶段:优先揭示核心观点,不从基础概念开始
  - PROBE 阶段:必须先攻高重要度知识点,显式禁止停留在基础定义
- **v1.3 P1-任务7：核心触达保险**
  - `_has_touched_core()`: 检查本章是否触达过 importance≥4 的 syllabus(in_progress/mastered)
  - `_force_core_probe()`: 在 min_rounds 边界,若未触达核心 → 强制 PROBE 1 次
  - `_core_probe_attempted` 标记:防止强制 probe 无限循环(每章节最多 1 次)
  - 边界场景:
    - 已触达核心 → 正常结束
    - 未触达 + 未尝试 → 强制 PROBE 一次后判断
    - 未触达 + 已尝试 → 正常结束(不无限循环)
    - 进心流 → 不结束,正常走 PROBE
- **事务包装**：`_persist_speed_results` 使用 SQLite 事务确保数据一致性(修复 v1.1 第二轮审查 P1-②)
  - 三步写入(快照→精华→掌握项)在同一事务中
  - 任何步骤失败自动回滚，不会写入部分数据
- **统一章节保存**：`_save_chapters_to_db()` 消除 `run_chapter_generation` 和 `generate_chapters` 重复逻辑(修复 v1.1 第二轮审查 P2)
- **syllabus 真实 chapter_index (commit `fc35da8`)** —— `generate_syllabus_items` 支持 `(chapter_index, title, content)` 三元组优先用真实 idx(向后兼容二元组走 enumerate);`api_load_chapter_content` 加载单章节时传三元组 + 先 DELETE 该章节旧 syllabus,避免反复加载累积

## 关键模块速查

| 模块 | 入口函数 | 失败兜底 |
|---|---|---|
| `chunker.extract_text` | 按 `source_type` 分发 | 返回空字符串 |
| `chunker.smart_chunk` | TOC-First,三级回退 | 返回 `[]` |
| `state_machine.DialogueStateMachine.next` | 状态流转 | 回到 INIT |
| `llm_client.llm.chat_json` | httpx 异步调用 | 返回 `{}` |
| `db.get_course` | SQL 查询 | 返回 `None` |

## 已知技术债

1. **`async_tasks` 内存 dict** —— 服务重启会丢任务,未做 DB 持久化(**已修复**：`run_chapter_generation` 增加 `finally` 清理; **v1.1.3** 新增 asyncio.Lock + threading.Lock 双重并发保护)
2. **`app.py` 单文件** ———— **v1.1.3 已部分解决**: 路由已拆至 `routes/` 四个模块(`task_routes.py`/`course_routes.py`/`chat_routes.py`/`defense_settings.py`)。`app.py` 仍保留原有函数定义以确保向后兼容,后续可逐步迁移路由注册。
3. **测试覆盖率 0%** —— P0 模块(chunkier / state_machine)优先补(**已更新**：v1.1 第二轮审查新增 `test_utc_format.py`, `test_save_chapters.py`, `test_generate_chapters_structure.py`; **v1.1.3** 243 测试全部通过)
4. **PDF 导入未支持** —— `app.py` 显式 400 拒绝,产品决策
5. **多用户未支持** —— SQLite 单文件,适合单机;要做多用户要换 Postgres
6. **WebSocket 会话清理** —— 客户端异常断开可能导致僵尸会话(**待修复**：v1.1 第二轮审查 P1-⑤)
7. **LLM 配置同步分散** —— startup / `_sync_llm_from_db` / `update_llm_settings` 多处重复(**待修复**：v1.1 第二轮审查 P2-⑧)
8. **`_get_course_group_chats` SQL bug (v1.3.1 已修复)** —— 历史 SQL 写 `s.created_at` 但 sessions 表只有 `started_at`,导致 `get_course_overview` 抛 `OperationalError`。原 bug 长期存在但未触发(无群聊数据时不会调用)。commit `ee0c02a` 修复。
9. **syllabus_items 写入错位的 chapter_index (commit `fc35da8` 已修复)** —— 当 chapters 表的 idx 非 0-based 连续整数(如 EPUB 抽出来的 4~15),`generate_syllabus_items` / `_add_default_syllabus_items` 用 `enumerate(chapters)` 顺序 idx 写入,导致 syllabus 全部打到 ch 0(不存在的章节);`api_load_chapter_content` 又没 DELETE 旧 syllabus,反复加载会越积越多。结果是 PROBE 阶段按当前章节取 syllabus 取不到 → LLM 失去 syllabus 引导 → 不同课程的对话题目变得相似/重复。修复:`generate_syllabus_items` / `_add_default_syllabus_items` 支持 `(chapter_index, title, content)` 三元组优先用真实 idx;`api_load_chapter_content` 传三元组 + 加载前 DELETE 该章节旧 syllabus。新增 `tests/test_chunker_syllabus.py` 覆盖 8 个场景。

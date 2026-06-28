# Wenqu 架构说明

> 适用范围: 问渠 (Wenqu) v1.1 全栈(FastAPI + SQLite + DeepSeek + 原生 JS 前端)

## 模块依赖图

```
app.py (FastAPI 路由 + 异步任务 + WebSocket)
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

## 关键模块速查

| 模块 | 入口函数 | 失败兜底 |
|---|---|---|
| `chunker.extract_text` | 按 `source_type` 分发 | 返回空字符串 |
| `chunker.smart_chunk` | TOC-First,三级回退 | 返回 `[]` |
| `state_machine.DialogueStateMachine.next` | 状态流转 | 回到 INIT |
| `llm_client.llm.chat_json` | httpx 异步调用 | 返回 `{}` |
| `db.get_course` | SQL 查询 | 返回 `None` |

## 已知技术债

1. **`async_tasks` 内存 dict** —— 服务重启会丢任务,未做 DB 持久化
2. **`app.py` 105KB 单文件** —— 路由 + 业务逻辑混杂,建议拆 `routers/`
3. **测试覆盖率 0%** —— P0 模块(chunkier / state_machine)优先补
4. **PDF 导入未支持** —— `app.py` 显式 400 拒绝,产品决策
5. **多用户未支持** —— SQLite 单文件,适合单机;要做多用户要换 Postgres

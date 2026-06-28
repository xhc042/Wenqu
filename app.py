"""
问渠（Wenqu）v1.1 主应用
FastAPI Web服务器 + API路由 + WebSocket对话
"""

import json
import os
import asyncio
import uuid
import io
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import aiofiles

from config import (
    HOST, PORT, STATIC_DIR, DATA_DIR, ROLES_META,
    DEFAULT_SLIDERS, DEPTH_CONFIG, DURATION_OPTIONS,
    ROLE_RECOMMENDATION, DEFAULT_ROLES, LLM_CONFIG, VERSION,
    READING_MODE_CONFIG, DEFAULT_READING_MODE,
    MODEL_TIER_CONFIG, DEFAULT_MODEL_TIER,
)
from database import init_db
import database as db
from chunker import (
    extract_text, smart_chunk, generate_syllabus_items, generate_course_summary,
    extract_chapter_snapshot, extract_chapter_snapshots_batch,
    generate_global_highlights,
    # generate_speed_read_syllabus 在 v1.1.2 已被 key_points 替代（P1-①），
    # v1.2.0 标记 deprecated，v1.3.0 移除
)
from state_machine import DialogueStateMachine
from llm_client import llm

# ==================== 异步任务管理系统 ====================
# 任务状态枚举
TASK_STATUS = {
    "PENDING": "pending",
    "PROCESSING": "processing",
    "COMPLETED": "completed",
    "FAILED": "failed",
}

# 内存任务存储: {task_id: {"status", "progress", "steps", "current_step", "course_id", "error"}}
async_tasks: dict[str, dict] = {}

def _utc(dt):
    if not dt or not isinstance(dt, str) or not dt.strip():
        return dt
    return (dt + 'Z') if not dt.endswith('Z') else dt

def _utc_dict(d, field):
    if not d:
        return d
    r = dict(d)
    if field in r and r[field]:
        r[field] = _utc(r[field])
    return r

def _utc_list(items, field):
    return [_utc_dict(item, field) for item in items] if items else []


def _get_course_group_chats(course_id: str) -> list:
    """获取课程所有群聊记录（按课程ID查询）

    v1.3.1 修复: sessions 表的列名是 started_at(不是 created_at),
              原 SQL 会导致 sqlite3.OperationalError: no such column: s.created_at
    """
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT gc.*, s.chapter_index, s.started_at as session_created_at "
            "FROM group_chats gc "
            "LEFT JOIN sessions s ON gc.session_id = s.id "
            "WHERE gc.course_id=? "
            "ORDER BY gc.created_at DESC LIMIT 20",
            (course_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def _run_speed_mode_postprocess(
    course_id: str,
    chapters: list,
    chapter_titles: List[str],
    source_type: str,
    source_path: str,
    concurrency: int = 3,
) -> dict:
    """
    speed 模式后处理（修复 P0-①：消除 3 处重复代码）

    流程：
    1. 准备 (chapter_idx, title, content) 三元组
    2. 并发生成快照（P0-② extract_chapter_snapshots_batch）
    3. 生成全局精华（generate_global_highlights）
    4. 生成精华掌握项（generate_speed_read_syllabus，v1.1.2 替换为 key_points）
    5. fallback（P1-④）：syllabus 为空时用 snapshot.learning_goal 兜底

    Returns:
        {
            "snapshots": List[dict],
            "snapshots_by_idx": Dict[int, dict],
            "highlights": dict,
            "syllabus_items": List[Tuple[int, str]],
        }
    """
    import asyncio
    import logging
    logger = logging.getLogger(__name__)

    # --- Step 1: 准备 chapter_idx/content/title ---
    chapter_data: List[Tuple[int, str, str]] = []
    for chapter_item in chapters:
        if isinstance(chapter_item, tuple) and len(chapter_item) >= 3:
            title, content, meta = chapter_item[0], chapter_item[1], chapter_item[2]
            chapter_idx = meta.get("idx", 0)
        elif isinstance(chapter_item, tuple) and len(chapter_item) >= 2:
            title, content = chapter_item[0], chapter_item[1]
            chapter_idx = 0
        else:
            continue
        chapter_data.append((chapter_idx, title, content))

    # --- Step 2: 并发生成快照（P0-②）---
    snapshots: List[dict] = []
    snapshot_by_idx: Dict[int, dict] = {}

    # 2a. 对 EPUB 场景：每章用完整内容（不限首尾）作为快照输入
    if source_type == "epub" and source_path:
        from chunker import extract_toc_from_epub, extract_chapter_content_by_href
        try:
            toc_items, _ = await extract_toc_from_epub(source_path)
            href_by_idx = {t.get("idx"): t.get("href", "") for t in toc_items}
        except Exception as e:
            logger.warning(f"[speed] TOC 提取失败，回退到 chapters 内容: {e}")
            href_by_idx = {}

        chapters_for_snapshot = []
        for ch_idx, title, content in chapter_data:
            href = href_by_idx.get(ch_idx, "")
            snapshot_content = content
            if href:
                try:
                    full = await extract_chapter_content_by_href(source_path, href)
                    if full:
                        snapshot_content = full
                except Exception as e:
                    logger.warning(f"[speed] EPUB 内容提取失败 [{title[:20]}]: {e}")
            chapters_for_snapshot.append((ch_idx, title, snapshot_content))

        try:
            snapshot_by_idx = await extract_chapter_snapshots_batch(
                chapters_for_snapshot, concurrency=concurrency,
            )
        except Exception as e:
            logger.warning(f"[speed] 批量快照失败，回退串行: {e}")
            snapshot_by_idx = {}

        if not snapshot_by_idx:
            # 回退：同步串行
            for ch_idx, title, snapshot_content in chapters_for_snapshot:
                if not snapshot_content:
                    continue
                try:
                    snap = await extract_chapter_snapshot(snapshot_content, title)
                    snapshot_by_idx[ch_idx] = snap
                except Exception as e:
                    logger.warning(f"[speed] 串行快照失败 [{title[:20]}]: {e}")
    else:
        # 非 EPUB：用 chapters 里的 content
        try:
            snapshot_by_idx = await extract_chapter_snapshots_batch(
                chapter_data, concurrency=concurrency,
            )
        except Exception as e:
            logger.warning(f"[speed] 批量快照失败，回退串行: {e}")
            snapshot_by_idx = {}

        if not snapshot_by_idx:
            for ch_idx, title, content in chapter_data:
                if not content:
                    continue
                try:
                    snap = await extract_chapter_snapshot(content, title)
                    snapshot_by_idx[ch_idx] = snap
                except Exception as e:
                    logger.warning(f"[speed] 串行快照失败 [{title[:20]}]: {e}")

    snapshots = list(snapshot_by_idx.values())

    # --- Step 3: 全局精华 ---
    highlights: dict = {}
    if snapshots:
        try:
            highlights = await generate_global_highlights(course_id, snapshots, chapter_titles)
        except Exception as e:
            logger.warning(f"[speed] 全局精华生成失败: {e}")
            highlights = {}

    # --- Step 4: 精华掌握项（P1-① v1.1.2：用 global_highlights.key_points 替代 generate_speed_read_syllabus）---
    # 原因：
    #   1. `key_points` 已经是"全书 5-10 个核心知识点"，再调一次 LLM 冗余
    #   2. generate_speed_read_syllabus 有 `chapter_info[:20]` 截断，长书后 N 章不进 prompt
    #   3. key_points 路径节省 1 次 LLM 调用 + token 消耗
    # v1.3 P0-③: 写入 importance 字段（来自 key_points 排序，1-5）
    #   - key_points 越靠前越核心 → importance 越高
    #   - 5-10 个 key_points 映射到 3-5 重要度（前 3 条 = 5，最后几条 = 3）
    syllabus_items: List[Tuple[int, str, int]] = []
    key_points = highlights.get("key_points", []) if isinstance(highlights, dict) else []

    if key_points:
        # 按 snapshot.importance 降序排序，importance 高的章节分配更多 key_points
        sorted_snaps = sorted(
            snapshot_by_idx.items(),
            key=lambda kv: kv[1].get("importance", 3),
            reverse=True,
        )
        # 计算 importance 梯度：kps 总数 N,前 ceil(N*0.3) 条 = 5,中间 = 4,后 = 3
        kp_count = min(len(key_points), 15)
        def _calc_importance(pos: int, total: int) -> int:
            """前 30% → 5,中间 40% → 4,后 30% → 3"""
            if total <= 0:
                return 3
            if pos < max(1, total * 0.3):
                return 5
            if pos < total * 0.7:
                return 4
            return 3

        # round-robin 分配：每个 key_point 分配到当前 importance 最高的章节
        for i, kp in enumerate(key_points[:kp_count]):
            if sorted_snaps:
                target_ch_idx = sorted_snaps[i % len(sorted_snaps)][0]
            else:
                target_ch_idx = 0
            importance = _calc_importance(i, kp_count)
            syllabus_items.append((target_ch_idx, kp, importance))

    # --- Step 5: fallback（P1-④）---
    if not syllabus_items and snapshots:
        for ch_idx, snap in snapshot_by_idx.items():
            goal = (snap.get("learning_goal", "") or "").strip()
            if goal:
                syllabus_items.append((ch_idx, goal, 3))
            if len(syllabus_items) >= 10:
                break

    return {
        "snapshots": snapshots,
        "snapshots_by_idx": snapshot_by_idx,
        "highlights": highlights,
        "syllabus_items": syllabus_items,
    }


async def _persist_speed_results(course_id: str, result: dict) -> None:
    """
    把 speed 模式后处理结果写入数据库（独立函数便于测试）
    修复 P0-①：抽离 DB 写入逻辑
    修复 P1-②：使用事务包装，确保数据一致性
    
    如果中途失败，整个事务回滚，不会写入部分数据
    """
    import json
    import logging
    logger = logging.getLogger(__name__)

    snapshots_by_idx = result.get("snapshots_by_idx", {})

    # 使用数据库事务确保原子性
    conn = db.get_conn()
    try:
        # 启用外键约束和事务
        conn.execute("BEGIN TRANSACTION")
        
        try:
            # 1. 写 chapter_snapshots
            for ch_idx, snapshot in snapshots_by_idx.items():
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO chapter_snapshots 
                           (course_id, chapter_index, keywords, core_viewpoint, global_priority, learning_goal, importance, difficulty)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            course_id, ch_idx,
                            json.dumps(snapshot.get("keywords", []), ensure_ascii=False),
                            snapshot.get("core_viewpoint", ""),
                            snapshot.get("importance", 0),
                            snapshot.get("learning_goal", ""),
                            snapshot.get("importance", 0),
                            snapshot.get("difficulty", ""),
                        )
                    )
                except Exception as e:
                    logger.warning(f"[speed] 写 chapter_snapshot 失败 [{ch_idx}]: {e}")

            # 2. 写 global_highlights
            highlights = result.get("highlights", {}) or {}
            if highlights:
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO global_highlights 
                           (course_id, key_points, chapter_priorities, relationships, core_chapter_indices, chapter_dependencies)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            course_id,
                            json.dumps(highlights.get("key_points", []), ensure_ascii=False),
                            json.dumps(highlights.get("chapter_priorities", []), ensure_ascii=False),
                            highlights.get("relationships", ""),
                            json.dumps(highlights.get("core_chapter_indices", []), ensure_ascii=False),
                            json.dumps(highlights.get("chapter_dependencies", {}), ensure_ascii=False),
                        )
                    )
                except Exception as e:
                    logger.warning(f"[speed] 写 global_highlights 失败: {e}")

            # 3. 删旧 syllabus_items + 写新的
            # v1.3 P0-③: 写入 importance 字段（来自 key_points 排序）
            syllabus_items = result.get("syllabus_items", [])
            if syllabus_items:
                try:
                    conn.execute("DELETE FROM syllabus_items WHERE course_id=?", (course_id,))
                    for item in syllabus_items:
                        # 兼容老格式 (ch_idx, desc) 和新格式 (ch_idx, desc, importance)
                        if len(item) == 3:
                            ch_idx, desc, importance = item
                        else:
                            ch_idx, desc = item[0], item[1]
                            importance = 3
                        conn.execute(
                            "INSERT INTO syllabus_items (course_id, chapter_index, description, importance) VALUES (?, ?, ?, ?)",
                            (course_id, ch_idx, desc, importance)
                        )
                except Exception as e:
                    logger.warning(f"[speed] 写 syllabus_items 失败: {e}")

            # 所有操作成功，提交事务
            conn.commit()
        except Exception as e:
            # 任何错误都回滚
            conn.rollback()
            logger.error(f"[speed] 事务失败，已回滚: {e}")
            raise
        
    finally:
        conn.close()


def _save_chapters_to_db(course_id: str, chapters: list, reading_mode: str) -> list:
    """
    保存章节到数据库（统一入口，消除重复逻辑）
    
    修复 P2：从 run_chapter_generation 和 generate_chapters 中抽离
    
    Returns:
        chapter_titles: 章节标题列表
    """
    is_speed = reading_mode == "speed"
    max_loaded = 3 if reading_mode == "deep" else 99
    
    chapter_titles = []
    for idx, chapter_item in enumerate(chapters):
        if isinstance(chapter_item, tuple) and len(chapter_item) >= 3:
            title, content = chapter_item[0], chapter_item[1]
            meta = chapter_item[2] if len(chapter_item) > 2 else {}
        elif isinstance(chapter_item, tuple):
            title, content = chapter_item[0], chapter_item[1]
            meta = {}
        else:
            title, content = str(chapter_item), ""
            meta = {}
        
        chapter_titles.append(title)
        
        if is_speed:
            is_loaded = 0
            # speed 模式下，chapters 列表里 content 已经过 _sample_first_last(≤600字) 采样
            # 此处保留 500 字截断以确保 DB content_slice ≤ 600 字（验收标准）
            content_short = content[:500] if content else ""
        else:
            is_loaded = 1 if idx < max_loaded else 0
            content_short = content[:5000] if content else ""
        
        db.add_chapter(
            course_id=course_id,
            idx=meta.get("idx", idx),
            title=title,
            content_slice=content_short,
            summary="",
            content_full=content if is_loaded else "",
            is_loaded=is_loaded,
            parent_idx=meta.get("parent_idx", -1),
            level=meta.get("level", 0),
            sort_order=meta.get("sort_order", str(idx)),
        )
    
    return chapter_titles


def _add_default_syllabus_items(course_id: str, chapters: list):
    """生成默认掌握项（当LLM生成失败时使用）"""
    defaults = [
        "能用自己的话复述本章的核心观点",
        "能解释本章涉及的关键概念",
        "能用自己的话举例说明本章内容",
    ]
    for idx, (title, _) in enumerate(chapters):
        for desc in defaults:
            db.add_syllabus_item(course_id, idx, desc)


# ==================== 初始化 ====================
app = FastAPI(title=f"问渠（Wenqu）v{VERSION}", version=VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 挂载静态文件 ====================
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ==================== 事件 ====================
@app.on_event("startup")
async def startup():
    init_db()
    # 确保数据目录存在
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)

    # 从数据库同步活跃的 LLM 配置到内存
    active = db.get_active_model_with_provider()
    if active:
        LLM_CONFIG["api_key"] = active["api_key"]
        LLM_CONFIG["base_url"] = active["base_url"]
        LLM_CONFIG["model"] = active["model_name"]
        llm.api_key = active["api_key"]
        llm.base_url = active["base_url"].rstrip("/")
        llm.model = active["model_name"]
    else:
        # 数据库无配置（首次运行或迁移），检查是否需要从旧配置迁移
        providers = db.get_all_llm_providers()
        if not providers:
            fallback = LLM_CONFIG.get("base_url", "")
            if fallback and fallback != "https://api.deepseek.com":
                # 用户曾通过 env var 或旧 modal 配置过，迁移到数据库
                name = "默认提供商"
                pid = db.add_llm_provider(name, fallback, LLM_CONFIG.get("api_key", ""))
                db.add_llm_model(pid, LLM_CONFIG.get("model", "deepseek-chat"))
                db.set_active_provider(pid)
                # 重新读取一次
                active = db.get_active_model_with_provider()
                if active:
                    llm.api_key = active["api_key"]
                    llm.base_url = active["base_url"].rstrip("/")
                    llm.model = active["model_name"]


# ==================== 前端路由 ====================
@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
        content = content.replace("{{VERSION}}", VERSION)
        return HTMLResponse(content=content, media_type="text/html; charset=utf-8")
    return HTMLResponse(
        content=f"<h1>问渠 v{VERSION}</h1><p>前端页面未找到，请确保static/index.html存在。</p>",
        media_type="text/html; charset=utf-8"
    )


@app.get("/favicon.ico")
async def favicon():
    """返回内嵌的SVG favicon，避免404"""
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
        <text x="50%" y="50%" font-size="48" text-anchor="middle" dominant-baseline="central">🌊</text>
    </svg>'''
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": VERSION}


# ==================== 异步任务管理 ====================
@app.post("/api/tasks/chapters-generate")
async def create_chapter_generation_task(data: dict):
    """创建分章任务（异步）"""
    course_id = data.get("course_id")
    if not course_id:
        raise HTTPException(400, "缺少course_id")
    
    task_id = str(uuid.uuid4())[:8]
    async_tasks[task_id] = {
        "task_id": task_id,
        "course_id": course_id,
        "type": "chapters_generate",
        "status": TASK_STATUS["PENDING"],
        "progress": 0,
        "steps": [
            {"name": "正在智能分章...", "status": "pending", "detail": ""},
            {"name": "生成教学大纲", "status": "pending", "detail": ""},
            {"name": "完成", "status": "pending", "detail": ""},
        ],
        "current_step": 0,
        "result": None,
        "error": None,
        "created_at": datetime.now().isoformat(),
    }
    
    # 启动后台任务
    asyncio.create_task(run_chapter_generation(task_id))
    
    return {"task_id": task_id}


async def run_chapter_generation(task_id: str):
    """后台执行分章和大纲生成的异步任务"""
    task = async_tasks.get(task_id)
    if not task:
        return
    
    try:
        task["status"] = TASK_STATUS["PROCESSING"]
        task["steps"][0]["status"] = "processing"
        task["current_step"] = 0
        
        course_id = task["course_id"]
        course = db.get_course(course_id)
        if not course:
            raise Exception("课程不存在")
        
        source_path = course.get("source_path", "")
        source_type = course.get("source_type", "")
        reading_mode = course.get("reading_mode", "standard")
        is_speed = reading_mode == "speed"
        
        # 步骤1: 智能分章
        task["steps"][0]["detail"] = "正在分析文本结构..."
        actual_type = source_type
        if source_type == "text":
            actual_type = "txt"
        text = await extract_text(source_path, actual_type)
        
        if not text:
            raise Exception("无法提取文本内容")
        
        task["steps"][0]["detail"] = "正在智能分章..."
        chapters = await smart_chunk(text, source_type=source_type, file_path=source_path if source_type == "epub" else "", reading_mode=reading_mode)
        task["steps"][0]["status"] = "done"
        task["steps"][0]["detail"] = f"分章完成，共{len(chapters)}章"
        task["progress"] = 30
        
        # 保存章节到数据库（使用统一函数）
        chapter_titles = _save_chapters_to_db(course_id, chapters, reading_mode)
        
        db.add_learning_event(course_id, "lesson_end", {"action": "chapters_generated", "count": len(chapters)})
        
        # 速读模式额外处理（P0-①：抽离为独立函数，3 处复用同一份逻辑）
        if is_speed:
            task["steps"][0]["detail"] = "正在生成知识快照..."
            try:
                result = await _run_speed_mode_postprocess(
                    course_id, chapters, chapter_titles,
                    source_type, source_path, concurrency=3,
                )
                await _persist_speed_results(course_id, result)
                task["steps"][0]["detail"] = (
                    f"快照 {len(result['snapshots'])} 章，"
                    f"掌握项 {len(result['syllabus_items'])} 条"
                )
            except Exception as e:
                import logging
                logging.warning(f"速读模式快照生成失败: {e}")
        
        task["progress"] = 50
        
        # 步骤2: 生成教学大纲（非速读模式）
        if reading_mode != "speed":
            task["steps"][1]["status"] = "processing"
            task["current_step"] = 1
            task["steps"][1]["detail"] = "正在生成掌握项..."
            
            chapters_db = db.get_chapters(course_id)
            ch_list = [(ch["title"], ch.get("content_slice", "")) for ch in chapters_db if ch.get("is_loaded", 1)]
            
            if ch_list:
                # 先删除旧知识点，防止重复追加（任务重跑时避免叠加）
                _conn = db.get_conn()
                try:
                    _conn.execute("DELETE FROM syllabus_items WHERE course_id=?", (course_id,))
                    _conn.commit()
                finally:
                    _conn.close()

                try:
                    items = await generate_syllabus_items(course_id, ch_list)
                    if items:
                        for chapter_index, description in items:
                            db.add_syllabus_item(course_id, chapter_index, description)
                        task["steps"][1]["status"] = "done"
                        task["steps"][1]["detail"] = f"大纲生成完成，共{len(items)}个掌握项"
                    else:
                        # LLM返回空，使用默认掌握项
                        import logging
                        logging.warning(f"[异步任务 {task_id}] LLM返回空掌握项，使用默认值")
                        _add_default_syllabus_items(course_id, ch_list)
                        task["steps"][1]["status"] = "done"
                        task["steps"][1]["detail"] = "大纲生成完成（使用默认项）"
                except Exception as e:
                    import logging
                    logging.error(f"[异步任务 {task_id}] 掌握项生成失败: {e}", exc_info=True)
                    # 失败时使用默认掌握项确保课程可用
                    _add_default_syllabus_items(course_id, ch_list)
                    task["steps"][1]["status"] = "done"
                    task["steps"][1]["detail"] = "大纲生成完成（使用默认项）"
            else:
                task["steps"][1]["status"] = "done"
                task["steps"][1]["detail"] = "无已加载章节"
            
            task["progress"] = 80
        else:
            task["steps"][1]["status"] = "done"
            task["steps"][1]["detail"] = "速读模式跳过"
            task["progress"] = 80
        
        # 步骤3: 完成
        task["steps"][2]["status"] = "done"
        task["steps"][2]["detail"] = "全部完成"
        task["status"] = TASK_STATUS["COMPLETED"]
        task["progress"] = 100
        
    except Exception as e:
        import logging
        logging.error(f"异步任务 {task_id} 失败: {e}", exc_info=True)
        task["status"] = TASK_STATUS["FAILED"]
        task["error"] = str(e)
        if task["steps"]:
            task["steps"][task["current_step"]]["status"] = "error"
            task["steps"][task["current_step"]]["detail"] = f"失败: {str(e)[:50]}"


@app.get("/api/tasks/{task_id}")
async def get_task_status(task_id: str):
    """查询异步任务状态"""
    task = async_tasks.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    
    return {
        "task_id": task["task_id"],
        "course_id": task["course_id"],
        "type": task["type"],
        "status": task["status"],
        "progress": task["progress"],
        "steps": task["steps"],
        "current_step": task["current_step"],
        "error": task.get("error"),
        "result": task.get("result"),
        "created_at": task.get("created_at"),
    }


@app.delete("/api/tasks/{task_id}")
async def cancel_task(task_id: str):
    """取消异步任务"""
    task = async_tasks.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    
    if task["status"] in [TASK_STATUS["COMPLETED"], TASK_STATUS["FAILED"]]:
        return {"status": "already_finished"}
    
    task["status"] = TASK_STATUS["FAILED"]
    task["error"] = "用户取消"
    return {"status": "cancelled"}


# ==================== 课程管理 ====================
@app.get("/api/courses")
async def list_courses():
    """获取所有课程"""
    courses = db.get_all_courses()
    for c in courses:
        progress = db.get_syllabus_progress(c["id"])
        c["progress"] = progress
    return courses


@app.post("/api/courses")
async def create_course(data: dict):
    """创建课程（支持file/url/text/recommendation）"""
    source_type = data.get("source_type", "text")
    source_path = data.get("source_path", "")
    content_text = data.get("content_text", "")
    reading_mode = data.get("reading_mode", DEFAULT_READING_MODE)

    # 如果没有传入 title，尝试从 source_path 提取书名
    title = data.get("title", "")
    if not title and source_path:
        # 优先取文件名（去掉扩展名）作为书名
        if source_type == "upload" or "/" in source_path or "\\" in source_path:
            import os
            filename = os.path.basename(source_path)
            # 去掉常见扩展名
            for ext in [".txt", ".md", ".markdown", ".epub"]:
                if filename.lower().endswith(ext):
                    title = filename[:-len(ext)]
                    break
            if not title:
                title = filename
        elif source_type == "url":
            # 从 URL 中提取文件名或域名作为书名
            try:
                from urllib.parse import urlparse
                parsed = urlparse(source_path)
                path = parsed.path.strip("/")
                if path:
                    fname = os.path.basename(path)
                    for ext in [".txt", ".md", ".epub"]:
                        if fname.lower().endswith(ext):
                            title = fname[:-len(ext)]
                            break
                    else:
                        title = fname
                if not title:
                    title = parsed.netloc or "网页内容"
            except Exception:
                title = "网页内容"
        elif source_type == "text" and content_text:
            # 从文本内容提取标题（取第一行或前50字）
            lines = content_text.strip().split("\n")
            first_line = lines[0].strip() if lines else ""
            title = first_line[:50] if first_line else "文本文档"

    # 确保 title 有值
    if not title:
        title = "未命名课程"

    course_id = db.create_course(title, source_type, source_path, reading_mode)
    db.add_learning_event(course_id, "login", {"action": "create_course"})

    # 阅读模式映射到认知深度
    depth_map = {"speed": "basic", "standard": "standard", "deep": "deep"}
    db.update_course_depth(course_id, depth_map.get(reading_mode, "standard"))

    # 如果是文本粘贴，直接保存内容并更新source_path
    uploaded_path = ""
    if source_type == "text" and content_text:
        text_path = DATA_DIR / "uploads" / f"{course_id}_text.txt"
        async with aiofiles.open(text_path, "w", encoding="utf-8") as f:
            await f.write(content_text)
        uploaded_path = str(text_path)
        # 更新课程的source_path，使得分章引擎能找到内容
        import database as db_crud
        conn = db_crud.get_conn()
        try:
            conn.execute("UPDATE courses SET source_path=? WHERE id=?", (uploaded_path, course_id))
            conn.commit()
        finally:
            conn.close()

    # 如果有内容来源，自动启动异步分章任务
    task_id = None
    if source_type != "recommendation" and source_path:
        task_id = str(uuid.uuid4())[:8]
        async_tasks[task_id] = {
            "task_id": task_id,
            "course_id": course_id,
            "type": "chapters_generate",
            "status": TASK_STATUS["PENDING"],
            "progress": 0,
            "steps": [
                {"name": "正在智能分章...", "status": "pending", "detail": ""},
                {"name": "生成教学大纲", "status": "pending", "detail": ""},
                {"name": "完成", "status": "pending", "detail": ""},
            ],
            "current_step": 0,
            "result": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
        }
        # 启动后台任务
        import logging
        logging.info(f"[异步任务] 创建任务 {task_id} 用于课程 {course_id}")
        asyncio.create_task(run_chapter_generation(task_id))

    result = {"course_id": course_id, "title": title, "source_type": source_type}
    if task_id:
        result["task_id"] = task_id
    return result


@app.post("/api/courses/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传文件"""
    # 判断文件类型
    ext = Path(file.filename).suffix.lower()
    type_map = {
        ".pdf": "pdf", ".epub": "epub",
        ".md": "md", ".markdown": "md",
        ".txt": "txt",
    }
    source_type = type_map.get(ext, "txt")

    # 保存文件
    # PDF 导入暂不支持
    if source_type == "pdf":
        raise HTTPException(400, "暂不支持 PDF 格式导入，请使用 EPUB / MD / TXT 格式。")

    file_path = DATA_DIR / "uploads" / f"{uuid.uuid4().hex}{ext}"
    content = await file.read()
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    return {
        "filename": file.filename,
        "source_type": source_type,
        "file_path": str(file_path),
    }


@app.get("/api/courses/{course_id}")
async def get_course(course_id: str):
    """获取课程详情"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")
    chapters = db.get_chapters(course_id)
    syllabus = db.get_syllabus_items(course_id)
    progress = db.get_syllabus_progress(course_id)
    profile = db.get_profile(course_id)
    affinities = db.get_all_affinities(course_id)
    certificates = db.get_certificates(course_id)
    diaries = _utc_list(db.get_diaries(course_id), "created_at")
    summaries = _utc_list(db.get_summaries(course_id), "created_at")
    sessions = _utc_list(db.get_sessions(course_id), "started_at")
    stats = db.get_course_learning_stats(course_id)

    # 查询该课程的活跃分章任务
    active_task = None
    for task_id, task in async_tasks.items():
        if task.get("course_id") == course_id and task.get("status") in ["pending", "processing"]:
            active_task = {
                "task_id": task["task_id"],
                "type": task["type"],
                "status": task["status"],
                "progress": task["progress"],
                "steps": task["steps"],
                "current_step": task["current_step"],
                "error": task.get("error"),
            }
            break

    # ?????????????
    all_group_chats = []
    for s in sessions:
        chats = db.get_group_chats(s["id"])
        all_group_chats.extend(chats)
    all_group_chats = _utc_list(all_group_chats, "created_at")

    return {
        "course": _utc_dict(course, "created_at"),
        "chapters": _utc_list(chapters, "created_at"),
        "syllabus": syllabus,
        "progress": progress,
        "profile": profile,
        "affinities": affinities,
        "certificates": _utc_list(certificates, "issued_at"),
        "diaries": diaries,
        "summaries": summaries,
        "group_chats": all_group_chats,
        "sessions_count": len(sessions),
        "learning_stats": stats,
        "active_task": active_task,  # 活跃的分章任务
    }


@app.delete("/api/courses/{course_id}")
async def remove_course(course_id: str):
    db.delete_course(course_id)
    return {"status": "deleted"}


# ==================== 分章 ====================
@app.post("/api/courses/{course_id}/chapters/generate")
async def generate_chapters(course_id: str):
    """TOC-First 分章（异步执行），根据阅读模式调整处理深度"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    source_path = course.get("source_path", "")
    source_type = course.get("source_type", "")
    reading_mode = course.get("reading_mode", "standard")

    # 提取文本
    actual_type = source_type
    if source_type == "text":
        actual_type = "txt"
    text = await extract_text(source_path, actual_type)

    if not text:
        raise HTTPException(400, "无法提取文本内容")

    # 智能分章（传入 source_type, file_path, reading_mode 以便 TOC-First 路径）
    chapters = await smart_chunk(text, source_type=source_type, file_path=source_path if source_type == "epub" else "", reading_mode=reading_mode)

    # 保存章节到数据库（使用统一函数）
    chapter_titles = _save_chapters_to_db(course_id, chapters, reading_mode)

    db.add_learning_event(course_id, "lesson_end", {"action": "chapters_generated", "count": len(chapters)})

    # 获取章节索引映射（用于速读模式）
    is_speed = reading_mode == "speed"
    snapshot_map = {}
    core_indices = set()
    if is_speed:
        try:
            snapshots = db.get_chapter_snapshots(course_id)
            for s in snapshots:
                snapshot_map[s["chapter_index"]] = s
            
            # 获取核心章节索引
            highlights = db.get_global_highlights(course_id)
            if highlights:
                for s in highlights.get("core_chapter_indices", []):
                    import re as _re
                    m = _re.search(r'第(\d+)章', str(s))
                    if m:
                        core_indices.add(int(m.group(1)) - 1)
        except Exception:
            pass

    # 速读模式：额外生成知识快照和全局精华（P0-①：抽离为独立函数）
    if is_speed:
        import logging
        logging.info(f"[速读模式] 开始生成知识快照，共 {len(chapters)} 章")
        try:
            result = await _run_speed_mode_postprocess(
                course_id, chapters, chapter_titles,
                source_type, source_path, concurrency=3,
            )
            await _persist_speed_results(course_id, result)

            snapshots = result["snapshots"]
            highlights = result["highlights"]

            # 用新生成的 highlights 重新计算 core_indices（用于返回结构）
            new_core_indices = set()
            if isinstance(highlights, dict):
                import re as _re
                for s in highlights.get("core_chapter_indices", []):
                    m = _re.search(r'第(\d+)章', str(s))
                    if m:
                        new_core_indices.add(int(m.group(1)) - 1)
            if new_core_indices:
                core_indices = new_core_indices

            logging.info(
                f"[速读模式] 完成，快照 {len(snapshots)} 章，掌握项 {len(result['syllabus_items'])} 条"
            )
            # 统一返回结构：成功和失败使用相同的章节格式
            return {
                "total_chapters": len(chapters),
                "snapshots_generated": len(snapshots),
                "highlights_generated": bool(snapshots),
                "reading_mode": "speed",
                "core_chapter_indices": list(core_indices),
                "chapters": [
                    {
                        "idx": i,
                        "title": chapters[i][0] if isinstance(chapters[i], tuple) else str(chapters[i]),
                        "is_loaded": False,
                        "importance": snapshot_map.get(i, {}).get("importance", 0),
                        "keywords": snapshot_map.get(i, {}).get("keywords", []),
                        "core_viewpoint": snapshot_map.get(i, {}).get("core_viewpoint", ""),
                        "learning_goal": snapshot_map.get(i, {}).get("learning_goal", ""),
                        "is_core": i in core_indices,
                    }
                    for i in range(len(chapters))
                ],
            }
        except Exception as e:
            # 快照生成失败不影响主流程，使用相同结构返回
            import logging
            logging.warning(f"速读模式快照生成失败: {e}")
            import traceback
            logging.warning(traceback.format_exc())
            # 失败时也返回统一结构的章节列表
            return {
                "total_chapters": len(chapters),
                "snapshots_generated": 0,
                "highlights_generated": False,
                "reading_mode": "speed",
                "core_chapter_indices": [],
                "chapters": [
                    {
                        "idx": i,
                        "title": chapters[i][0] if isinstance(chapters[i], tuple) else str(chapters[i]),
                        "is_loaded": False,
                        "importance": 0,
                        "keywords": [],
                        "core_viewpoint": "",
                        "learning_goal": "",
                        "is_core": False,
                    }
                    for i in range(len(chapters))
                ],
            }

    return {
        "total_chapters": len(chapters),
        "reading_mode": course.get("reading_mode", "standard"),
        "chapters": [
            {"idx": c[2].get("idx", i), "title": c[0], "is_loaded": is_speed or i < max_loaded}
            if isinstance(c, tuple) and len(c) >= 3 else
            {"idx": i, "title": c[0] if isinstance(c, tuple) else str(c), "is_loaded": True}
            for i, c in enumerate(chapters)
        ],
    }


# ==================== 懒加载：按需加载章节内容 ====================
@app.post("/api/courses/{course_id}/chapters/{chapter_idx}/load")
async def load_chapter_content(course_id: str, chapter_idx: int):
    """懒加载：仅加载指定章节的全文和掌握项"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    chapter = db.get_chapter(course_id, chapter_idx)
    if not chapter:
        raise HTTPException(404, "章节不存在")

    # 已加载则直接返回
    if chapter.get("is_loaded"):
        return {"status": "already_loaded", "chapter": chapter}

    source_path = course.get("source_path", "")
    source_type = course.get("source_type", "")
    reading_mode = course.get("reading_mode", "standard")

    # 从 EPUB 按 href 提取正文（TOC-First 懒加载）
    full_content = ""
    if source_type == "epub" and source_path:
        from chunker import extract_chapter_content_by_href
        href = chapter.get("sort_order", "") or chapter.get("title", "")
        # 先尝试通过 idx 从 TOC 反查 href
        toc_items, _ = await _get_epub_toc(source_path)
        matched_href = None
        for item in toc_items:
            if item["idx"] == chapter_idx:
                matched_href = item.get("href", "")
                break
        if matched_href:
            full_content = await extract_chapter_content_by_href(source_path, matched_href)
        else:
            # 兜底：重新提取全本
            from chunker import extract_text_from_epub
            full_text = await extract_text_from_epub(source_path)

    # 如果是文本粘贴等非EPUB来源且已提取过全文，从原始文件重读
    if not full_content and source_path:
        from chunker import extract_text
        actual = "txt" if source_type == "text" else source_type
        full_text = await extract_text(source_path, actual)
        # 按章节标题切出相关内容
        full_content = _extract_chapter_from_text(full_text, chapter["title"], chapter_idx)

    if not full_content:
        full_content = chapter.get("content_slice", "") or "（内容未找到）"

    # 更新数据库
    from chunker import generate_syllabus_items
    content_short = full_content[:5000]
    db.update_chapter_content(course_id, chapter_idx, full_content)
    # 同时更新 content_slice
    conn = db.get_conn()
    try:
        conn.execute("UPDATE chapters SET content_slice=? WHERE course_id=? AND idx=?",
                     (content_short, course_id, chapter_idx))
        conn.commit()
    finally:
        conn.close()

    # 为此章节生成掌握项（懒加载触发首次生成）
    # 先删除该章节的旧知识点，避免重复追加
    _lc = db.get_conn()
    try:
        _lc.execute("DELETE FROM syllabus_items WHERE course_id=? AND chapter_index=?", (course_id, chapter_idx))
        _lc.commit()
    finally:
        _lc.close()

    items = await generate_syllabus_items(course_id,
                                          [(chapter["title"], full_content)])
    for ch_idx, desc in items:
        db.add_syllabus_item(course_id, ch_idx, desc)

    return {
        "status": "loaded",
        "chapter": {
            "idx": chapter_idx,
            "title": chapter["title"],
            "summary": "",
            "content_slice": content_short,
            "is_loaded": 1,
        },
        "syllabus_generated": len(items),
    }


async def _get_epub_toc(file_path: str) -> tuple:
    """辅助：只获取EPUB目录结构（不提取正文）"""
    from chunker import extract_toc_from_epub
    return await extract_toc_from_epub(file_path)


def _extract_chapter_from_text(full_text: str, chapter_title: str, chapter_idx: int) -> str:
    """从完整文本中按标题切出章节内容"""
    import re
    # 尝试用标题定位
    pattern = re.compile(re.escape(chapter_title), re.IGNORECASE)
    match = pattern.search(full_text)
    if match:
        start = match.start()
        # 找到下一个章节标题或末尾
        remaining = full_text[start + len(chapter_title):]
        # 找下一个看起来像章节标题的行
        next_ch = re.search(r'\n#{1,4}\s+|\n第[\d一二三四五六七八九十]+[章节]|\nChapter\s+\d+', remaining)
        end = start + len(chapter_title) + (next_ch.start() if next_ch else len(remaining))
        return full_text[start:end].strip()
    # 兜底：分段落取
    paragraphs = [p.strip() for p in full_text.split('\n\n') if p.strip()]
    chunk_size = len(paragraphs) // max(1, (chapter_idx + 1))
    start_para = chapter_idx * chunk_size
    end_para = start_para + chunk_size if chapter_idx < 9 else len(paragraphs)
    return '\n\n'.join(paragraphs[start_para:end_para])


# ==================== 手动生成掌握项 ====================
@app.post("/api/courses/{course_id}/syllabus/regenerate")
async def regenerate_syllabus(course_id: str):
    """
    手动重新生成掌握项（用于修复生成失败的课程）
    会先删除旧的掌握项，再重新生成
    """
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    chapters = db.get_chapters(course_id)
    if not chapters:
        raise HTTPException(400, "请先生成分章")

    # 删除旧掌握项
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM syllabus_items WHERE course_id=?", (course_id,))
        conn.commit()
    finally:
        conn.close()

    # 获取已加载的章节
    ch_list = [(ch["title"], ch.get("content_slice", "")) for ch in chapters if ch.get("is_loaded", 1)]
    
    if not ch_list:
        raise HTTPException(400, "无已加载章节")

    reading_mode = course.get("reading_mode", "standard")
    
    # 生成新掌握项
    if reading_mode == "speed":
        # 速读模式走快照路线
        return {"message": "速读模式使用快照生成，请先生成知识快照", "count": 0}
    else:
        items = await generate_syllabus_items(course_id, ch_list)
        if not items:
            # LLM失败，使用默认项
            _add_default_syllabus_items(course_id, ch_list)
            return {"message": "LLM生成失败，使用默认掌握项", "count": len(ch_list) * 3}
        
        for chapter_index, description in items:
            db.add_syllabus_item(course_id, chapter_index, description)
        return {"message": "掌握项生成完成", "count": len(items)}


@app.post("/api/courses/{course_id}/syllabus/generate")
async def generate_syllabus(course_id: str):
    """生成掌握项清单（根据阅读模式调整深度）"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    chapters = db.get_chapters(course_id)
    if not chapters:
        raise HTTPException(400, "请先生成分章")

    reading_mode = course.get("reading_mode", "standard")

    # 速读模式不生成掌握项
    if reading_mode == "speed":
        return {"total_items": 0, "message": "速读模式不生成掌握项"}

    # 只为已加载的章节生成掌握项
    ch_list = [(ch["title"], ch.get("content_slice", "")) for ch in chapters if ch.get("is_loaded", 1)]
    if not ch_list:
        return {"total_items": 0, "message": "无已加载章节，请先加载章节内容"}

    # 先删除旧知识点，避免重复追加
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM syllabus_items WHERE course_id=?", (course_id,))
        conn.commit()
    finally:
        conn.close()

    items = await generate_syllabus_items(course_id, ch_list)

    for chapter_index, description in items:
        db.add_syllabus_item(course_id, chapter_index, description)

    return {"total_items": len(items)}


# ==================== 苏格拉底预演 ====================
@app.get("/api/courses/{course_id}/chapters/{chapter_idx}/preview")
async def generate_next_preview(course_id: str, chapter_idx: int):
    """
    基于当前章节内容，为下一章生成苏格拉底式预演引导。
    学生在进入下一章前，会看到1-2个引导性问题，带着思考学习效率更高。
    """
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    next_chapter = db.get_chapter(course_id, chapter_idx + 1)
    if not next_chapter:
        return {"preview": "", "next_title": "", "questions": []}

    current_chapter = db.get_chapter(course_id, chapter_idx)
    if not current_chapter:
        return {"preview": "", "next_title": next_chapter.get("title", ""), "questions": []}

    current_content = current_chapter.get("content_slice", "")
    if not current_content:
        return {"preview": "", "next_title": next_chapter.get("title", ""), "questions": []}

    # 生成引导性问题
    prompt = f"""你是苏格拉底式学习引导者。

当前章节内容摘要：
{current_content[:1000]}

下一章节标题：{next_chapter['title']}

请基于当前章节讲过的内容，为下一章生成 1-2 个精巧的引导性问题。
学生带着这些问题进入下一章，效率会更高。
问题要具体、有穿透力，指向下一章的核心差异或深化方向。
不要问"你觉得下一章会讲什么"这类泛泛的问题。

返回 JSON：
{{"questions": ["问题1", "问题2"]}}
"""
    try:
        result = await llm.chat_json([
            {"role": "system", "content": "你是苏格拉底式学习引导者。返回JSON。"},
            {"role": "user", "content": prompt},
        ])
        questions = result.get("questions", [])
        if not questions or not isinstance(questions, list):
            questions = []
    except Exception as e:
        import logging
        logging.warning(f"苏格拉底预演生成失败: {e}")
        questions = []

    # 构建预览文本
    preview = ""
    if questions:
        preview = "带着以下问题去学习下一章，效率会更高："
    else:
        preview = f"准备学习「{next_chapter['title']}」吧！"

    return {
        "preview": preview,
        "next_title": next_chapter["title"],
        "questions": questions,
    }


# ==================== 角色推荐 ====================
@app.get("/api/courses/{course_id}/roles/recommend")
async def recommend_roles(course_id: str):
    """根据课程标题推荐教师角色"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    title = course.get("title", "")

    for pattern, roles in ROLE_RECOMMENDATION.items():
        if re.search(pattern, title, re.IGNORECASE):
            return {
                "recommended": roles,
                "all_roles": list(ROLES_META.keys()),
            }

    return {
        "recommended": DEFAULT_ROLES,
        "all_roles": list(ROLES_META.keys()),
    }


@app.get("/api/roles")
async def get_all_roles():
    """获取所有角色信息（含特点、适用场景）"""
    from config import PROMPTS_DIR
    result = {}
    for role_id, meta in ROLES_META.items():
        prompt_path = PROMPTS_DIR / f"{role_id}.md"
        prompt_preview = ""
        if prompt_path.exists():
            content = prompt_path.read_text(encoding="utf-8")
            prompt_preview = content[:200]  # 仅预览前200字
        result[role_id] = {
            "id": role_id,
            "name": meta["name"],
            "name_en": meta.get("name_en", ""),
            "emoji": meta["emoji"],
            "style": meta.get("style", ""),
            "personality": meta.get("personality", ""),
            "best_for": meta.get("best_for", ""),
            "tags": meta.get("tags", []),
            "default_sliders": DEFAULT_SLIDERS.get(role_id, {"strictness": 0, "encouragement": 0, "verbosity": 0}),
            "prompt_preview": prompt_preview,
        }
    return result


@app.get("/api/roles/{role_id}")
async def get_role_detail(role_id: str):
    """获取角色详情（含完整提示词）"""
    meta = ROLES_META.get(role_id)
    if not meta:
        raise HTTPException(404, "角色不存在")
    from config import PROMPTS_DIR
    prompt_path = PROMPTS_DIR / f"{role_id}.md"
    prompt_content = ""
    if prompt_path.exists():
        prompt_content = prompt_path.read_text(encoding="utf-8")
    return {
        "id": role_id,
        "name": meta["name"],
        "emoji": meta["emoji"],
        "style": meta.get("style", ""),
        "personality": meta.get("personality", ""),
        "best_for": meta.get("best_for", ""),
        "tags": meta.get("tags", []),
        "default_sliders": DEFAULT_SLIDERS.get(role_id, {"strictness": 0, "encouragement": 0, "verbosity": 0}),
        "prompt": prompt_content,
    }


# ==================== 学习契约 ====================
@app.patch("/api/courses/{course_id}/contract")
async def update_contract(course_id: str, data: dict):
    """更新学习契约设置"""
    if "teacher" in data:
        db.update_course_teacher(course_id, data["teacher"])
    if "depth" in data:
        assert data["depth"] in ("basic", "standard", "deep")
        db.update_course_depth(course_id, data["depth"])
    if "duration" in data:
        assert data["duration"] in DURATION_OPTIONS
        db.update_course_duration(course_id, data["duration"])
    return {"status": "ok"}


# ==================== 掌握项 ====================
@app.get("/api/courses/{course_id}/syllabus")
async def get_syllabus(course_id: str):
    return {"syllabus": db.get_syllabus_items(course_id)}


@app.patch("/api/syllabus/{item_id}")
async def update_syllabus(item_id: int, data: dict):
    """手动勾选/取消勾选掌握项"""
    status = data.get("status", "pending")
    db.update_syllabus_item(item_id, status)
    return {"status": "ok"}


# ==================== 角色滑块 ====================
@app.get("/api/courses/{course_id}/sliders/{role_id}")
async def get_role_sliders(course_id: str, role_id: str):
    return db.get_sliders(course_id, role_id)


@app.post("/api/courses/{course_id}/sliders/{role_id}")
async def save_role_sliders(course_id: str, role_id: str, data: dict):
    db.save_sliders(
        course_id, role_id,
        data.get("strictness", 0),
        data.get("encouragement", 0),
        data.get("verbosity", 0),
    )
    return {"status": "ok"}


# ==================== 对话 ====================
# 活跃状态机实例管理器
active_sessions: dict[str, DialogueStateMachine] = {}


@app.post("/api/courses/{course_id}/chat/start")
async def start_chat(course_id: str, data: dict):
    """开始对话会话"""
    chapter_index = data.get("chapter_index", 0)
    teacher_role_id = data.get("teacher_role_id", "ganyu")
    depth = data.get("depth", "standard")
    sliders = data.get("sliders", {})

    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    # 创建会话
    session_id = db.create_session(course_id, chapter_index, teacher_role_id)

    # 创建状态机
    sm = DialogueStateMachine(
        session_id=session_id,
        course_id=course_id,
        chapter_index=chapter_index,
        teacher_role_id=teacher_role_id,
        depth=depth or course.get("current_depth", "standard"),
        sliders=sliders,
        duration_minutes=course.get("current_duration", 30),
    )
    await sm.load_context()
    active_sessions[session_id] = sm

    return {"session_id": session_id, "chapter_index": chapter_index}


@app.get("/api/sessions/{session_id}/basic")
async def get_session_basic(session_id: str):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    messages = db.get_messages(session_id)
    annotations = db.get_annotations(session_id)
    group_chats = db.get_group_chats(session_id)
    return {
        "session": session,
        "messages": messages,
        "annotations": annotations,
        "group_chats": group_chats,
    }


# ==================== WebSocket 流式对话 ====================
@app.websocket("/ws/chat/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    await websocket.accept()

    sm = active_sessions.get(session_id)
    if not sm:
        await websocket.send_json({"error": "会话不存在或已过期"})
        await websocket.close()
        return

    try:
        # INIT → 初始化对话
        await websocket.send_json({"state": "INIT", "content": "📚 正在翻开教材..."})
        await asyncio.sleep(0.3)

        # SHARE → 分享教材片段
        share_text = ""
        async for chunk in sm.initialize():
            share_text += chunk
            await websocket.send_json({"state": "SHARE", "content": chunk})
        await websocket.send_json({"state": "SHARE_DONE"})

        await asyncio.sleep(0.2)

        # PROBE → 提出第一个问题
        probe_text = ""
        async for chunk in sm._probe():
            probe_text += chunk
            await websocket.send_json({"state": "PROBE", "content": chunk})
        await websocket.send_json({"state": "PROBE_DONE"})

        # 对话循环：等待用户输入
        while True:
            # 发送WAIT_USER信号
            await websocket.send_json({"state": "WAIT_USER", "timeout": 120})

            # 等用户回复
            data = await websocket.receive_text()
            msg = json.loads(data)
            user_text = msg.get("content", "")

            if msg.get("action") == "end":
                # 用户主动结束
                async for chunk in sm._end_session("用户主动结束"):
                    await websocket.send_json({"state": "END", "content": chunk})
                break

            # 快速跳过模式：用户点击"我已掌握"按钮
            if msg.get("quick_mastered"):
                sm.current_round += 1
                sm.total_rounds += 1
                # 保存用户消息
                sm.messages.append({"role": "user", "content": user_text})
                db.add_message(session_id, "user", user_text, "USER_INPUT")
                # 标记当前章节的第一个 pending 项为 mastered
                syllabus_items = db.get_syllabus_items(course_id)
                chapter_pending = [s for s in syllabus_items
                                   if s["chapter_index"] == chapter_index
                                   and s["status"] == "pending"]
                if chapter_pending and chapter_pending[0]["id"] not in sm.session_mastered_ids:
                    db.update_syllabus_item(chapter_pending[0]["id"], "mastered")
                    sm.session_mastered_ids.add(chapter_pending[0]["id"])
                    # 通知客户端标记成功
                    await websocket.send_json({"state": "MASTERED_SKIPPED", "syllabus_id": chapter_pending[0]["id"]})
                # 直接进入下一个 PROBE
                await websocket.send_json({"state": "TURN_DONE"})
                await asyncio.sleep(0.2)
                async for chunk in sm._probe():
                    await websocket.send_json({"state": "PROBE", "content": chunk})
                await websocket.send_json({"state": "PROBE_DONE"})
                continue

            # 处理用户输入（EVAL → ACTION）
            full_response = ""
            async for chunk in sm.handle_user_input(user_text):
                full_response += chunk
                current_state = sm.state
                await websocket.send_json({"state": current_state, "content": chunk})

            # 标记本轮结束
            await websocket.send_json({"state": "TURN_DONE"})

            # 如果状态机进入END，断开
            if sm.state == sm.END:
                await websocket.send_json({"state": "SESSION_END"})
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"error": f"网络好像有点问题，要不我们换个话题试试？"})
        except Exception:
            pass
    finally:
        active_sessions.pop(session_id, None)
        try:
            await websocket.close()
        except Exception:
            pass


# ==================== 划词问答 ====================
@app.post("/api/annotate")
async def annotate_ask(data: dict):
    """划词问答（独立API，Temperature=0.3）"""
    course_id = data.get("course_id", "")
    session_id = data.get("session_id", "")
    quoted_text = data.get("quoted_text", "")
    question = data.get("question", "")

    if not quoted_text or not question:
        raise HTTPException(400, "缺少引用文本或问题")

    # 轻量级Prompt，不携带角色扮演
    messages = [
        {"role": "system", "content": "你是一个冷静助教。根据用户引用的文本和问题，给出简洁、准确的解答。不要角色扮演。Temperature自动设为0.3。"},
        {"role": "user", "content": f"引用文本：\n{quoted_text}\n\n问题：{question}"},
    ]

    answer = await llm.chat(messages, temperature=0.3, max_tokens=500)

    # 保存划词问答记录
    if course_id:
        db.add_annotation(course_id, session_id, quoted_text, question, answer)
        db.add_learning_event(course_id, "annotation_ask", {"session": session_id})

    return {"quoted_text": quoted_text, "question": question, "answer": answer}


@app.get("/api/courses/{course_id}/annotations")
async def get_course_annotations(course_id: str):
    return {"annotations": db.get_annotations(course_id)}


# ==================== 答辩记录查询 ====================
@app.get("/api/courses/{course_id}/defense/records")
async def get_defense_records(course_id: str):
    """获取课程的所有答辩记录"""
    return {"records": db.get_defense_records(course_id)}


# ==================== 学习历史 ====================
@app.get("/api/courses/{course_id}/history")
async def get_course_history(course_id: str):
    """获取课程完整学习历史"""
    sessions = db.get_sessions(course_id)
    history = []
    for s in sessions:
        messages = db.get_messages(s["id"])
        user_msgs = [m for m in messages if m["role"] == "user"]

        # 没有用户消息的会话视为空对话，不生成学习记录
        if not user_msgs:
            continue

        diaries_list = []
        # 查找本会话关联的日记
        conn = db.get_conn()
        try:
            rows = conn.execute("SELECT * FROM diaries WHERE session_id=?", (s["id"],)).fetchall()
            diaries_list = [dict(r) for r in rows]
        finally:
            conn.close()

        group_chats = db.get_group_chats(s["id"])
        summaries_list = []
        conn2 = db.get_conn()
        try:
            rows = conn2.execute("SELECT * FROM summaries WHERE session_id=?", (s["id"],)).fetchall()
            summaries_list = [dict(r) for r in rows]
        finally:
            conn2.close()

        assistant_msgs = [m for m in messages if m["role"] == "assistant"]

        # 获取章节标题
        chapter_title = ""
        ch = db.get_chapter(course_id, s["chapter_index"])
        if ch:
            chapter_title = ch.get("title", "")

        # 计算时长
        duration_minutes = 0
        if s.get("ended_at") and s.get("started_at"):
            try:
                from datetime import datetime
                start = datetime.strptime(s["started_at"][:19], "%Y-%m-%d %H:%M:%S")
                end = datetime.strptime(s["ended_at"][:19], "%Y-%m-%d %H:%M:%S")
                duration_minutes = max(1, int((end - start).total_seconds() // 60))
            except (ValueError, IndexError):
                duration_minutes = (s.get("total_rounds") or 0) * 2
        else:
            duration_minutes = (s.get("total_rounds") or 0) * 2

        # 转换为 UTC 时间
        s_utc = _utc_dict(_utc_dict(s, "started_at"), "ended_at")

        history.append({
            "session_id": s_utc["id"],
            "chapter_index": s_utc["chapter_index"],
            "teacher_role_id": s_utc["teacher_role_id"],
            "total_rounds": s_utc.get("total_rounds", 0),
            "started_at": s_utc.get("started_at"),
            "ended_at": s_utc.get("ended_at"),
            "duration_minutes": duration_minutes,
            "message_count": len(messages),
            "user_message_count": len(user_msgs),
            "assistant_message_count": len(assistant_msgs),
            "diaries": _utc_list(diaries_list, "created_at"),
            "group_chats": _utc_list(group_chats, "created_at"),
            "summaries": _utc_list(summaries_list, "created_at"),
            "has_diary": len(diaries_list) > 0,
            "has_summary": len(summaries_list) > 0,
            "user_messages": [m["content"][:100] for m in user_msgs[-3:]],
            "chapter_title": chapter_title,
        })

    # 获取答辩记录
    defense_records = db.get_defense_records(course_id)

    return {"history": history, "defense_records": defense_records}


# ==================== 单次会话完整详情 ====================
@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """获取单次会话的完整详情，含全部对话消息和产出物"""
    # 获取会话基本信息
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(404, "会话不存在")
        session = _utc_dict(dict(row), "started_at")
    finally:
        conn.close()

    course_id = session["course_id"]
    course = db.get_course(course_id)

    # 获取章节标题
    chapter_title = ""
    ch = db.get_chapter(course_id, session["chapter_index"])
    if ch:
        chapter_title = ch.get("title", "")

    # 获取完整消息
    messages = db.get_messages(session_id)

    # 过滤掉 system 消息，只保留 user 和 assistant 的对话
    dialogue_messages = [m for m in messages if m["role"] in ("user", "assistant")]

    # 获取教师角色信息
    from config import ROLES_META
    teacher_meta = ROLES_META.get(session["teacher_role_id"], {})
    teacher_info = {
        "id": session["teacher_role_id"],
        "name": teacher_meta.get("name", session["teacher_role_id"]),
        "emoji": teacher_meta.get("emoji", "🎓"),
        "style": teacher_meta.get("style", ""),
    }

    # 获取课后产出物（按会话过滤）
    conn2 = db.get_conn()
    try:
        diary_rows = conn2.execute(
            "SELECT * FROM diaries WHERE session_id=? ORDER BY created_at DESC", (session_id,)
        ).fetchall()
        diaries = [dict(r) for r in diary_rows]

        summary_rows = conn2.execute(
            "SELECT * FROM summaries WHERE session_id=? ORDER BY created_at DESC", (session_id,)
        ).fetchall()
        summaries = [dict(r) for r in summary_rows]
    finally:
        conn2.close()

    group_chats = db.get_group_chats(session_id)
    annotations = db.get_annotations(session_id)

    # 计算时长
    duration_minutes = 0
    if session.get("ended_at") and session.get("started_at"):
        try:
            from datetime import datetime
            start = datetime.strptime(session["started_at"][:19], "%Y-%m-%d %H:%M:%S")
            end = datetime.strptime(session["ended_at"][:19], "%Y-%m-%d %H:%M:%S")
            duration_minutes = max(1, int((end - start).total_seconds() // 60))
        except (ValueError, IndexError):
            duration_minutes = (session.get("total_rounds") or 0) * 2
    else:
        duration_minutes = (session.get("total_rounds") or 0) * 2

    return {
        "session": {
            "id": session["id"],
            "course_id": session["course_id"],
            "chapter_index": session["chapter_index"],
            "chapter_title": chapter_title,
            "teacher_role_id": session["teacher_role_id"],
            "teacher_info": teacher_info,
            "course_title": course["title"] if course else "",
            "total_rounds": session["total_rounds"],
            "total_duration": duration_minutes,
            "started_at": session["started_at"],
            "ended_at": session.get("ended_at", ""),
        },
        "messages": dialogue_messages,
        "outputs": {
            "diaries": _utc_list(diaries, "created_at")[:3] if diaries else [],
            "summaries": _utc_list(summaries, "created_at")[:2] if summaries else [],
            "group_chats": _utc_list(group_chats, "created_at")[:5] if group_chats else [],
            "annotations": annotations[:10] if annotations else [],
        },
    }


# ==================== 日记/群聊/总结 ====================
@app.get("/api/courses/{course_id}/diaries")
async def get_diaries(course_id: str):
    return {"diaries": _utc_list(db.get_diaries(course_id), "created_at")}


@app.get("/api/courses/{course_id}/summaries")
async def get_summaries(course_id: str):
    return {"summaries": _utc_list(db.get_summaries(course_id), "created_at")}


@app.get("/api/courses/{course_id}/group-chats")
async def get_group_chats(course_id: str):
    """获取最新的群聊记录"""
    sessions = db.get_sessions(course_id)
    all_chats = []
    for s in sessions[:5]:
        chats = db.get_group_chats(s["id"])
        all_chats.extend(chats)
    return {"group_chats": _utc_list(all_chats, "created_at")}


# ==================== 结业答辩 ====================
@app.get("/api/courses/{course_id}/defense/check")
async def check_defense_eligibility(course_id: str):
    """检查结业答辩资格"""
    progress = db.get_syllabus_progress(course_id)
    eligible = progress["total"] > 0 and progress["percent"] == 100
    return {"eligible": eligible, "progress": progress}


@app.post("/api/courses/{course_id}/defense/start")
async def start_defense(course_id: str):
    """开始结业答辩"""
    # 后端硬校验：进度必须100%
    progress = db.get_syllabus_progress(course_id)
    if progress["total"] == 0 or progress["percent"] < 100:
        raise HTTPException(400, "进度未达100%，不能申请结业答辩")

    syllabus = db.get_syllabus_items(course_id)
    course = db.get_course(course_id)

    # 随机抽取3条掌握项（覆盖首/中/尾章节）
    import random
    if len(syllabus) <= 3:
        selected = syllabus
    else:
        first = [s for s in syllabus if s["chapter_index"] == 0]
        middle_candidates = [s for s in syllabus if s["chapter_index"] == (course["total_chapters"] // 2)]
        last_candidates = [s for s in syllabus if s["chapter_index"] == course["total_chapters"] - 1]
        middle = [s for s in middle_candidates if s not in first]
        last = [s for s in last_candidates if s not in first]

        selected = []
        if first:
            selected.append(random.choice(first))
        if middle:
            selected.append(random.choice(middle))
        if last:
            selected.append(random.choice(last))

        # 补全到3条
        remaining = [s for s in syllabus if s not in selected]
        random.shuffle(remaining)
        while len(selected) < 3 and remaining:
            selected.append(remaining.pop())

    # 将掌握项转化为开放式问题
    questions = []
    for item in selected[:3]:
        prompt = f"""根据以下掌握项，生成一个开放式答辩问题（不直接问原文，而是考察理解和应用能力）：

掌握项：{item['description']}

返回JSON：
{{"question": "开放式问题内容"}}"""
        try:
            result = await llm.chat_json([
                {"role": "system", "content": "你是考试出题专家。"},
                {"role": "user", "content": prompt},
            ])
            questions.append({
                "syllabus_id": item["id"],
                "chapter_index": item["chapter_index"],
                "description": item["description"],
                "question": result.get("question", item["description"]),
            })
        except Exception:
            questions.append({
                "syllabus_id": item["id"],
                "chapter_index": item["chapter_index"],
                "description": item["description"],
                "question": f"请谈谈你对「{item['description']}」的理解。",
            })

    defense_id = str(uuid.uuid4())[:8]
    return {
        "defense_id": defense_id,
        "questions": questions,
        "time_limit_minutes": 10,
    }


@app.post("/api/courses/{course_id}/defense/submit")
async def submit_defense(course_id: str, data: dict):
    """提交答辩答案并逐题评估"""
    questions = data.get("questions", [])
    answers = data.get("answers", [])

    if len(questions) != len(answers) or len(questions) == 0:
        raise HTTPException(400, "问题和答案数量不匹配")

    results = []
    all_passed = True

    for i, (q, a) in enumerate(zip(questions, answers)):
        prompt = f"""评估学生的答辩回答。

题目：{q.get('question', '')}
学生的回答：{a.get('answer', '')}

要求：
- 判断是否真正掌握了该知识点（PASS/FAIL）
- 给出简短评语（20字以内）

返回JSON：
{{"verdict": "PASS"|"FAIL", "comment": "评语"}}"""

        try:
            result = await llm.chat_json([
                {"role": "system", "content": "你是严格但公正的答辩评审专家。"},
                {"role": "user", "content": prompt},
            ])
            verdict = result.get("verdict", "FAIL")
            comment = result.get("comment", "回答不够充分")
            if verdict == "FAIL":
                all_passed = False
            results.append({"question_index": i, "verdict": verdict, "comment": comment})
        except Exception:
            results.append({"question_index": i, "verdict": "FAIL", "comment": "无法评估"})
            all_passed = False

    # 为FAIL的题目生成参考答案
    for i, (q, a) in enumerate(zip(questions, answers)):
        if results[i]["verdict"] == "FAIL":
            ref_prompt = f"""以下是一道考试题和学生的回答。学生答错了，请给出一个标准参考答案。

题目：{q.get('question', '')}
对应的掌握项：{q.get('description', '')}

要求：给出一个简洁完整的参考答案（100字以内），帮助学生学习这个知识点。

只输出参考答案本身，不要加前缀。"""
            try:
                ref_answer = await llm.chat([
                    {"role": "system", "content": "你是课程助教，给出标准答案。"},
                    {"role": "user", "content": ref_prompt},
                ], temperature=0.3, max_tokens=300)
                questions[i]["reference_answer"] = ref_answer.strip()
            except Exception:
                questions[i]["reference_answer"] = "请回顾教材中相关章节的内容。"

    # 保存答辩记录到数据库
    result_questions = []
    for i, (q, a) in enumerate(zip(questions, answers)):
        result_questions.append({
            "syllabus_id": q.get("syllabus_id"),
            "chapter_index": q.get("chapter_index"),
            "description": q.get("description"),
            "question": q.get("question"),
            "answer": a.get("answer", ""),
            "verdict": results[i]["verdict"],
            "comment": results[i]["comment"],
            "reference_answer": q.get("reference_answer", ""),
        })

    certificate_id = None

    # 如果全部通过，生成结业证书
    certificate = None
    if all_passed:
        course = db.get_course(course_id)
        profile = db.get_profile(course_id)
        affinities = db.get_all_affinities(course_id)
        teacher_id = course.get("current_teacher", "ganyu") if course else "ganyu"

        strengths_text = "、".join(profile.get("strengths", ["待总结"])[:3]) if profile else "顺利完成课程"
        weaknesses_text = "、".join(profile.get("weaknesses", ["继续加油"])[:3]) if profile else "继续努力"

        # 教师寄语
        teacher_comment = await llm.chat([
            {"role": "system", "content": "你是一个温暖而有智慧的教师。写一句毕业寄语（30字以内）。"},
            {"role": "user", "content": f"学生刚完成了{course['title']}课程的全部学习。"},
        ], temperature=0.7, max_tokens=100)

        stats = db.get_course_learning_stats(course_id)
        total_minutes = stats.get("total_minutes", 0)
        total_tokens = stats.get("total_tokens", 0)

        certificate_id = db.add_certificate(
            course_id=course_id,
            teacher_role_id=teacher_id,
            total_minutes=total_minutes,
            strengths=strengths_text,
            weaknesses=weaknesses_text,
            teacher_comment=teacher_comment,
        )
        certificate = {
            "course_title": course["title"] if course else "课程",
            "teacher": ROLES_META.get(teacher_id, {}).get("name", teacher_id),
            "total_minutes": total_minutes,
            "total_tokens": total_tokens,
            "strengths": strengths_text,
            "weaknesses": weaknesses_text,
            "teacher_comment": teacher_comment,
            "issued_at": datetime.now().isoformat(),
        }

    # 保存答辩记录
    db.add_defense_record(course_id, all_passed, result_questions, certificate_id)

    return {"results": results, "all_passed": all_passed, "certificate": certificate, "defense_record_id": 0}


# ==================== 热力图 ====================
@app.get("/api/events/heatmap")
async def get_heatmap(year: int = Query(default=None, description="年份")):
    if year is None:
        year = datetime.now().year
    data = db.get_heatmap_data(year)
    return {"year": year, "data": data}


@app.get("/api/events/daily_summary")
async def daily_summary(date: str = Query(..., description="日期 (YYYY-MM-DD)")):
    return db.get_daily_summary(date)


# ==================== LLM设置 ====================
@app.get("/api/settings/llm")
async def get_llm_settings():
    cfg = llm.__dict__
    return {
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "has_api_key": bool(cfg.get("api_key", "")),
    }


@app.post("/api/settings/llm")
async def update_llm_settings(data: dict):
    from config import update_llm_config, LLM_CONFIG
    if "api_key" in data and data["api_key"]:
        LLM_CONFIG["api_key"] = data["api_key"]
        llm.api_key = data["api_key"]
    if "base_url" in data and data["base_url"]:
        LLM_CONFIG["base_url"] = data["base_url"]
        llm.base_url = data["base_url"].rstrip("/")
    if "model" in data and data["model"]:
        LLM_CONFIG["model"] = data["model"]
        llm.model = data["model"]

    # 同步到数据库中的活跃配置（如有）
    active = db.get_active_model_with_provider()
    if active:
        updates = {}
        if "api_key" in data and data["api_key"]:
            updates["api_key"] = data["api_key"]
        if "base_url" in data and data["base_url"]:
            updates["base_url"] = data["base_url"]
        if updates:
            db.update_llm_provider(active["provider_id"], **updates)
        if "model" in data and data["model"]:
            model_id = active.get("model_id")
            if model_id:
                db.update_llm_model_rename(model_id, data["model"])
    else:
        # 没有库记录时，在数据库中创建一份
        name = data.get("name", "默认提供商")
        base_url = data.get("base_url") or LLM_CONFIG.get("base_url", "https://api.deepseek.com")
        api_key = data.get("api_key") or LLM_CONFIG.get("api_key", "")
        model = data.get("model") or LLM_CONFIG.get("model", "deepseek-chat")
        providers = db.get_all_llm_providers()
        if not providers:
            pid = db.add_llm_provider(name, base_url, api_key)
            db.add_llm_model(pid, model)
            db.set_active_provider(pid)
        else:
            # 使用第一个 provider 更新
            db.update_llm_provider(providers[0]["id"], base_url=base_url, api_key=api_key)

    return {"status": "ok"}


# ==================== LLM 提供商管理 ====================

@app.get("/api/llm/providers")
async def list_llm_providers():
    """列出所有提供商（含 models）"""
    return db.get_all_llm_providers()


@app.post("/api/llm/providers")
async def create_llm_provider(data: dict):
    name = data.get("name", "").strip()
    base_url = data.get("base_url", "").strip()
    api_key = data.get("api_key", "").strip()
    if not name or not base_url:
        raise HTTPException(400, "名称和 Base URL 不能为空")
    provider_id = db.add_llm_provider(name, base_url, api_key)
    return {"provider_id": provider_id}


@app.put("/api/llm/providers/{provider_id}")
async def update_llm_provider(provider_id: int, data: dict):
    db.update_llm_provider(
        provider_id,
        name=data.get("name"),
        base_url=data.get("base_url"),
        api_key=data.get("api_key"),
    )
    return {"status": "ok"}


@app.delete("/api/llm/providers/{provider_id}")
async def delete_llm_provider(provider_id: int):
    provider = db.get_llm_provider(provider_id)
    if not provider:
        raise HTTPException(404, "提供商不存在")
    was_active = provider["is_active"]
    db.delete_llm_provider(provider_id)
    # 如果删除了活跃提供商，尝试激活其他任意一个
    if was_active:
        remaining = db.get_all_llm_providers()
        if remaining:
            db.set_active_provider(remaining[0]["id"])
            models = db.get_llm_models(remaining[0]["id"])
            if models:
                db.set_active_model(models[0]["id"])
    # 同步 llm 单例
    _sync_llm_from_db()
    return {"status": "ok"}


@app.post("/api/llm/providers/{provider_id}/activate")
async def activate_llm_provider(provider_id: int):
    provider = db.get_llm_provider(provider_id)
    if not provider:
        raise HTTPException(404, "提供商不存在")
    models = db.get_llm_models(provider_id)
    if not models:
        raise HTTPException(400, "此提供商下没有模型，请先添加模型")
    db.set_active_provider(provider_id)
    # 如果该提供商下没有任何 model 激活，激活第一个
    if not any(m["is_active"] for m in models):
        db.set_active_model(models[0]["id"])
    _sync_llm_from_db()
    return {"status": "ok"}


@app.get("/api/llm/providers/{provider_id}/models")
async def list_llm_models(provider_id: int):
    return db.get_llm_models(provider_id)


@app.post("/api/llm/providers/{provider_id}/models")
async def create_llm_model(provider_id: int, data: dict):
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(400, "模型名称不能为空")
    provider = db.get_llm_provider(provider_id)
    if not provider:
        raise HTTPException(404, "提供商不存在")
    model_id = db.add_llm_model(provider_id, name)
    # 如果此 provider 恰是活跃的，同步 llm 单例
    if provider["is_active"]:
        _sync_llm_from_db()
    return {"model_id": model_id}


@app.delete("/api/llm/models/{model_id}")
async def delete_llm_model(model_id: int):
    model = db.get_llm_model(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    provider = db.get_llm_provider(model["provider_id"])
    db.delete_llm_model(model_id)
    # 如果删除了活跃 model，激活该 provider 下的第一个 model
    if model["is_active"] and provider:
        remaining = db.get_llm_models(provider["id"])
        if remaining:
            db.set_active_model(remaining[0]["id"])
    _sync_llm_from_db()
    return {"status": "ok"}


@app.post("/api/llm/models/{model_id}/activate")
async def activate_llm_model(model_id: int):
    model = db.get_llm_model(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    provider = db.get_llm_provider(model["provider_id"])
    if not provider:
        raise HTTPException(404, "提供商不存在")
    db.set_active_provider(provider["id"])
    db.set_active_model(model_id)
    _sync_llm_from_db()
    return {"status": "ok"}


@app.get("/api/llm/active")
async def get_active_llm_config():
    """获取当前活跃配置"""
    active = db.get_active_model_with_provider()
    if active:
        return {
            "provider": {"id": active["provider_id"], "name": active["provider_name"], "base_url": active["base_url"]},
            "model": {"id": active["model_id"], "name": active["model_name"]},
            "has_api_key": bool(active.get("api_key", "")),
        }
    return {"provider": None, "model": None, "has_api_key": False}


@app.post("/api/llm/test")
async def test_llm_connection(data: dict):
    """测试连接"""
    provider_id = data.get("provider_id")
    model_name = data.get("model_name", "")
    api_key = data.get("api_key", "")

    if provider_id:
        provider = db.get_llm_provider(provider_id)
        if not provider:
            raise HTTPException(404, "提供商不存在")
        base_url = provider["base_url"]
        if not api_key:
            api_key = provider["api_key"]
        if not model_name:
            models = db.get_llm_models(provider_id)
            if models:
                model_name = models[0]["name"]
    else:
        raise HTTPException(400, "缺少 provider_id")

    if not model_name:
        return {"success": False, "message": "没有可测试的模型"}
    if not api_key:
        return {"success": False, "message": "API Key 为空"}

    result = await llm.test_connection(base_url, model_name, api_key)
    return result


def _sync_llm_from_db():
    """将数据库活跃配置同步到 llm 单例和 LLM_CONFIG"""
    active = db.get_active_model_with_provider()
    if active:
        LLM_CONFIG["api_key"] = active["api_key"]
        LLM_CONFIG["base_url"] = active["base_url"]
        LLM_CONFIG["model"] = active["model_name"]
        llm.api_key = active["api_key"]
        llm.base_url = active["base_url"].rstrip("/")
        llm.model = active["model_name"]


# ==================== 全局概览 API ====================
@app.get("/api/courses/{course_id}/overview")
async def get_course_overview(course_id: str):
    """获取课程全局学习概览"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    syllabus = db.get_syllabus_items(course_id)
    chapters = db.get_chapters(course_id)
    reading_mode = course.get("reading_mode", "standard")

    import re
    def parse_chapter_num(s: str) -> int:
        """从'第N章'中提取数字"""
        m = re.search(r'第(\d+)章', s)
        return int(m.group(1)) - 1 if m else -1

    # 速读模式：使用章节快照数量作为统计
    if reading_mode == "speed":
        snapshots = db.get_chapter_snapshots(course_id)
        highlights = db.get_global_highlights(course_id) or {}

        core_indices = set()
        if highlights:
            for s in highlights.get("core_chapter_indices", []):
                idx = parse_chapter_num(s)
                if idx >= 0:
                    core_indices.add(idx)

        # 章节维度统计（基于快照）
        chapter_stats = []
        for ch in chapters:
            snapshot = next((s for s in snapshots if s["chapter_index"] == ch["idx"]), None)
            sessions = db.get_sessions(course_id)
            ch_sessions = [s for s in sessions if s.get("chapter_index") == ch["idx"]]
            chapter_stats.append({
                "idx": ch["idx"],
                "title": ch["title"],
                "total": 1 if snapshot else 0,
                "mastered": len(ch_sessions),
                "is_loaded": ch.get("is_loaded", 0),
                "importance": snapshot.get("importance", 0) if snapshot else 0,
                "learning_goal": snapshot.get("learning_goal", "") if snapshot else "",
                "keywords": snapshot.get("keywords", []) if snapshot else [],
                "core_viewpoint": snapshot.get("core_viewpoint", "") if snapshot else "",
                "is_core": ch["idx"] in core_indices,
            })

        chapter_dependencies = {}
        if highlights:
            for ch_key, deps in highlights.get("chapter_dependencies", {}).items():
                ch_idx = parse_chapter_num(ch_key)
                if ch_idx < 0:
                    continue
                if isinstance(deps, str):
                    dep_idx = parse_chapter_num(deps)
                    if dep_idx >= 0:
                        chapter_dependencies[ch_idx] = [dep_idx]
                elif isinstance(deps, list):
                    dep_indices = []
                    for d in deps:
                        di = parse_chapter_num(d)
                        if di >= 0:
                            dep_indices.append(di)
                    if dep_indices:
                        chapter_dependencies[ch_idx] = dep_indices

        # 已学习的章节
        learned_indices = {s.get("chapter_index") for s in chapter_stats if s["mastered"] > 0}

        # 1. 已学习章节
        learned_list = []
        for ch in chapters:
            if ch["idx"] in learned_indices:
                snapshot = next((s for s in snapshots if s["chapter_index"] == ch["idx"]), None)
                learned_list.append({
                    "idx": ch["idx"],
                    "title": ch["title"],
                    "learning_goal": snapshot.get("learning_goal", "") if snapshot else "",
                    "importance": snapshot.get("importance", 0) if snapshot else 0,
                })

        # 2. 核心未学章节
        core_unlearned = []
        for ch in chapters:
            if ch["idx"] in learned_indices or ch["idx"] not in core_indices:
                continue
            snapshot = next((s for s in snapshots if s["chapter_index"] == ch["idx"]), None)
            deps = chapter_dependencies.get(ch["idx"], [])
            core_unlearned.append({
                "idx": ch["idx"],
                "title": ch["title"],
                "learning_goal": snapshot.get("learning_goal", "") if snapshot else "",
                "importance": snapshot.get("importance", 3) if snapshot else 3,
                "dependencies": deps,
                "deps_satisfied": all(d in learned_indices or d in core_indices for d in deps),
            })

        # 拓扑排序
        def sort_by_deps(items):
            return sorted(items, key=lambda x: (not x["deps_satisfied"], -x["importance"], x["idx"]))

        core_unlearned = sort_by_deps(core_unlearned)

        # 3. 可选章节
        optional_list = []
        for ch in chapters:
            if ch["idx"] in learned_indices or ch["idx"] in core_indices:
                continue
            snapshot = next((s for s in snapshots if s["chapter_index"] == ch["idx"]), None)
            optional_list.append({
                "idx": ch["idx"],
                "title": ch["title"],
                "learning_goal": snapshot.get("learning_goal", "") if snapshot else "",
                "importance": snapshot.get("importance", 0) if snapshot else 0,
            })
        optional_list.sort(key=lambda x: (-x["importance"], x["idx"]))

        recommended_order = {
            "core_count": len(core_indices),
            "total_count": len(chapters),
            "learned": learned_list,
            "core_to_learn": core_unlearned,
            "optional": optional_list,
            "next_chapter": core_unlearned[0] if core_unlearned else (optional_list[0] if optional_list else None),
        }

        weak_areas = [
            {"id": ch["idx"], "description": f"第{ch['idx'] + 1}章「{ch['title']}」", "chapter_index": ch["idx"], "status": "未学习", "is_core": ch["idx"] in core_indices}
            for ch in chapters if ch["idx"] not in learned_indices
        ][:5]

        core_total = len(core_indices) if core_indices else len(chapters)
        core_learned = sum(1 for ch in chapters if ch["idx"] in core_indices and ch["idx"] in learned_indices)
        core_percent = round(core_learned / core_total * 100, 1) if core_total > 0 else 0

        total_points = len(snapshots)
        mastered_points = len(snapshots)
        percent = 100.0 if total_points > 0 else 0.0

        strategy = {"speed": "精华提炼模式", "standard": "系统学习模式", "deep": "辩证分析模式"}
        next_ch = recommended_order["next_chapter"]
        if total_points == 0:
            recommendation = "正在生成知识快照，请稍候..."
        elif next_ch:
            recommendation = f"建议先学核心章节，下一站：第{next_ch['idx']+1}章「{next_ch['title']}」"
        else:
            recommendation = f"核心章节已学完！共掌握{core_learned}/{core_total}个核心章节（{core_percent}%）"

        return {
            "reading_mode": reading_mode,
            "strategy_description": strategy.get(reading_mode, strategy["standard"]),
            "knowledge_coverage": {"total_points": total_points, "mastered_points": mastered_points, "percent": percent, "total_chapters": len(chapters), "core_chapters": core_total, "core_learned": core_learned, "core_percent": core_percent},
            "chapter_stats": chapter_stats,
            "recommended_order": recommended_order,
            "weak_areas": weak_areas,
            "recommendation": recommendation,
            "highlights": highlights,
            # 新增：日记、群聊、学习记录
            "diaries": _utc_list(db.get_diaries(course_id), "created_at")[:10],
            "summaries": _utc_list(db.get_summaries(course_id), "created_at")[:10],
            "group_chats": _get_course_group_chats(course_id),
        }
    else:
        # 细读/研读模式：使用掌握项统计
        total_points = len(syllabus)
        mastered_points = sum(1 for s in syllabus if s["status"] == "mastered")

        chapter_stats = []
        for ch in chapters:
            ch_items = [s for s in syllabus if s["chapter_index"] == ch["idx"]]
            chapter_stats.append({
                "idx": ch["idx"],
                "title": ch["title"],
                "total": len(ch_items),
                "mastered": sum(1 for s in ch_items if s["status"] == "mastered"),
                "is_loaded": ch.get("is_loaded", 0),
            })

        strategy = {"speed": "精华提炼模式", "standard": "系统学习模式", "deep": "辩证分析模式"}
        pending = [s for s in syllabus if s["status"] == "pending"]
        weak_areas = [{"id": s["id"], "description": s["description"], "chapter_index": s["chapter_index"], "status": "未掌握"} for s in pending[:5]]

        recommended_order = []
        for ch in chapters:
            ch_items = [s for s in syllabus if s["chapter_index"] == ch["idx"]]
            mastered = sum(1 for s in ch_items if s["status"] == "mastered")
            total = len(ch_items)
            if total == 0:
                continue
            mastered_ratio = mastered / total if total > 0 else 0
            if mastered_ratio == 0:
                priority = "high"
            elif mastered_ratio < 1:
                priority = "medium"
            else:
                priority = "low"
            recommended_order.append({"idx": ch["idx"], "title": ch["title"], "priority": priority, "progress": f"{mastered}/{total}", "status": "已完成" if mastered_ratio == 1 else ("进行中" if mastered_ratio > 0 else "未开始")})

        priority_order = {"high": 0, "medium": 1, "low": 2}
        recommended_order.sort(key=lambda x: (priority_order.get(x["priority"], 3), x["idx"]))

        percent = round(mastered_points / max(total_points, 1) * 100, 1)
        next_chapter = next((ch for ch in recommended_order if ch["status"] != "已完成"), None)

        if total_points == 0:
            recommendation = "正在生成分章，请稍候..."
        elif next_chapter:
            if percent < 30:
                recommendation = f"建议从第{next_chapter['idx'] + 1}章「{next_chapter['title']}」开始学习"
            elif percent < 50:
                recommendation = f"继续学习第{next_chapter['idx'] + 1}章「{next_chapter['title']}」，完成更多掌握项"
            elif percent < 100:
                recommendation = f"还剩{len(pending)}个知识点未完成，继续加油！"
            else:
                recommendation = "恭喜！可以申请结业答辩了"
        else:
            recommendation = "恭喜！可以申请结业答辩了"

        return {
            "reading_mode": reading_mode,
            "strategy_description": strategy.get(reading_mode, strategy["standard"]),
            "knowledge_coverage": {"total_points": total_points, "mastered_points": mastered_points, "percent": percent},
            "chapter_stats": chapter_stats,
            "recommended_order": recommended_order[:5],
            "weak_areas": weak_areas,
            "recommendation": recommendation,
            "next_chapter": next_chapter,
            # 新增：日记、群聊、学习记录
            "diaries": _utc_list(db.get_diaries(course_id), "created_at")[:10],
            "summaries": _utc_list(db.get_summaries(course_id), "created_at")[:10],
            "group_chats": _get_course_group_chats(course_id),
        }


# ==================== 知识快照 API ====================
@app.get("/api/courses/{course_id}/snapshots")
async def get_chapter_snapshots(course_id: str):
    """获取课程所有章节快照（速读模式）"""
    snapshots = db.get_chapter_snapshots(course_id)
    highlights = db.get_global_highlights(course_id)
    return {
        "snapshots": snapshots,
        "highlights": highlights,
    }


@app.post("/api/courses/{course_id}/snapshots/generate")
async def generate_snapshots(course_id: str):
    """手动生成知识快照（速读模式）- 支持 EPUB 完整内容加载"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    chapters = db.get_chapters(course_id)
    if not chapters:
        raise HTTPException(400, "请先生成分章")

    source_type = course.get("source_type", "")
    source_path = course.get("source_path", "")

    # P0-①：抽离为统一函数（与 run_chapter_generation / generate_chapters 共用同一份逻辑）
    # 旧实现有"懒加载"逻辑：EPUB 内容短时从 EPUB 重新加载。helper 函数内已处理（基于 toc_items）。
    try:
        chapter_list = []
        for ch in chapters:
            content = ch.get("content_slice", "") or ch.get("content_full", "")
            chapter_list.append((ch["title"], content, {"idx": ch["idx"]}))
        chapter_titles = [ch["title"] for ch in chapters]

        result = await _run_speed_mode_postprocess(
            course_id, chapter_list, chapter_titles,
            source_type, source_path, concurrency=3,
        )
        await _persist_speed_results(course_id, result)

        snapshots = result["snapshots"]
        snapshot_by_idx = result["snapshots_by_idx"]
        highlights = result["highlights"]
        syllabus_items = result["syllabus_items"]

        highlights_result = {
            "key_points_count": len(highlights.get("key_points", [])) if isinstance(highlights, dict) else 0,
            "core_chapters_count": len(highlights.get("core_chapter_indices", [])) if isinstance(highlights, dict) else 0,
            "is_fallback": highlights.get("_fallback", False) if isinstance(highlights, dict) else False,
        } if highlights else None

        # failed_chapters：原字段为重生成失败的章节。新实现下用 snapshot_by_idx 缺失的章节推算
        all_idxs = {ch["idx"] for ch in chapters}
        success_idxs = set(snapshot_by_idx.keys())
        failed_chapters = sorted(all_idxs - success_idxs)

        return {
            "snapshots_generated": len(snapshots),
            "snapshots_failed": failed_chapters,
            "highlights": highlights_result,
            "syllabus_generated": len(syllabus_items),
        }
    except Exception as e:
        import logging
        logging.error(f"重建快照失败: {e}")
        return {
            "snapshots_generated": 0,
            "snapshots_failed": [ch["idx"] for ch in chapters],
            "highlights": None,
            "syllabus_generated": 0,
        }


# ==================== 思辨笔记 API ====================
@app.get("/api/courses/{course_id}/spiritual-notes")
async def get_spiritual_notes(course_id: str):
    """获取课程所有思辨笔记（研读模式）"""
    notes = db.get_spiritual_notes(course_id)
    # 获取章节标题
    chapters = db.get_chapters(course_id)
    chapter_map = {ch["idx"]: ch["title"] for ch in chapters}
    for note in notes:
        note["chapter_title"] = chapter_map.get(note["chapter_index"], f"第{note['chapter_index'] + 1}章")
    return {"notes": notes}


@app.post("/api/courses/{course_id}/spiritual-notes/generate")
async def generate_spiritual_notes(course_id: str):
    """为指定会话生成思辨笔记"""
    data = request.json()
    session_id = data.get("session_id")
    if not session_id:
        raise HTTPException(400, "缺少 session_id")

    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")

    # 获取会话消息
    messages = db.get_messages(session_id)
    user_msgs = [m for m in messages if m["role"] == "user"]
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]

    chapter = db.get_chapter(course_id, session["chapter_index"])
    chapter_title = chapter.get("title", f"第{session['chapter_index'] + 1}章") if chapter else ""

    prompt = f"""根据以下课堂对话，生成一份"思辨笔记"。

章节：{chapter_title}

学生回答：
{chr(10).join(f"- {m['content'][:200]}" for m in user_msgs[-10:])}

教师引导：
{chr(10).join(f"- {m['content'][:200]}" for m in assistant_msgs[-10:])}

笔记格式（只输出JSON）：
{{"core_contradictions": ["...", "..."], "unresolved_questions": ["...", "..."], "extension_directions": ["...", "..."], "personal_reflection": "..."}}"""

    try:
        result = await llm.chat_json([
            {"role": "system", "content": "你是思辨笔记生成助手。只返回JSON。"},
            {"role": "user", "content": prompt},
        ], temperature=0.3)

        note_id = db.add_spiritual_note(
            course_id=course_id,
            session_id=session_id,
            chapter_index=session["chapter_index"],
            core_contradictions=json.dumps(result.get("core_contradictions", []), ensure_ascii=False),
            unresolved_questions=json.dumps(result.get("unresolved_questions", []), ensure_ascii=False),
            extension_directions=json.dumps(result.get("extension_directions", []), ensure_ascii=False),
            personal_reflection=result.get("personal_reflection", ""),
            raw_content=json.dumps(result, ensure_ascii=False),
        )
        return {"note_id": note_id, "content": result}
    except Exception as e:
        raise HTTPException(500, f"生成思辨笔记失败: {str(e)}")


# ==================== 掌握进度 API ====================
@app.get("/api/courses/{course_id}/mastery-progress")
async def get_mastery_progress(course_id: str):
    """获取课程掌握进度"""
    progress = db.get_mastery_progress(course_id)
    syllabus = db.get_syllabus_items(course_id)

    # 合并实时数据
    if syllabus:
        progress["total"] = len(syllabus)
        progress["mastered"] = [s["id"] for s in syllabus if s["status"] == "mastered"]
        progress["pending"] = [s["id"] for s in syllabus if s["status"] == "pending"]
        progress["in_progress"] = [s["id"] for s in syllabus if s["status"] == "in_progress"]

        by_chapter = {}
        for s in syllabus:
            ch_idx = s["chapter_index"]
            if ch_idx not in by_chapter:
                by_chapter[ch_idx] = {"total": 0, "mastered": 0}
            by_chapter[ch_idx]["total"] += 1
            if s["status"] == "mastered":
                by_chapter[ch_idx]["mastered"] += 1
        progress["by_chapter"] = by_chapter

    return progress


# ==================== 健康检查 ====================
@app.get("/api/config")
async def get_config():
    """获取系统配置信息"""
    return {
        "roles": list(ROLES_META.keys()),
        "depth_options": list(DEPTH_CONFIG.keys()),
        "duration_options": DURATION_OPTIONS,
        "version": VERSION,
    }


# ==================== 启动 ====================
if __name__ == "__main__":
    import uvicorn
    print(f"🌊 问渠（Wenqu）v{VERSION} 启动中...")
    print(f"📚 访问地址：http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")

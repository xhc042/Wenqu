"""
问渠（Wenqu）v1.1 主应用
FastAPI Web服务器 + API路由 + WebSocket对话

v1.4 重构: 路由模块化
- routes/course_routes.py: 课程管理、分章、掌握项
- routes/task_routes.py: 异步任务管理
- routes/defense_settings.py: 结业答辩、设置
- routes/websocket_routes.py: WebSocket 实时对话
- modules/llm_config_manager.py: LLM 配置统一管理
"""

import json
import os
import asyncio
import uuid
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
from modules.llm_config_manager import (
    sync_db_to_config,
    sync_config_to_db,
    get_runtime_config,
    update_runtime_config,
)

# ==================== 导入路由模块 ====================
from routes.course_routes import (
    _save_chapters_to_db,
    _add_default_syllabus_items,
    _run_speed_mode_postprocess,
    _persist_speed_results,
    _get_course_group_chats,
    _utc,
    _utc_dict,
    _utc_list,
    # 课程路由
    api_list_courses as route_list_courses,
    api_create_course as route_create_course,
    api_upload_file as route_upload_file,
    api_get_course as route_get_course,
    api_remove_course as route_remove_course,
    api_generate_chapters as route_generate_chapters,
    api_load_chapter_content as route_load_chapter_content,
    api_regenerate_syllabus as route_regenerate_syllabus,
    api_generate_syllabus as route_generate_syllabus,
    api_generate_next_preview as route_generate_next_preview,
    api_recommend_roles as route_recommend_roles,
    api_get_all_roles as route_get_all_roles,
    api_get_role_detail as route_get_role_detail,
    api_get_chapter_snapshots as route_get_chapter_snapshots,
    api_generate_snapshots as route_generate_snapshots,
    api_get_course_overview as route_get_course_overview,
    api_get_spiritual_notes as route_get_spiritual_notes,
    api_get_mastery_progress as route_get_mastery_progress,
)
from routes.task_routes import (
    async_tasks, _tasks_lock as _async_tasks_lock, TASK_STATUS,
    create_task as route_create_task,
    get_task as route_get_task,
    cancel_task as route_cancel_task,
    find_active_task_for_course as route_find_active_task,
)
from routes.defense_settings import (
    api_generate_defense_questions,
    api_submit_answer,
    api_get_defense_questions,
    api_get_defense_progress,
    api_complete_defense,
    api_get_certificate,
    api_issue_certificate,
    api_get_llm_config as route_get_llm_config,
    api_update_llm_config as route_update_llm_config,
    api_update_course_settings,
    api_get_course_settings,
    api_add_learning_event,
    api_get_learning_events,
    api_save_diary_entry,
    api_get_diary_entries,
    api_delete_diary_entry,
    api_get_summary,
    api_clear_summary,
    api_get_course_groups,
    api_get_group_chats,
    api_reset_course_progress,
    api_update_syllabus_item,
    api_add_spiritual_note,
    api_delete_spiritual_note,
)
from routes.websocket_routes import ws_manager, handle_single_chat, handle_group_chat


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

    # 统一从数据库同步 LLM 配置（使用新的配置管理器）
    sync_db_to_config()


# ==================== 前端路由 ====================
@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = STATIC_DIR / "static" / "index.html"
    if not index_path.exists():
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
    is_speed_task = data.get("reading_mode") == "speed"
    steps = [
        {"name": "正在智能分章...", "status": "pending", "detail": ""},
        {"name": "生成知识快照", "status": "pending", "detail": ""} if is_speed_task else {"name": "生成教学大纲", "status": "pending", "detail": ""},
        {"name": "生成教学大纲", "status": "pending", "detail": ""} if is_speed_task else None,
        {"name": "完成", "status": "pending", "detail": ""},
    ]
    steps = [s for s in steps if s is not None]
    async with _async_tasks_lock:
        async_tasks[task_id] = {
            "task_id": task_id,
            "course_id": course_id,
            "type": "chapters_generate",
            "status": TASK_STATUS["PENDING"],
            "progress": 0,
            "steps": steps,
            "current_step": 0,
            "result": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
        }
    
    asyncio.create_task(run_chapter_generation(task_id))
    return {"task_id": task_id}


async def run_chapter_generation(task_id: str):
    """后台执行分章和大纲生成的异步任务"""
    task = async_tasks.get(task_id)
    if not task:
        return
    
    try:
        from chunker import extract_text, smart_chunk
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
        
        task["steps"][0]["detail"] = "正在读取文件内容..."
        task["progress"] = 5
        actual_type = source_type
        if source_type == "text":
            actual_type = "txt"
        text = await extract_text(source_path, actual_type)
        
        if not text:
            raise Exception("无法提取文本内容")
        
        task["steps"][0]["detail"] = "正在分析文本结构..."
        task["progress"] = 10
        chapters = await smart_chunk(text, source_type=source_type, file_path=source_path if source_type == "epub" else "", reading_mode=reading_mode)
        task["steps"][0]["status"] = "done"
        task["steps"][0]["detail"] = f"分章完成，共{len(chapters)}章"
        task["progress"] = 20
        
        chapter_titles = _save_chapters_to_db(course_id, chapters, reading_mode)
        db.add_learning_event(course_id, "lesson_end", {"action": "chapters_generated", "count": len(chapters)})
        
        if is_speed:
            task["steps"][1]["status"] = "processing"
            task["current_step"] = 1
            task["steps"][1]["detail"] = "正在生成知识快照..."
            task["progress"] = 25
            
            def snapshot_progress_cb(current, total, title):
                pct = 25 + int((current / total) * 45)
                short_title = title[:15] + "..." if len(title) > 15 else title
                task["steps"][1]["detail"] = f"[{current}/{total}] {short_title}"
                task["progress"] = pct
            
            try:
                result = await _run_speed_mode_postprocess(
                    course_id, chapters, chapter_titles,
                    source_type, source_path, concurrency=2,
                    progress_callback=snapshot_progress_cb,
                )
                await _persist_speed_results(course_id, result)
                task["steps"][1]["status"] = "done"
                task["steps"][1]["detail"] = f"快照 {len(result['snapshots'])} 章完成"
                task["progress"] = 70
            except Exception as e:
                import logging
                logging.warning(f"速读模式快照生成失败: {e}")
                task["steps"][1]["status"] = "done"
                task["steps"][1]["detail"] = "快照生成跳过"
                task["progress"] = 70
                result = {"snapshots": [], "syllabus_items": []}

            task["steps"][2]["status"] = "done"
            task["steps"][2]["detail"] = f"掌握项 {len(result.get('syllabus_items', []))} 条完成"
        else:
            from chunker import generate_syllabus_items
            task["steps"][1]["status"] = "processing"
            task["current_step"] = 1
            task["steps"][1]["detail"] = "正在生成掌握项..."
            
            chapters_db = db.get_chapters(course_id)
            ch_list = [(ch["title"], ch.get("content_slice", "")) for ch in chapters_db if ch.get("is_loaded", 1)]
            
            if ch_list:
                _conn = db.get_conn()
                try:
                    _conn.execute("DELETE FROM syllabus_items WHERE course_id=?", (course_id,))
                    _conn.commit()
                finally:
                    _conn.close()

                def progress_cb(current, total, message):
                    task["steps"][1]["detail"] = message
                    task["progress"] = 50 + int((current / total) * 30)

                try:
                    items = await generate_syllabus_items(course_id, ch_list, progress_callback=progress_cb)
                    if items:
                        for chapter_index, description in items:
                            db.add_syllabus_item(course_id, chapter_index, description)
                        task["steps"][1]["status"] = "done"
                        task["steps"][1]["detail"] = f"大纲生成完成，共{len(items)}个掌握项"
                    else:
                        _add_default_syllabus_items(course_id, ch_list)
                        task["steps"][1]["status"] = "done"
                        task["steps"][1]["detail"] = "大纲生成完成（使用默认项）"
                except Exception as e:
                    import logging
                    logging.error(f"[异步任务 {task_id}] 掌握项生成失败: {e}", exc_info=True)
                    _add_default_syllabus_items(course_id, ch_list)
                    task["steps"][1]["status"] = "done"
                    task["steps"][1]["detail"] = "大纲生成完成（使用默认项）"
            else:
                task["steps"][1]["status"] = "done"
                task["steps"][1]["detail"] = "无已加载章节"
            
            task["progress"] = 80
        
        task["progress"] = 100
        task["status"] = TASK_STATUS["COMPLETED"]
        task["steps"][-1]["status"] = "done"
        task["steps"][-1]["detail"] = "全部完成"
        
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
    async with _async_tasks_lock:
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
    return await route_list_courses()


@app.post("/api/courses")
async def create_course(data: dict):
    """创建课程（支持file/url/text/recommendation）"""
    result = await route_create_course(data)
    
    # 如果有内容来源，自动启动异步分章任务
    source_type = data.get("source_type", "text")
    source_path = data.get("source_path", "")
    if source_type != "recommendation" and source_path:
        task_id = str(uuid.uuid4())[:8]
        async with _async_tasks_lock:
            async_tasks[task_id] = {
                "task_id": task_id,
                "course_id": result["course_id"],
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
        import logging
        logging.info(f"[异步任务] 创建任务 {task_id} 用于课程 {result['course_id']}")
        asyncio.create_task(run_chapter_generation(task_id))
        result["task_id"] = task_id
    
    return result


@app.post("/api/courses/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传文件"""
    return await route_upload_file(file)


@app.get("/api/courses/{course_id}")
async def get_course(course_id: str):
    """获取课程详情"""
    result = await route_get_course(course_id)
    
    # 查询该课程的活跃分章任务
    active_task = None
    async with _async_tasks_lock:
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
    
    result["active_task"] = active_task
    return result


@app.delete("/api/courses/{course_id}")
async def remove_course(course_id: str):
    return await route_remove_course(course_id)


# ==================== 分章 ====================
@app.post("/api/courses/{course_id}/chapters/generate")
async def generate_chapters(course_id: str):
    """TOC-First 分章（异步执行），根据阅读模式调整处理深度"""
    return await route_generate_chapters(course_id)


@app.post("/api/courses/{course_id}/chapters/{chapter_idx}/load")
async def load_chapter_content(course_id: str, chapter_idx: int):
    """懒加载：仅加载指定章节的全文和掌握项"""
    return await route_load_chapter_content(course_id, chapter_idx)


# ==================== 掌握项 ====================
@app.post("/api/courses/{course_id}/syllabus/regenerate")
async def regenerate_syllabus(course_id: str):
    """手动重新生成掌握项"""
    return await route_regenerate_syllabus(course_id)


@app.post("/api/courses/{course_id}/syllabus/generate")
async def generate_syllabus(course_id: str):
    """生成掌握项清单"""
    return await route_generate_syllabus(course_id)


@app.get("/api/courses/{course_id}/syllabus")
async def get_syllabus(course_id: str):
    return {"syllabus": db.get_syllabus_items(course_id)}


@app.patch("/api/syllabus/{item_id}")
async def update_syllabus(item_id: int, data: dict):
    """手动勾选/取消勾选掌握项"""
    status = data.get("status", "pending")
    db.update_syllabus_item(item_id, status)
    return {"status": "ok"}


# ==================== 苏格拉底预演 ====================
@app.get("/api/courses/{course_id}/chapters/{chapter_idx}/preview")
async def generate_next_preview(course_id: str, chapter_idx: int):
    """基于当前章节内容，为下一章生成苏格拉底式预演引导"""
    return await route_generate_next_preview(course_id, chapter_idx)


# ==================== 角色管理 ====================
@app.get("/api/courses/{course_id}/roles/recommend")
async def recommend_roles(course_id: str):
    """根据课程标题推荐教师角色"""
    return await route_recommend_roles(course_id)


@app.get("/api/roles")
async def get_all_roles():
    """获取所有角色信息"""
    return await route_get_all_roles()


@app.get("/api/roles/{role_id}")
async def get_role_detail(role_id: str):
    """获取角色详情"""
    return await route_get_role_detail(role_id)


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


# ==================== 知识快照 ====================
@app.get("/api/courses/{course_id}/snapshots")
async def get_chapter_snapshots(course_id: str):
    """获取课程所有章节快照"""
    return await route_get_chapter_snapshots(course_id)


@app.post("/api/courses/{course_id}/snapshots/generate")
async def generate_snapshots(course_id: str):
    """手动生成知识快照"""
    return await route_generate_snapshots(course_id)


# ==================== 全局概览 ====================
@app.get("/api/courses/{course_id}/overview")
async def get_course_overview(course_id: str):
    """获取课程全局学习概览"""
    return await route_get_course_overview(course_id)


# ==================== 思辨笔记 ====================
@app.get("/api/courses/{course_id}/spiritual-notes")
async def get_spiritual_notes(course_id: str):
    """获取课程所有思辨笔记"""
    return await route_get_spiritual_notes(course_id)


# ==================== 掌握进度 ====================
@app.get("/api/courses/{course_id}/mastery-progress")
async def get_mastery_progress(course_id: str):
    """获取课程掌握进度"""
    return await route_get_mastery_progress(course_id)


# ==================== 对话管理 ====================
active_sessions: dict[str, "DialogueStateMachine"] = {}

try:
    from state_machine import DialogueStateMachine
except ImportError:
    DialogueStateMachine = None


@app.post("/api/courses/{course_id}/chat/start")
async def start_chat(course_id: str, data: dict):
    """开始对话会话"""
    if DialogueStateMachine is None:
        raise HTTPException(500, "状态机模块不可用")
    
    chapter_index = data.get("chapter_index", 0)
    teacher_role_id = data.get("teacher_role_id", "ganyu")
    depth = data.get("depth", "standard")
    sliders = data.get("sliders", {})

    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    session_id = db.create_session(course_id, chapter_index, teacher_role_id)

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
    """WebSocket 苏格拉底式对话"""
    await websocket.accept()

    sm = active_sessions.get(session_id)
    if not sm:
        await websocket.send_json({"error": "会话不存在或已过期"})
        await websocket.close()
        return

    try:
        await websocket.send_json({"state": "INIT", "content": "📚 正在翻开教材..."})
        await asyncio.sleep(0.3)

        share_text = ""
        async for chunk in sm.initialize():
            share_text += chunk
            await websocket.send_json({"state": "SHARE", "content": chunk})
        await websocket.send_json({"state": "SHARE_DONE"})

        await asyncio.sleep(0.2)

        probe_text = ""
        async for chunk in sm._probe():
            probe_text += chunk
            await websocket.send_json({"state": "PROBE", "content": chunk})
        await websocket.send_json({"state": "PROBE_DONE"})

        course_id = sm.course_id
        chapter_index = sm.chapter_index

        while True:
            await websocket.send_json({"state": "WAIT_USER", "timeout": 120})

            data = await websocket.receive_text()
            msg = json.loads(data)
            user_text = msg.get("content", "")

            if msg.get("action") == "end":
                async for chunk in sm._end_session("用户主动结束"):
                    await websocket.send_json({"state": "END", "content": chunk})
                break

            if msg.get("quick_mastered"):
                sm.current_round += 1
                sm.total_rounds += 1
                sm.messages.append({"role": "user", "content": user_text})
                db.add_message(session_id, "user", user_text, "USER_INPUT")
                syllabus_items = db.get_syllabus_items(course_id)
                chapter_pending = [s for s in syllabus_items
                                   if s["chapter_index"] == chapter_index
                                   and s["status"] == "pending"]
                if chapter_pending and chapter_pending[0]["id"] not in sm.session_mastered_ids:
                    db.update_syllabus_item(chapter_pending[0]["id"], "mastered")
                    sm.session_mastered_ids.add(chapter_pending[0]["id"])
                    await websocket.send_json({"state": "MASTERED_SKIPPED", "syllabus_id": chapter_pending[0]["id"]})
                await websocket.send_json({"state": "TURN_DONE"})
                await asyncio.sleep(0.2)
                async for chunk in sm._probe():
                    await websocket.send_json({"state": "PROBE", "content": chunk})
                await websocket.send_json({"state": "PROBE_DONE"})
                continue

            full_response = ""
            async for chunk in sm.handle_user_input(user_text):
                full_response += chunk
                current_state = sm.state
                await websocket.send_json({"state": current_state, "content": chunk})

            await websocket.send_json({"state": "TURN_DONE"})

            if sm.state == sm.END:
                await websocket.send_json({"state": "SESSION_END"})
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"error": "网络好像有点问题，要不我们换个话题试试？"})
        except Exception:
            pass
    finally:
        active_sessions.pop(session_id, None)
        try:
            await websocket.close()
        except Exception:
            pass


# ==================== 划词问答 ====================
from llm_client import llm

@app.post("/api/annotate")
async def annotate_ask(data: dict):
    """划词问答（独立API，Temperature=0.3）"""
    course_id = data.get("course_id", "")
    session_id = data.get("session_id", "")
    quoted_text = data.get("quoted_text", "")
    question = data.get("question", "")

    if not quoted_text or not question:
        raise HTTPException(400, "缺少引用文本或问题")

    messages = [
        {"role": "system", "content": "你是一个冷静助教。根据用户引用的文本和问题，给出简洁、准确的解答。不要角色扮演。Temperature自动设为0.3。"},
        {"role": "user", "content": f"引用文本：\n{quoted_text}\n\n问题：{question}"},
    ]

    answer = await llm.chat(messages, temperature=0.3, max_tokens=500)

    if course_id:
        db.add_annotation(course_id, session_id, quoted_text, question, answer)
        db.add_learning_event(course_id, "annotation_ask", {"session": session_id})

    return {"quoted_text": quoted_text, "question": question, "answer": answer}


@app.get("/api/courses/{course_id}/annotations")
async def get_course_annotations(course_id: str):
    return {"annotations": db.get_annotations(course_id)}


# ==================== 日记/群聊/总结 ====================
@app.get("/api/courses/{course_id}/diaries")
async def get_diaries(course_id: str):
    return await route_get_diary_entries(course_id)


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


# ==================== 答辩记录 ====================
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
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]

        if not user_msgs and not assistant_msgs:
            continue

        is_active = s.get("ended_at") is None and s.get("total_rounds", 0) > 0

        diaries_list = []
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

        chapter_title = ""
        ch = db.get_chapter(course_id, s["chapter_index"])
        if ch:
            chapter_title = ch.get("title", "")

        duration_minutes = 0
        started_at = s.get("started_at")
        ended_at = s.get("ended_at")
        if started_at and ended_at:
            try:
                start = datetime.fromisoformat(str(started_at)[:19])
                end = datetime.fromisoformat(str(ended_at)[:19])
                duration_minutes = max(1, int((end - start).total_seconds() // 60))
            except (ValueError, TypeError, OverflowError):
                duration_minutes = (s.get("total_rounds") or 0) * 2
        else:
            duration_minutes = (s.get("total_rounds") or 0) * 2

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
            "is_active": is_active,
        })

    defense_records = db.get_defense_records(course_id)
    return {"history": history, "defense_records": defense_records}


# ==================== 单次会话详情 ====================
@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """获取单次会话的完整详情"""
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

    chapter_title = ""
    ch = db.get_chapter(course_id, session["chapter_index"])
    if ch:
        chapter_title = ch.get("title", "")

    messages = db.get_messages(session_id)
    dialogue_messages = [m for m in messages if m["role"] in ("user", "assistant")]

    teacher_meta = ROLES_META.get(session["teacher_role_id"], {})
    teacher_info = {
        "id": session["teacher_role_id"],
        "name": teacher_meta.get("name", session["teacher_role_id"]),
        "emoji": teacher_meta.get("emoji", "🎓"),
        "style": teacher_meta.get("style", ""),
    }

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

    duration_minutes = 0
    if session.get("ended_at") and session.get("started_at"):
        try:
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
    progress = db.get_syllabus_progress(course_id)
    if progress["total"] == 0 or progress["percent"] < 100:
        raise HTTPException(400, "进度未达100%，不能申请结业答辩")

    syllabus = db.get_syllabus_items(course_id)
    course = db.get_course(course_id)

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

        remaining = [s for s in syllabus if s not in selected]
        random.shuffle(remaining)
        while len(selected) < 3 and remaining:
            selected.append(remaining.pop())

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

    certificate = None
    if all_passed:
        course = db.get_course(course_id)
        profile = db.get_profile(course_id)
        teacher_id = course.get("current_teacher", "ganyu") if course else "ganyu"

        strengths_text = "、".join(profile.get("strengths", ["待总结"])[:3]) if profile else "顺利完成课程"
        weaknesses_text = "、".join(profile.get("weaknesses", ["继续加油"])[:3]) if profile else "继续努力"

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


# ==================== LLM设置（使用统一配置管理器） ====================
@app.get("/api/settings/llm")
async def get_llm_settings():
    """获取 LLM 配置（使用统一配置管理器）"""
    return await route_get_llm_config()


@app.post("/api/settings/llm")
async def update_llm_settings(data: dict):
    """更新 LLM 配置（使用统一配置管理器）"""
    # 更新运行时配置
    updates = {}
    if "api_key" in data and data["api_key"]:
        updates["DEFAULT_MODEL"] = data.get("model", updates.get("DEFAULT_MODEL"))
    if "base_url" in data and data["base_url"]:
        updates["DEFAULT_MODEL"] = data.get("model", updates.get("DEFAULT_MODEL"))
    
    # 同步到数据库
    sync_config_to_db()
    # 同步到 llm 单例
    _sync_llm_from_db()
    
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
    if was_active:
        remaining = db.get_all_llm_providers()
        if remaining:
            db.set_active_provider(remaining[0]["id"])
            models = db.get_llm_models(remaining[0]["id"])
            if models:
                db.set_active_model(models[0]["id"])
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
    """将数据库活跃配置同步到 llm 单例和 LLM_CONFIG（统一入口）"""
    active = db.get_active_model_with_provider()
    if active:
        LLM_CONFIG["api_key"] = active["api_key"]
        LLM_CONFIG["base_url"] = active["base_url"]
        LLM_CONFIG["model"] = active["model_name"]
        llm.api_key = active["api_key"]
        llm.base_url = active["base_url"].rstrip("/")
        llm.model = active["model_name"]


# ==================== 课程设置 ====================
@app.post("/api/courses/{course_id}/settings")
async def update_course_settings_route(course_id: str, data: dict):
    """更新课程设置"""
    return await api_update_course_settings(
        course_id,
        reading_mode=data.get("reading_mode"),
        depth=data.get("depth"),
        duration=data.get("duration"),
    )


@app.get("/api/courses/{course_id}/settings")
async def get_course_settings_route(course_id: str):
    """获取课程设置"""
    return await api_get_course_settings(course_id)


# ==================== 学习事件 ====================
@app.post("/api/courses/{course_id}/events")
async def add_learning_event_route(course_id: str, data: dict):
    """添加学习事件"""
    return await api_add_learning_event(course_id, data.get("event_type", ""), data)


@app.get("/api/courses/{course_id}/events")
async def get_learning_events_route(course_id: str):
    """获取学习事件"""
    return await api_get_learning_events(course_id)


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

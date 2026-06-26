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
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import aiofiles

from config import (
    HOST, PORT, STATIC_DIR, DATA_DIR, ROLES_META,
    DEFAULT_SLIDERS, DEPTH_CONFIG, DURATION_OPTIONS,
    ROLE_RECOMMENDATION, DEFAULT_ROLES,
)
from database import init_db
import database as db
from chunker import extract_text, smart_chunk, generate_syllabus_items, generate_course_summary
from state_machine import DialogueStateMachine
from llm_client import llm

def _utc(dt):
    return (dt + 'Z') if dt else dt

def _utc_dict(d, field):
    if not d:
        return d
    r = dict(d)
    if field in r and r[field]:
        r[field] = _utc(r[field])
    return r

def _utc_list(items, field):
    return [_utc_dict(item, field) for item in items] if items else []


# ==================== 初始化 ====================
app = FastAPI(title="问渠（Wenqu）v1.1", version="1.1.0")

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


# ==================== 前端路由 ====================
@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
        return HTMLResponse(content)
    return HTMLResponse("<h1>问渠 v1.1</h1><p>前端页面未找到，请确保static/index.html存在。</p>")


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.1.0"}


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
    title = data.get("title", "")
    source_type = data.get("source_type", "text")
    source_path = data.get("source_path", "")
    content_text = data.get("content_text", "")
    course_id = db.create_course(title, source_type, source_path)
    db.add_learning_event(course_id, "login", {"action": "create_course"})

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

    return {"course_id": course_id, "title": title, "source_type": source_type}


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
    }


@app.delete("/api/courses/{course_id}")
async def remove_course(course_id: str):
    db.delete_course(course_id)
    return {"status": "deleted"}


# ==================== 分章 ====================
@app.post("/api/courses/{course_id}/chapters/generate")
async def generate_chapters(course_id: str):
    """三级回退分章（异步执行）"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    source_path = course.get("source_path", "")
    source_type = course.get("source_type", "")

    # 提取文本（text类型映射为txt）
    actual_type = source_type
    if source_type == "text":
        actual_type = "txt"
    text = await extract_text(source_path, actual_type)

    if not text:
        raise HTTPException(400, "无法提取文本内容")

    # 智能分章
    chapters = await smart_chunk(text)

    # 保存章节到数据库
    for idx, (title, content) in enumerate(chapters):
        db.add_chapter(course_id, idx, title, content[:5000])

    db.add_learning_event(course_id, "lesson_end", {"action": "chapters_generated", "count": len(chapters)})

    return {
        "total_chapters": len(chapters),
        "chapters": [{"idx": i, "title": t} for i, (t, _) in enumerate(chapters)],
    }


@app.post("/api/courses/{course_id}/syllabus/generate")
async def generate_syllabus(course_id: str):
    """生成掌握项清单"""
    chapters = db.get_chapters(course_id)
    if not chapters:
        raise HTTPException(400, "请先生成分章")

    ch_list = [(ch["title"], ch.get("content_slice", "")) for ch in chapters]
    items = await generate_syllabus_items(course_id, ch_list)

    for chapter_index, description in items:
        db.add_syllabus_item(course_id, chapter_index, description)

    return {"total_items": len(items)}


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


@app.get("/api/sessions/{session_id}")
async def get_session_info(session_id: str):
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

        history.append({
            "session": _utc_dict(_utc_dict(s, "started_at"), "ended_at"),
            "message_count": len(messages),
            "user_message_count": len(user_msgs),
            "assistant_message_count": len(assistant_msgs),
            "diaries": _utc_list(diaries_list, "created_at"),
            "group_chats": _utc_list(group_chats, "created_at"),
            "summaries": _utc_list(summaries_list, "created_at"),
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
        middle = [s for s in syllabus if s["chapter_index"] == (course["total_chapters"] // 2) if s not in first]
        last = [s for s in syllabus if s["chapter_index"] == course["total_chapters"] - 1 if s not in first]

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
    return {"status": "ok"}


# ==================== 健康检查 ====================
@app.get("/api/config")
async def get_config():
    """获取系统配置信息"""
    return {
        "roles": list(ROLES_META.keys()),
        "depth_options": list(DEPTH_CONFIG.keys()),
        "duration_options": DURATION_OPTIONS,
        "version": "1.1.0",
    }


# ==================== 启动 ====================
if __name__ == "__main__":
    import uvicorn
    print(f"🌊 问渠（Wenqu）v1.1 启动中...")
    print(f"📚 访问地址：http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")

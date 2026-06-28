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
from chunker import extract_text, smart_chunk, generate_syllabus_items, generate_course_summary, extract_chapter_snapshot, generate_global_highlights, generate_speed_read_syllabus
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
        return HTMLResponse(content)
    return HTMLResponse(f"<h1>问渠 v{VERSION}</h1><p>前端页面未找到，请确保static/index.html存在。</p>")


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

    # 速读模式：全量章节直接设为懒加载占位
    is_speed = reading_mode == "speed"
    max_loaded = 3 if reading_mode == "deep" else 99  # 研读只预加载前3章

    # 保存章节到数据库
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

        # 决定是否已加载
        if is_speed:
            is_loaded = 0
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

    db.add_learning_event(course_id, "lesson_end", {"action": "chapters_generated", "count": len(chapters)})

    # 速读模式：额外生成知识快照和全局精华
    if is_speed:
        import logging
        logging.info(f"[速读模式] 开始生成知识快照，共 {len(chapters)} 章")
        try:
            # 为每章生成知识快照
            snapshots = []
            for chapter_item in chapters:
                # 从 meta 中获取实际的 chapter_index（与 chapters 表一致）
                if isinstance(chapter_item, tuple) and len(chapter_item) >= 3:
                    title, content, meta = chapter_item[0], chapter_item[1], chapter_item[2]
                    chapter_idx = meta.get("idx", 0)
                elif isinstance(chapter_item, tuple) and len(chapter_item) >= 2:
                    title, content = chapter_item[0], chapter_item[1]
                    chapter_idx = 0
                else:
                    continue

                logging.info(f"[速读模式] 处理第 {chapter_idx} 章: {title[:30]}")

                # 速读模式：从 EPUB 提取更多内容用于快照（不限于首尾段）
                snapshot_content = ""
                if source_type == "epub" and source_path:
                    from chunker import extract_toc_from_epub, extract_chapter_content_by_href
                    toc_items, _ = await extract_toc_from_epub(source_path)
                    for toc_item in toc_items:
                        if toc_item.get("idx") == chapter_idx:
                            href = toc_item.get("href", "")
                            if href:
                                snapshot_content = await extract_chapter_content_by_href(source_path, href)
                            break

                # 如果没有从 EPUB 提取到内容，使用 chapters 中已有内容
                if not snapshot_content:
                    snapshot_content = content

                if not snapshot_content:
                    logging.warning(f"[速读模式] 第 {chapter_idx} 章无内容")
                    continue

                snapshot = await extract_chapter_snapshot(snapshot_content, title)
                logging.info(f"[速读模式] 快照结果: keywords={len(snapshot.get('keywords', []))}, viewpoint={snapshot.get('core_viewpoint', '')[:30]}, importance={snapshot.get('importance', 0)}, fallback={snapshot.get('_fallback', False)}")
                snapshots.append(snapshot)
                db.add_chapter_snapshot(
                    course_id, chapter_idx,
                    json.dumps(snapshot.get("keywords", []), ensure_ascii=False),
                    snapshot.get("core_viewpoint", ""),
                    global_priority=snapshot.get("importance", 0),  # 用重要性作为优先级
                    learning_goal=snapshot.get("learning_goal", ""),
                    importance=snapshot.get("importance", 0),
                    difficulty=snapshot.get("difficulty", ""),
                )

            # 全局精华提炼（只做一次）
            if snapshots:
                logging.info(f"[速读模式] 生成全局精华，共 {len(snapshots)} 个快照")
                highlights = await generate_global_highlights(course_id, snapshots, chapter_titles)
                db.add_global_highlights(
                    course_id,
                    json.dumps(highlights.get("key_points", []), ensure_ascii=False),
                    json.dumps(highlights.get("chapter_priorities", []), ensure_ascii=False),
                    highlights.get("relationships", ""),
                    json.dumps(highlights.get("core_chapter_indices", []), ensure_ascii=False),
                    json.dumps(highlights.get("chapter_dependencies", {}), ensure_ascii=False),
                )

                # 速读模式：生成精简掌握项（只提取最重要的20%知识点）
                logging.info(f"[速读模式] 开始生成精华掌握项")
                try:
                    syllabus_items = await generate_speed_read_syllabus(course_id, chapters, snapshots)
                    for ch_idx, desc in syllabus_items:
                        db.add_syllabus_item(course_id, ch_idx, desc)
                    logging.info(f"[速读模式] 完成，生成了 {len(syllabus_items)} 条精华掌握项")
                except Exception as syllabus_err:
                    logging.warning(f"[速读模式] 精华掌握项生成失败: {syllabus_err}")

            logging.info(f"[速读模式] 完成，生成了 {len(snapshots)} 个快照")
            return {
                "total_chapters": len(chapters),
                "snapshots_generated": len(snapshots),
                "highlights_generated": bool(snapshots),
                "reading_mode": "speed",
                "chapters": [
                    {"idx": c[2].get("idx", i), "title": c[0], "is_loaded": False}
                    if isinstance(c, tuple) and len(c) >= 3 else
                    {"idx": i, "title": c[0] if isinstance(c, tuple) else str(c), "is_loaded": False}
                    for i, c in enumerate(chapters)
                ],
            }
        except Exception as e:
            # 快照生成失败不影响主流程
            logging.warning(f"速读模式快照生成失败: {e}")
            import traceback
            logging.warning(traceback.format_exc())
            return {
                "total_chapters": len(chapters),
                "snapshots_generated": 0,
                "highlights_generated": False,
                "reading_mode": "speed",
                "chapters": [
                    {"idx": c[2].get("idx", i), "title": c[0], "is_loaded": False}
                    if isinstance(c, tuple) and len(c) >= 3 else
                    {"idx": i, "title": c[0] if isinstance(c, tuple) else str(c), "is_loaded": False}
                    for i, c in enumerate(chapters)
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

    items = await generate_syllabus_items(course_id, ch_list)

    for chapter_index, description in items:
        db.add_syllabus_item(course_id, chapter_index, description)

    return {"total_items": len(items)}


# ==================== 苏格拉底预演 ====================
@app.get("/api/courses/{course_id}/chapters/{chapter_idx}/preview")
async def generate_next_preview(course_id: str, chapter_idx: int):
    """基于当前章节内容，为下一章生成苏格拉底式预演引导"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    next_chapter = db.get_chapter(course_id, chapter_idx + 1)
    if not next_chapter:
        return {"preview": "", "next_title": ""}

    current_chapter = db.get_chapter(course_id, chapter_idx)
    if not current_chapter:
        return {"preview": "", "next_title": next_chapter.get("title", "")}

    current_content = current_chapter.get("content_slice", "")
    if not current_content:
        return {"preview": "", "next_title": next_chapter.get("title", "")}

    # 用轻量模型生成预演引导
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
        from llm_client import multi_llm
        result = await multi_llm.chat_json("fast", [
            {"role": "system", "content": "你是苏格拉底式学习引导者。返回JSON。"},
            {"role": "user", "content": prompt},
        ])
        questions = result.get("questions", [])
    except Exception:
        questions = []

    return {
        "preview": "根据上一章的底层逻辑，下一章" + next_chapter["title"] + "极大概率会涉及相关深化内容。",
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

    # 速读模式：使用章节快照数量作为统计
    if reading_mode == "speed":
        snapshots = db.get_chapter_snapshots(course_id)
        highlights = db.get_global_highlights(course_id)

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
            })

        # === 智能三级推荐学习顺序 ===
        import re

        def parse_chapter_num(s: str) -> int:
            """从'第N章'中提取数字"""
            m = re.search(r'第(\d+)章', s)
            return int(m.group(1)) - 1 if m else -1

        # 解析核心章节和依赖关系
        core_indices = set()
        if highlights:
            for s in highlights.get("core_chapter_indices", []):
                idx = parse_chapter_num(s)
                if idx >= 0:
                    core_indices.add(idx)

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

        # 1. 已学习章节：按学习顺序
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

        # 2. 核心未学章节：基于依赖关系+重要性排序
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

        # 拓扑排序：优先推荐依赖已满足的章节
        def sort_by_deps(items):
            """按依赖关系排序：依赖已满足的排前面"""
            return sorted(items, key=lambda x: (not x["deps_satisfied"], -x["importance"], x["idx"]))

        core_unlearned = sort_by_deps(core_unlearned)

        # 3. 可选章节：非核心章节
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

        # 构建最终推荐数据结构
        recommended_order = {
            "core_count": len(core_indices),
            "total_count": len(chapters),
            "learned": learned_list,
            "core_to_learn": core_unlearned,  # 核心待学
            "optional": optional_list,  # 可选
            "next_chapter": core_unlearned[0] if core_unlearned else (optional_list[0] if optional_list else None),
        }

        # 薄弱环节：未学习章节（简化展示）
        weak_areas = [
            {
                "id": ch["idx"],
                "description": f"第{ch['idx'] + 1}章「{ch['title']}」",
                "chapter_index": ch["idx"],
                "status": "未学习",
                "is_core": ch["idx"] in core_indices
            }
            for ch in chapters if ch["idx"] not in learned_indices
        ][:5]

        # 知识覆盖率：核心章节的完成度
        core_total = len(core_indices) if core_indices else len(chapters)
        core_learned = sum(1 for ch in chapters if ch["idx"] in core_indices and ch["idx"] in learned_indices)
        core_percent = round(core_learned / core_total * 100, 1) if core_total > 0 else 0

        total_points = len(snapshots)
        mastered_points = len(snapshots)
        percent = 100.0 if total_points > 0 else 0.0

        strategy = {
            "speed": "精华提炼模式 — 聚焦最重要的20%知识点",
            "standard": "系统学习模式 — 逐章覆盖全部知识点",
            "deep": "辩证分析模式 — 深度理解+批判性思考",
        }

        # 智能建议
        next_ch = recommended_order["next_chapter"]
        if total_points == 0:
            recommendation = "正在生成知识快照，请稍候..."
        elif next_ch:
            recommendation = f"建议先学核心章节，下一站：第{next_ch['idx']+1}章「{next_ch['title']}」"
        else:
            recommendation = f"🎉 核心章节已学完！共掌握{core_learned}/{core_total}个核心章节（{core_percent}%）"

        return {
            "reading_mode": reading_mode,
            "strategy_description": strategy.get(reading_mode, strategy["standard"]),
            "knowledge_coverage": {
                "total_points": total_points,
                "mastered_points": mastered_points,
                "percent": percent,
                "total_chapters": len(chapters),
                "core_chapters": core_total,
                "core_learned": core_learned,
                "core_percent": core_percent,
            },
            "chapter_stats": chapter_stats,
            "recommended_order": recommended_order,  # 新的三级结构
            "weak_areas": weak_areas,
            "recommendation": recommendation,
            "highlights": highlights,
        }

    # 细读/研读模式：使用掌握项统计
    total_points = len(syllabus)
    mastered_points = sum(1 for s in syllabus if s["status"] == "mastered")

    # 章节维度统计
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

    strategy = {
        "speed": "精华提炼模式 — 聚焦最重要的20%知识点",
        "standard": "系统学习模式 — 逐章覆盖全部知识点",
        "deep": "辩证分析模式 — 深度理解+批判性思考",
    }

    # 识别薄弱环节：未掌握的知识点
    pending = [s for s in syllabus if s["status"] == "pending"]
    weak_areas = [
        {"id": s["id"], "description": s["description"], "chapter_index": s["chapter_index"], "status": "未掌握"}
        for s in pending[:5]
    ]

    # 智能学习顺序推荐：按章节掌握度推荐
    # 优先推荐：未开始的章节 > 进行中的章节 > 已掌握的章节
    recommended_order = []
    for ch in chapters:
        ch_items = [s for s in syllabus if s["chapter_index"] == ch["idx"]]
        mastered = sum(1 for s in ch_items if s["status"] == "mastered")
        total = len(ch_items)
        
        if total == 0:
            continue
        
        mastered_ratio = mastered / total if total > 0 else 0
        if mastered_ratio == 0:
            priority = "high"  # 未开始
        elif mastered_ratio < 1:
            priority = "medium"  # 进行中
        else:
            priority = "low"  # 已完成
        
        recommended_order.append({
            "idx": ch["idx"],
            "title": ch["title"],
            "priority": priority,
            "progress": f"{mastered}/{total}",
            "status": "已完成" if mastered_ratio == 1 else ("进行中" if mastered_ratio > 0 else "未开始"),
        })

    # 按优先级排序
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recommended_order.sort(key=lambda x: (priority_order.get(x["priority"], 3), x["idx"]))

    # 生成下一步行动建议
    percent = round(mastered_points / max(total_points, 1) * 100, 1)
    
    # 找到下一个推荐章节
    next_chapter = next((ch for ch in recommended_order if ch["status"] != "已完成"), None)
    
    if percent < 30:
        recommendation = f"建议从第{next_chapter['idx'] + 1}章「{next_chapter['title']}」开始学习"
    elif percent < 50:
        recommendation = f"继续学习第{next_chapter['idx'] + 1}章「{next_chapter['title']}」，完成更多掌握项"
    elif percent < 100:
        recommendation = f"还剩{len(pending)}个知识点未完成，继续加油！"
    else:
        recommendation = "恭喜！可以申请结业答辩了"

    return {
        "reading_mode": reading_mode,
        "strategy_description": strategy.get(reading_mode, strategy["standard"]),
        "knowledge_coverage": {
            "total_points": total_points,
            "mastered_points": mastered_points,
            "percent": percent,
        },
        "chapter_stats": chapter_stats,
        "recommended_order": recommended_order[:5],  # 推荐前5章
        "weak_areas": weak_areas,
        "recommendation": recommendation,
        "next_chapter": next_chapter,  # 下一个推荐章节
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

    snapshots = []
    failed_chapters = []

    for ch in chapters:
        # 加载章节内容（多种来源）
        content = ch.get("content_slice", "") or ch.get("content_full", "")

        # 如果内容太短且是EPUB，从EPUB重新加载完整内容
        if source_type == "epub" and source_path and len(content) < 1000:
            try:
                from chunker import extract_chapter_content_by_href
                # 找到对应的href
                href = ch.get("href", "")
                if href:
                    full_content = await extract_chapter_content_by_href(source_path, href)
                    if full_content:
                        content = full_content
            except Exception as e:
                import logging
                logging.warning(f"懒加载章节 {ch['idx']} 失败: {e}")

        if not content or len(content.strip()) < 50:
            failed_chapters.append(ch["idx"])
            continue

        try:
            snapshot = await extract_chapter_snapshot(content, ch["title"])
            snapshots.append(snapshot)
            db.add_chapter_snapshot(
                course_id, ch["idx"],
                json.dumps(snapshot.get("keywords", []), ensure_ascii=False),
                snapshot.get("core_viewpoint", ""),
                global_priority=snapshot.get("importance", 0),
                learning_goal=snapshot.get("learning_goal", ""),
                importance=snapshot.get("importance", 0),
                difficulty=snapshot.get("difficulty", ""),
            )
        except Exception as e:
            import logging
            logging.warning(f"生成章节 {ch['idx']} 快照失败: {e}")
            failed_chapters.append(ch["idx"])

    # 全局精华提炼
    highlights_result = None
    if snapshots:
        chapter_titles = [ch["title"] for ch in chapters if ch["idx"] not in failed_chapters]
        try:
            highlights = await generate_global_highlights(course_id, snapshots, chapter_titles)
            db.add_global_highlights(
                course_id,
                json.dumps(highlights.get("key_points", []), ensure_ascii=False),
                json.dumps(highlights.get("chapter_priorities", []), ensure_ascii=False),
                highlights.get("relationships", ""),
                json.dumps(highlights.get("core_chapter_indices", []), ensure_ascii=False),
                json.dumps(highlights.get("chapter_dependencies", {}), ensure_ascii=False),
            )
            highlights_result = {
                "key_points_count": len(highlights.get("key_points", [])),
                "core_chapters_count": len(highlights.get("core_chapter_indices", [])),
                "is_fallback": highlights.get("_fallback", False),
            }
        except Exception as e:
            import logging
            logging.error(f"生成全局精华失败: {e}")

    # 速读模式：生成精华掌握项
    syllabus_count = 0
    if snapshots:
        try:
            from chunker import generate_speed_read_syllabus
            # 转换 chapters 格式给 generate_speed_read_syllabus
            chapter_list = []
            for ch in chapters:
                content = ch.get("content_slice", "") or ch.get("content_full", "")
                chapter_list.append((ch["title"], content, {"idx": ch["idx"]}))
            syllabus_items = await generate_speed_read_syllabus(course_id, chapter_list, snapshots)
            for ch_idx, desc in syllabus_items:
                db.add_syllabus_item(course_id, ch_idx, desc)
            syllabus_count = len(syllabus_items)
        except Exception as e:
            import logging
            logging.error(f"生成精华掌握项失败: {e}")

    return {
        "snapshots_generated": len(snapshots),
        "snapshots_failed": failed_chapters,
        "highlights": highlights_result,
        "syllabus_generated": syllabus_count,
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
    data = await request.json()
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

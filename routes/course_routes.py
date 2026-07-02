"""
问渠 (Wenqu) v1.1 — 课程管理路由模块

负责课程 CRUD、文件上传、分章、掌握项生成等。
"""

import json
import os
import asyncio
import uuid
import re
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import database as db
from config import (
    STATIC_DIR, DATA_DIR, ROLES_META,
    DEFAULT_SLIDERS, DEPTH_CONFIG, DURATION_OPTIONS,
    ROLE_RECOMMENDATION, DEFAULT_ROLES, LLM_CONFIG, VERSION,
    READING_MODE_CONFIG, DEFAULT_READING_MODE,
    MODEL_TIER_CONFIG, DEFAULT_MODEL_TIER,
)
from chunker import (
    extract_text, smart_chunk, generate_syllabus_items, generate_course_summary,
    extract_chapter_snapshot, extract_chapter_snapshots_batch,
    generate_global_highlights,
)
from llm_client import llm

logger = logging.getLogger(__name__)


# v1.5 优化: 预编译正则，避免每次调用重复编译
_CHAPTER_NUM_RE = re.compile(r'第(\d+)章')


def _parse_chapter_num(s: str) -> int:
    """从"第N章"格式解析章节号（0-based）"""
    m = _CHAPTER_NUM_RE.search(str(s))
    return int(m.group(1)) - 1 if m else -1


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
    """获取课程所有群聊记录（按课程ID查询）"""
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


def _save_chapters_to_db(course_id: str, chapters: list, reading_mode: str) -> list:
    """保存章节到数据库（统一入口）"""
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
    """生成默认掌握项（当LLM生成失败时使用）

    chapters 元素支持两种格式：
      - (chapter_index, title, content)：三元组，用真实 chapter_index
      - (title, content)：二元组，回退到 enumerate 顺序 idx（向后兼容）
    """
    defaults = [
        "能用自己的话复述本章的核心观点",
        "能解释本章涉及的关键概念",
        "能用自己的话举例说明本章内容",
    ]
    for i, item in enumerate(chapters):
        if isinstance(item, tuple) and len(item) >= 3:
            ch_idx = item[0]
        else:
            ch_idx = i
        for desc in defaults:
            db.add_syllabus_item(course_id, ch_idx, desc)


async def _run_speed_mode_postprocess(
    course_id: str,
    chapters: list,
    chapter_titles: List[str],
    source_type: str,
    source_path: str,
    concurrency: int = 2,
    progress_callback=None,
) -> dict:
    """速读模式后处理（统一函数）"""
    import asyncio as _asyncio

    # Step 1: 准备 chapter_idx/content/title
    chapter_data: List[tuple] = []
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

    # Step 2: 并发生成快照
    snapshot_by_idx: dict = {}

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
                progress_callback=progress_callback,
            )
        except Exception as e:
            logger.warning(f"[speed] 批量快照失败，回退串行: {e}")
            snapshot_by_idx = {}

        if not snapshot_by_idx:
            total = len(chapters_for_snapshot)
            for i, (ch_idx, title, snapshot_content) in enumerate(chapters_for_snapshot):
                if not snapshot_content:
                    continue
                try:
                    snap = await extract_chapter_snapshot(snapshot_content, title)
                    snapshot_by_idx[ch_idx] = snap
                except Exception as e:
                    logger.warning(f"[speed] 串行快照失败 [{title[:20]}]: {e}")
                if progress_callback:
                    progress_callback(i + 1, total, title)
    else:
        try:
            snapshot_by_idx = await extract_chapter_snapshots_batch(
                chapter_data, concurrency=concurrency,
                progress_callback=progress_callback,
            )
        except Exception as e:
            logger.warning(f"[speed] 批量快照失败，回退串行: {e}")
            snapshot_by_idx = {}

        if not snapshot_by_idx:
            total = len(chapter_data)
            for i, (ch_idx, title, content) in enumerate(chapter_data):
                if not content:
                    continue
                try:
                    snap = await extract_chapter_snapshot(content, title)
                    snapshot_by_idx[ch_idx] = snap
                except Exception as e:
                    logger.warning(f"[speed] 串行快照失败 [{title[:20]}]: {e}")
                if progress_callback:
                    progress_callback(i + 1, total, title)

    snapshots = list(snapshot_by_idx.values())

    # Step 3: 全局精华
    highlights: dict = {}
    if snapshots:
        try:
            highlights = await generate_global_highlights(course_id, snapshots, chapter_titles)
        except Exception as e:
            logger.warning(f"[speed] 全局精华生成失败: {e}")
            highlights = {}

    # Step 4: 精华掌握项
    syllabus_items: List[tuple] = []
    key_points = highlights.get("key_points", []) if isinstance(highlights, dict) else []

    if key_points:
        sorted_snaps = sorted(
            snapshot_by_idx.items(),
            key=lambda kv: kv[1].get("importance", 3),
            reverse=True,
        )
        kp_count = min(len(key_points), 15)

        def _calc_importance(pos: int, total: int) -> int:
            if total <= 0:
                return 3
            if pos < max(1, total * 0.3):
                return 5
            if pos < total * 0.7:
                return 4
            return 3

        for i, kp in enumerate(key_points[:kp_count]):
            if sorted_snaps:
                target_ch_idx = sorted_snaps[i % len(sorted_snaps)][0]
            else:
                target_ch_idx = 0
            importance = _calc_importance(i, kp_count)
            syllabus_items.append((target_ch_idx, kp, importance))

    # Step 5: fallback（无任何 key_points 时的兜底）
    if not syllabus_items and snapshots:
        for ch_idx, snap in snapshot_by_idx.items():
            goal = (snap.get("learning_goal", "") or "").strip()
            if goal:
                syllabus_items.append((ch_idx, goal, 3))
            if len(syllabus_items) >= 10:
                break

    # Step 5.5: 正文章节保底（速读精华可能遗漏正文章节）
    # 确保重要的正文章节至少有 1 条掌握项，避免用户看到"无掌握项"以为是 bug
    if syllabus_items and snapshot_by_idx:
        covered_chapters = set(item[0] for item in syllabus_items)
        fallback_count = 0
        MAX_FALLBACK = 5  # 最多补5章，保持速读"精华"定位

        # 预定义元数据章节标题关键词（这些章节可以被舍弃）
        metadata_keywords = ["序", "前言", "附录", "推荐", "版权", "目录", "引言", "导言"]

        # 按 importance 降序遍历，优先补充重要章节
        for ch_idx, snap in sorted(snapshot_by_idx.items(),
                                   key=lambda kv: kv[1].get("importance", 0), reverse=True):
            if ch_idx in covered_chapters:
                continue

            # 从章节列表获取标题，判断是否为元数据
            ch_title = ""
            for ci, ct, _ in chapter_data:
                if ci == ch_idx:
                    ch_title = ct
                    break

            if any(kw in ch_title for kw in metadata_keywords):
                continue

            goal = (snap.get("learning_goal", "") or "").strip()
            if goal and fallback_count < MAX_FALLBACK:
                syllabus_items.append((ch_idx, goal, 3))
                fallback_count += 1

    return {
        "snapshots": snapshots,
        "snapshots_by_idx": snapshot_by_idx,
        "highlights": highlights,
        "syllabus_items": syllabus_items,
    }


async def _persist_speed_results(course_id: str, result: dict) -> None:
    """把 speed 模式后处理结果写入数据库"""
    snapshots_by_idx = result.get("snapshots_by_idx", {})

    conn = db.get_conn()
    try:
        conn.execute("BEGIN TRANSACTION")

        try:
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

            syllabus_items = result.get("syllabus_items", [])
            if syllabus_items:
                try:
                    conn.execute("DELETE FROM syllabus_items WHERE course_id=?", (course_id,))
                    for item in syllabus_items:
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

            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"[speed] 事务失败，已回滚: {e}")
            raise

    finally:
        conn.close()


# ==================== 路由函数 ====================

async def api_list_courses():
    """获取所有课程"""
    courses = db.get_all_courses()
    for c in courses:
        progress = db.get_syllabus_progress(c["id"])
        c["progress"] = progress
    return courses


async def api_create_course(data: dict):
    """创建课程（支持file/url/text/recommendation）"""
    source_type = data.get("source_type", "text")
    source_path = data.get("source_path", "")
    content_text = data.get("content_text", "")
    reading_mode = data.get("reading_mode", DEFAULT_READING_MODE)

    title = data.get("title", "")
    if not title and source_path:
        if source_type == "upload" or "/" in source_path or "\\" in source_path:
            import os as _os
            filename = _os.path.basename(source_path)
            for ext in [".txt", ".md", ".markdown", ".epub"]:
                if filename.lower().endswith(ext):
                    title = filename[:-len(ext)]
                    break
            if not title:
                title = filename
        elif source_type == "url":
            try:
                from urllib.parse import urlparse as _urlparse
                parsed = _urlparse(source_path)
                path = parsed.path.strip("/")
                if path:
                    fname = _os.path.basename(path)
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
            lines = content_text.strip().split("\n")
            first_line = lines[0].strip() if lines else ""
            title = first_line[:50] if first_line else "文本文档"

    if not title:
        title = "未命名课程"

    course_id = db.create_course(title, source_type, source_path, reading_mode)
    db.add_learning_event(course_id, "login", {"action": "create_course"})

    depth_map = {"speed": "basic", "standard": "standard", "deep": "deep"}
    db.update_course_depth(course_id, depth_map.get(reading_mode, "standard"))

    uploaded_path = ""
    if source_type == "text" and content_text:
        text_path = DATA_DIR / "uploads" / f"{course_id}_text.txt"
        async with __import__('aiofiles').open(text_path, "w", encoding="utf-8") as f:
            await f.write(content_text)
        uploaded_path = str(text_path)
        import database as _db
        conn = _db.get_conn()
        try:
            conn.execute("UPDATE courses SET source_path=? WHERE id=?", (uploaded_path, course_id))
            conn.commit()
        finally:
            conn.close()

    result = {"course_id": course_id, "title": title, "source_type": source_type}
    return result


async def api_upload_file(file: UploadFile = File(...)):
    """上传文件"""
    ext = Path(file.filename).suffix.lower()
    type_map = {
        ".pdf": "pdf", ".epub": "epub",
        ".md": "md", ".markdown": "md",
        ".txt": "txt",
    }
    source_type = type_map.get(ext, "txt")

    if source_type == "pdf":
        raise HTTPException(400, "暂不支持 PDF 格式导入，请使用 EPUB / MD / TXT 格式。")

    file_path = DATA_DIR / "uploads" / f"{uuid.uuid4().hex}{ext}"
    content = await file.read()
    async with __import__('aiofiles').open(file_path, "wb") as f:
        await f.write(content)

    return {
        "filename": file.filename,
        "source_type": source_type,
        "file_path": str(file_path),
    }


async def api_get_course(course_id: str):
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

    all_group_chats = []
    for s in sessions:
        chats = db.get_group_chats(s["id"])
        all_group_chats.extend(chats)
    all_group_chats = _utc_list(all_group_chats, "created_at")

    # 速读模式：附加章节快照信息，供前端显示"精华知识点"/"推荐学习"标签
    reading_mode = course.get("reading_mode", "standard")
    if reading_mode == "speed":
        try:
            snapshots = db.get_chapter_snapshots(course_id)
            snapshot_map = {s["chapter_index"]: s for s in snapshots}
            highlights = db.get_global_highlights(course_id)
            core_indices = set()
            if highlights:
                for s in highlights.get("core_chapter_indices", []):
                    idx = _parse_chapter_num(s)
                    if idx >= 0:
                        core_indices.add(idx)
            for ch in chapters:
                snap = snapshot_map.get(ch["idx"], {})
                ch["importance"] = snap.get("importance", 0)
                # keywords 已经从 db.get_chapter_snapshots 中 json.loads 过了，直接用
                ch["keywords"] = snap.get("keywords", [])
                ch["core_viewpoint"] = snap.get("core_viewpoint", "")
                ch["learning_goal"] = snap.get("learning_goal", "")
                ch["is_core"] = ch["idx"] in core_indices
        except Exception:
            pass

    return {
        "course": _utc_dict(course, "created_at"),
        "reading_mode": reading_mode,  # 顶层暴露 reading_mode,前端判断模式直接用 data.reading_mode
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


async def api_remove_course(course_id: str):
    db.delete_course(course_id)
    return {"status": "deleted"}


async def api_generate_chapters(course_id: str, reading_mode: str = "standard"):
    """TOC-First 分章"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    source_path = course.get("source_path", "")
    source_type = course.get("source_type", "")

    actual_type = source_type
    if source_type == "text":
        actual_type = "txt"
    text = await extract_text(source_path, actual_type)

    if not text:
        raise HTTPException(400, "无法提取文本内容")

    chapters = await smart_chunk(text, source_type=source_type, file_path=source_path if source_type == "epub" else "", reading_mode=reading_mode)
    chapter_titles = _save_chapters_to_db(course_id, chapters, reading_mode)
    db.add_learning_event(course_id, "lesson_end", {"action": "chapters_generated", "count": len(chapters)})

    is_speed = reading_mode == "speed"
    snapshot_map = {}
    core_indices = set()
    if is_speed:
        try:
            snapshots = db.get_chapter_snapshots(course_id)
            for s in snapshots:
                snapshot_map[s["chapter_index"]] = s

            highlights = db.get_global_highlights(course_id)
            if highlights:
                for s in highlights.get("core_chapter_indices", []):
                    idx = _parse_chapter_num(s)
                    if idx >= 0:
                        core_indices.add(idx)
        except Exception:
            pass

    if is_speed:
        logger.info(f"[速读模式] 开始生成知识快照，共 {len(chapters)} 章")
        try:
            result = await _run_speed_mode_postprocess(
                course_id, chapters, chapter_titles,
                source_type, source_path, concurrency=2,
            )
            await _persist_speed_results(course_id, result)

            snapshots = result["snapshots"]
            highlights = result["highlights"]

            new_core_indices = set()
            if isinstance(highlights, dict):
                for s in highlights.get("core_chapter_indices", []):
                    idx = _parse_chapter_num(s)
                    if idx >= 0:
                        new_core_indices.add(idx)
            if new_core_indices:
                core_indices = new_core_indices

            logger.info(f"[速读模式] 完成，快照 {len(snapshots)} 章，掌握项 {len(result['syllabus_items'])} 条")

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
            logger.warning(f"速读模式快照生成失败: {e}")
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
            {"idx": c[2].get("idx", i), "title": c[0], "is_loaded": is_speed or i < 99}
            if isinstance(c, tuple) and len(c) >= 3 else
            {"idx": i, "title": c[0] if isinstance(c, tuple) else str(c), "is_loaded": True}
            for i, c in enumerate(chapters)
        ],
    }


async def api_load_chapter_content(course_id: str, chapter_idx: int):
    """懒加载：仅加载指定章节的全文和掌握项"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    chapter = db.get_chapter(course_id, chapter_idx)
    if not chapter:
        raise HTTPException(404, "章节不存在")

    if chapter.get("is_loaded"):
        return {"status": "already_loaded", "chapter": chapter}

    source_path = course.get("source_path", "")
    source_type = course.get("source_type", "")

    full_content = ""
    if source_type == "epub" and source_path:
        from chunker import extract_chapter_content_by_href, extract_toc_from_epub
        toc_items, _ = await extract_toc_from_epub(source_path)
        matched_href = None
        for item in toc_items:
            if item["idx"] == chapter_idx:
                matched_href = item.get("href", "")
                break
        if matched_href:
            full_content = await extract_chapter_content_by_href(source_path, matched_href)
        else:
            from chunker import extract_text_from_epub
            full_text = await extract_text_from_epub(source_path)

    if not full_content and source_path:
        actual = "txt" if source_type == "text" else source_type
        full_text = await extract_text(source_path, actual)
        full_content = _extract_chapter_from_text(full_text, chapter["title"], chapter_idx)

    if not full_content:
        full_content = chapter.get("content_slice", "") or "（内容未找到）"

    content_short = full_content[:5000]
    db.update_chapter_content(course_id, chapter_idx, full_content)
    conn = db.get_conn()
    try:
        conn.execute("UPDATE chapters SET content_slice=? WHERE course_id=? AND idx=?",
                     (content_short, course_id, chapter_idx))
        conn.commit()
    finally:
        conn.close()

    items = []
    # 速读模式:不重新生成 syllabus(由 _run_speed_mode_postprocess 集中按精华 20% 选取
    # 写入数据库,加载时再生成会破坏"只有核心章节有掌握项"的设计,导致 syllabus 持续累积)
    if course.get("reading_mode") != "speed":
        items = await generate_syllabus_items(course_id, [(chapter_idx, chapter["title"], full_content)])
        if items:
            # 清掉该章节的旧 syllabus，避免反复加载累积导致 ch_idx 重复
            conn = db.get_conn()
            try:
                conn.execute(
                    "DELETE FROM syllabus_items WHERE course_id=? AND chapter_index=?",
                    (course_id, chapter_idx),
                )
                conn.commit()
            finally:
                conn.close()
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


def _extract_chapter_from_text(full_text: str, chapter_title: str, chapter_idx: int) -> str:
    """从完整文本中按标题切出章节内容

    v1.4 评审 🟡 #8: 删 hard-coded `chapter_idx < 9` magic number,统一按 chunk_size 切。
    原版对 idx>=10 章节直接返回全文,行为与算法本意(按 idx+1 等分)不一致,会在
    长文章末段切出大块冗余内容。
    """
    pattern = re.compile(re.escape(chapter_title), re.IGNORECASE)
    match = pattern.search(full_text)
    if match:
        start = match.start()
        remaining = full_text[start + len(chapter_title):]
        next_ch = re.search(r'\n#{1,4}\s+|\n第[\d一二三四五六七八九十]+[章节]|\nChapter\s+\d+', remaining)
        end = start + len(chapter_title) + (next_ch.start() if next_ch else len(remaining))
        return full_text[start:end].strip()
    # 标题未匹配时的兜底:按 chapter_idx 等分段落
    paragraphs = [p.strip() for p in full_text.split('\n\n') if p.strip()]
    chunk_size = max(1, len(paragraphs) // max(1, (chapter_idx + 1)))
    start_para = chapter_idx * chunk_size
    end_para = start_para + chunk_size
    return '\n\n'.join(paragraphs[start_para:end_para])


async def api_regenerate_syllabus(course_id: str):
    """手动重新生成掌握项"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    chapters = db.get_chapters(course_id)
    if not chapters:
        raise HTTPException(400, "请先生成分章")

    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM syllabus_items WHERE course_id=?", (course_id,))
        conn.commit()
    finally:
        conn.close()

    # 保留真实 chapter_index（章节 idx 可能是非连续的，如 4~15）
    ch_list = [
        (ch["idx"], ch["title"], ch.get("content_slice", ""))
        for ch in chapters if ch.get("is_loaded", 1)
    ]

    if not ch_list:
        raise HTTPException(400, "无已加载章节")

    reading_mode = course.get("reading_mode", "standard")

    if reading_mode == "speed":
        return {"message": "速读模式使用快照生成，请先生成知识快照", "count": 0}
    else:
        items = await generate_syllabus_items(course_id, ch_list)
        if not items:
            _add_default_syllabus_items(course_id, ch_list)
            return {"message": "LLM生成失败，使用默认掌握项", "count": len(ch_list) * 3}

        for chapter_index, description in items:
            db.add_syllabus_item(course_id, chapter_index, description)
        return {"message": "掌握项生成完成", "count": len(items)}


async def api_generate_syllabus(course_id: str):
    """生成掌握项清单"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    chapters = db.get_chapters(course_id)
    if not chapters:
        raise HTTPException(400, "请先生成分章")

    reading_mode = course.get("reading_mode", "standard")

    if reading_mode == "speed":
        return {"total_items": 0, "message": "速读模式不生成掌握项"}

    ch_list = [
        (ch["idx"], ch["title"], ch.get("content_slice", ""))
        for ch in chapters if ch.get("is_loaded", 1)
    ]
    if not ch_list:
        return {"total_items": 0, "message": "无已加载章节，请先加载章节内容"}

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


async def api_generate_next_preview(course_id: str, chapter_idx: int):
    """基于当前章节内容，为下一章生成苏格拉底式预演引导"""
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
        logger.warning(f"苏格拉底预演生成失败: {e}")
        questions = []

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


async def api_recommend_roles(course_id: str):
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


async def api_get_all_roles():
    """获取所有角色信息"""
    from config import PROMPTS_DIR
    result = {}
    for role_id, meta in ROLES_META.items():
        prompt_path = PROMPTS_DIR / f"{role_id}.md"
        prompt_preview = ""
        if prompt_path.exists():
            content = prompt_path.read_text(encoding="utf-8")
            prompt_preview = content[:200]
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


async def api_get_role_detail(role_id: str):
    """获取角色详情"""
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


async def api_get_chapter_snapshots(course_id: str):
    """获取课程所有章节快照"""
    snapshots = db.get_chapter_snapshots(course_id)
    highlights = db.get_global_highlights(course_id)
    return {
        "snapshots": snapshots,
        "highlights": highlights,
    }


async def api_generate_snapshots(course_id: str):
    """手动生成知识快照"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    chapters = db.get_chapters(course_id)
    if not chapters:
        raise HTTPException(400, "请先生成分章")

    source_type = course.get("source_type", "")
    source_path = course.get("source_path", "")

    try:
        chapter_list = []
        for ch in chapters:
            content = ch.get("content_slice", "") or ch.get("content_full", "")
            chapter_list.append((ch["title"], content, {"idx": ch["idx"]}))
        chapter_titles = [ch["title"] for ch in chapters]

        result = await _run_speed_mode_postprocess(
            course_id, chapter_list, chapter_titles,
            source_type, source_path, concurrency=2,
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
        logger.error(f"重建快照失败: {e}")
        return {
            "snapshots_generated": 0,
            "snapshots_failed": [ch["idx"] for ch in chapters],
            "highlights": None,
            "syllabus_generated": 0,
        }


async def api_get_course_overview(course_id: str):
    """获取课程全局学习概览"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    syllabus = db.get_syllabus_items(course_id)
    chapters = db.get_chapters(course_id)
    reading_mode = course.get("reading_mode", "standard")

    if reading_mode == "speed":
        snapshots = db.get_chapter_snapshots(course_id)
        highlights = db.get_global_highlights(course_id) or {}

        core_indices = set()
        if highlights:
            for s in highlights.get("core_chapter_indices", []):
                idx = _parse_chapter_num(s)
                if idx >= 0:
                    core_indices.add(idx)

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
                ch_idx = _parse_chapter_num(ch_key)
                if ch_idx < 0:
                    continue
                if isinstance(deps, str):
                    dep_idx = _parse_chapter_num(deps)
                    if dep_idx >= 0:
                        chapter_dependencies[ch_idx] = [dep_idx]
                elif isinstance(deps, list):
                    dep_indices = []
                    for d in deps:
                        di = _parse_chapter_num(d)
                        if di >= 0:
                            dep_indices.append(di)
                    if dep_indices:
                        chapter_dependencies[ch_idx] = dep_indices

        learned_indices = {s.get("chapter_index") for s in chapter_stats if s["mastered"] > 0}

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

        def sort_by_deps(items):
            return sorted(items, key=lambda x: (not x["deps_satisfied"], -x["importance"], x["idx"]))

        core_unlearned = sort_by_deps(core_unlearned)

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
            "diaries": _utc_list(db.get_diaries(course_id), "created_at")[:10],
            "summaries": _utc_list(db.get_summaries(course_id), "created_at")[:10],
            "group_chats": _get_course_group_chats(course_id),
        }
    else:
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
            "diaries": _utc_list(db.get_diaries(course_id), "created_at")[:10],
            "summaries": _utc_list(db.get_summaries(course_id), "created_at")[:10],
            "group_chats": _get_course_group_chats(course_id),
        }


async def api_get_spiritual_notes(course_id: str):
    """获取课程所有思辨笔记"""
    notes = db.get_spiritual_notes(course_id)
    chapters = db.get_chapters(course_id)
    chapter_map = {ch["idx"]: ch["title"] for ch in chapters}
    for note in notes:
        note["chapter_title"] = chapter_map.get(note["chapter_index"], f"第{note['chapter_index'] + 1}章")
    return {"notes": notes}


async def api_get_mastery_progress(course_id: str):
    """获取课程掌握进度"""
    progress = db.get_mastery_progress(course_id)
    syllabus = db.get_syllabus_items(course_id)

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

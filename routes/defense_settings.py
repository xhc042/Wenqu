"""
问渠 (Wenqu) v1.1 — 答辩和设置路由模块

负责：
- 结业答辩（自动生成题目、AI 评分）
- 课程设置（深度、时长、模型配置）
- 学习事件追踪
- 证书生成
- 日记和摘要管理
"""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import HTTPException

import database as db
from config import (
    ROLES_META, DEFAULT_SLIDERS, DEPTH_CONFIG,
    MODEL_TIER_CONFIG, DEFAULT_MODEL_TIER,
    DEFAULT_READING_MODE, READING_MODE_CONFIG,
)
from llm_client import llm

logger = logging.getLogger(__name__)

# 默认配置值（config.py 中没有这些常量，在此定义）
_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_TOP_P = 1.0
_DEFAULT_MAX_CONCURRENT_SESSIONS = 5
_DEFAULT_MAX_TOKENS = 4096


# ==================== 答辩相关 ====================

async def api_generate_defense_questions(course_id: str, num_questions: int = 10):
    """自动生成结业答辩题目"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    chapters = db.get_chapters(course_id)
    if not chapters:
        raise HTTPException(400, "请先生成分章")

    reading_mode = course.get("reading_mode", DEFAULT_READING_MODE)

    if reading_mode == "speed":
        snapshots = db.get_chapter_snapshots(course_id)
        highlights = db.get_global_highlights(course_id)

        if not snapshots:
            return {
                "status": "no_snapshots",
                "message": "请先生成知识快照",
                "questions": [],
                "topics": [],
            }

        prompt = f"""你是结业答辩出题专家。

课程：{course['title']}
共 {len(snapshots)} 章，已生成知识快照。

请基于以下快照信息，生成 {num_questions} 道有深度的结业答辩题目。
题目应覆盖核心观点、关键概念和章节间的联系。

知识快照：
"""
        for snap in snapshots:
            prompt += f"\n第{snap['chapter_index'] + 1}章《{snap['title']}》："
            prompt += f"核心观点：{snap.get('core_viewpoint', '')}；关键词：{', '.join(snap.get('keywords', []))}"

        if highlights:
            prompt += "\n\n全局精华："
            prompt += f"\n关键要点：{json.dumps(highlights.get('key_points', []), ensure_ascii=False)}"

        prompt += f"\n\n请直接返回 JSON 数组，格式：[{{\"topic\": \"主题\", \"question\": \"问题\", \"importance\": 5}}]"

        try:
            result = await llm.chat_json(prompt, "你是结业答辩出题专家。只返回JSON数组，不要其他内容。")
            questions = result.get("questions", [])
            topics = result.get("topics", [])
        except Exception as e:
            logger.warning(f"答辩题目生成失败: {e}")
            questions = []
            topics = []
    else:
        ch_list = [(ch["title"], ch.get("content_slice", "")) for ch in chapters if ch.get("is_loaded", 1)]
        if not ch_list:
            return {
                "status": "no_content",
                "message": "无已加载章节内容",
                "questions": [],
                "topics": [],
            }

        prompt = f"""你是结业答辩出题专家。

课程：{course['title']}
共 {len(ch_list)} 章已加载。

请基于以下章节内容，生成 {num_questions} 道有深度的结业答辩题目。
题目应覆盖核心观点、关键概念和章节间的联系。

章节内容：
"""
        for title, content in ch_list:
            prompt += f"\n《{title}》：{content[:300]}"

        prompt += f"\n\n请直接返回 JSON 数组，格式：[{{\"topic\": \"主题\", \"question\": \"问题\", \"importance\": 5}}]"

        try:
            result = await llm.chat_json(prompt, "你是结业答辩出题专家。只返回JSON数组，不要其他内容。")
            questions = result.get("questions", [])
            topics = result.get("topics", [])
        except Exception as e:
            logger.warning(f"答辩题目生成失败: {e}")
            questions = []
            topics = []

    if not questions:
        questions = [
            {"topic": "核心概念", "question": "请概括本课程的核心观点", "importance": 5},
            {"topic": "章节联系", "question": "各章节之间有什么内在联系？", "importance": 4},
            {"topic": "应用实践", "question": "如何应用所学解决实际问题？", "importance": 3},
        ]

    for q in questions:
        db.add_defense_question(course_id, q.get("topic", ""), q.get("question", ""), q.get("importance", 3))

    return {
        "status": "generated",
        "questions": questions,
        "topics": topics,
        "total_questions": len(questions),
    }


async def api_submit_answer(
    course_id: str,
    question_id: str,
    answer: str,
    role_id: str = "judge",
    sliders: Optional[dict] = None,
):
    """提交答案"""
    if sliders is None:
        sliders = DEFAULT_SLIDERS.get(role_id, DEFAULT_SLIDERS["tutor"])

    question = db.get_defense_question(question_id)
    if not question or question["course_id"] != course_id:
        raise HTTPException(404, "答辩题目不存在")

    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    reading_mode = course.get("reading_mode", DEFAULT_READING_MODE)

    session = db.create_session(
        course_id=course_id,
        chapter_index=-1,
        role_id=role_id,
        role_name=ROLES_META.get(role_id, ROLES_META["tutor"])["name"],
        reading_mode=READING_MODE_CONFIG.get(reading_mode, reading_mode),
    )

    system_prompt = f"""你是结业答辩评审专家。

课程：{course['title']}
深度：{session.get('depth', 'standard')}

你的任务是评估学生对答辩问题的回答质量。
请从以下维度评分：
- 理解深度（1-5分）
- 逻辑清晰度（1-5分）
- 创新性（1-5分）
- 综合表现（1-5分）

请返回 JSON：
{{
  "score_understanding": 4,
  "score_logic": 4,
  "score_innovation": 3,
  "score_overall": 4,
  "feedback": "详细的反馈评语",
  "strengths": ["优点1", "优点2"],
  "improvements": ["改进建议1", "改进建议2"]
}}
"""

    prompt = f"""【答辩题目】
{question['topic']}: {question['question']}

【学生回答】
{answer}

请评估以上回答的质量，返回 JSON 评分。
"""

    try:
        result = await llm.chat_json(prompt, system_prompt)

        db.save_group_chat(
            session_id=session["id"],
            message_type="user",
            message_content=f"答辩问题：{question['question']}\n学生回答：{answer}",
            course_id=course_id,
            chapter_index=-1,
        )
        db.save_group_chat(
            session_id=session["id"],
            message_type="ai",
            message_content=json.dumps(result, ensure_ascii=False),
            course_id=course_id,
            chapter_index=-1,
        )

        return {
            "status": "graded",
            "score": result,
            "question_id": question_id,
        }
    except Exception as e:
        logger.error(f"答案评分失败: {e}")
        raise HTTPException(500, f"评分失败: {str(e)}")


async def api_get_defense_questions(course_id: str):
    """获取课程的答辩题目"""
    questions = db.get_defense_questions(course_id)
    return {"questions": questions}


async def api_get_defense_progress(course_id: str):
    """获取答辩进度"""
    progress = db.get_defense_progress(course_id)
    return progress


async def api_complete_defense(course_id: str):
    """完成答辩"""
    progress = db.get_defense_progress(course_id)
    if not progress:
        return {"status": "no_progress"}

    total_score = progress.get("average_score", 0)
    question_count = progress.get("total_questions", 0)

    if question_count == 0:
        return {"status": "no_questions"}

    if total_score >= 3.0:
        db.complete_defense(course_id, total_score)
        return {
            "status": "passed",
            "score": total_score,
            "message": f"恭喜通过结业答辩！平均分：{total_score:.1f}/5.0",
        }
    else:
        return {
            "status": "failed",
            "score": total_score,
            "message": f"未通过，建议复习后重新答辩。平均分：{total_score:.1f}/5.0",
        }


async def api_get_certificate(course_id: str):
    """获取课程证书"""
    certificate = db.get_certificate(course_id)
    if not certificate:
        return {"has_certificate": False}
    return {"has_certificate": True, "certificate": certificate}


async def api_issue_certificate(course_id: str):
    """颁发证书"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    certificate = db.issue_certificate(course_id, course.get("title", ""))
    return {"certificate": certificate}


# ==================== 设置相关 ====================

async def api_get_llm_config():
    """获取 LLM 配置"""
    return {
        "default_model": _DEFAULT_MODEL,
        "default_temperature": _DEFAULT_TEMPERATURE,
        "default_top_p": _DEFAULT_TOP_P,
        "max_concurrent_sessions": _DEFAULT_MAX_CONCURRENT_SESSIONS,
        "default_max_tokens": _DEFAULT_MAX_TOKENS,
        "model_tier_config": MODEL_TIER_CONFIG,
        "default_model_tier": DEFAULT_MODEL_TIER,
    }


async def api_update_llm_config(config: dict):
    """更新 LLM 配置"""
    updates = {}
    if "default_model" in config:
        updates["DEFAULT_MODEL"] = config["default_model"]
    if "default_temperature" in config:
        updates["DEFAULT_TEMPERATURE"] = config["default_temperature"]
    if "default_top_p" in config:
        updates["DEFAULT_TOP_P"] = config["default_top_p"]
    if "max_concurrent_sessions" in config:
        updates["MAX_CONCURRENT_SESSIONS"] = config["max_concurrent_sessions"]
    if "default_max_tokens" in config:
        updates["DEFAULT_MAX_TOKENS"] = config["default_max_tokens"]
    if "model_tier_config" in config:
        updates["MODEL_TIER_CONFIG"] = config["model_tier_config"]
    if "default_model_tier" in config:
        updates["DEFAULT_MODEL_TIER"] = config["default_model_tier"]

    return {"status": "updated", "config": updates}


async def api_update_course_settings(course_id: str, reading_mode: str = None, depth: str = None, duration: str = None):
    """更新课程设置"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    updates = {}
    if reading_mode:
        updates["reading_mode"] = reading_mode
    if depth:
        updates["depth"] = depth
    if duration:
        updates["duration"] = duration

    if updates:
        db.update_course_settings(course_id, **updates)

    return {"status": "updated", "settings": updates}


async def api_get_course_settings(course_id: str):
    """获取课程设置"""
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    return {
        "reading_mode": course.get("reading_mode", DEFAULT_READING_MODE),
        "depth": course.get("depth", "standard"),
        "duration": course.get("duration", "medium"),
    }


# ==================== 学习事件 ====================

async def api_add_learning_event(course_id: str, event_type: str, data: dict = None):
    """添加学习事件"""
    db.add_learning_event(course_id, event_type, data or {})
    return {"status": "added"}


async def api_get_learning_events(course_id: str):
    """获取学习事件"""
    events = db.get_learning_events(course_id)
    return {"events": events}


# ==================== 日记和摘要 ====================

async def api_save_diary_entry(course_id: str, content: str, chapter_index: int = None):
    """保存日记条目"""
    diary = db.save_diary_entry(course_id, content, chapter_index)
    return {"diary": diary}


async def api_get_diary_entries(course_id: str):
    """获取日记条目"""
    entries = db.get_diaries(course_id)
    return {"entries": entries}


async def api_delete_diary_entry(entry_id: str):
    """删除日记条目"""
    db.delete_diary_entry(entry_id)
    return {"status": "deleted"}


async def api_get_summary(course_id: str, summary_type: str = "daily"):
    """获取摘要"""
    summary = db.get_summary(course_id, summary_type)
    return {"summary": summary}


async def api_clear_summary(course_id: str, summary_type: str = "daily"):
    """清除摘要"""
    db.clear_summary(course_id, summary_type)
    return {"status": "cleared"}


# ==================== 其他辅助 ====================

async def api_get_course_groups(course_id: str):
    """获取课程群聊记录"""
    groups = db.get_course_groups(course_id)
    return {"groups": groups}


async def api_get_group_chats(session_id: str):
    """获取群聊记录"""
    chats = db.get_group_chats(session_id)
    return {"chats": chats}


async def api_reset_course_progress(course_id: str):
    """重置课程进度"""
    db.reset_course_progress(course_id)
    return {"status": "reset"}


async def api_update_syllabus_item(item_id: str, status: str = None, notes: str = None):
    """更新掌握项"""
    updates = {}
    if status:
        updates["status"] = status
    if notes:
        updates["notes"] = notes
    db.update_syllabus_item(item_id, **updates)
    return {"status": "updated"}


async def api_get_spiritual_notes(course_id: str):
    """获取思辨笔记"""
    notes = db.get_spiritual_notes(course_id)
    return {"notes": notes}


async def api_add_spiritual_note(course_id: str, chapter_index: int, content: str, topic: str = None):
    """添加思辨笔记"""
    note = db.add_spiritual_note(course_id, chapter_index, content, topic)
    return {"note": note}


async def api_delete_spiritual_note(note_id: str):
    """删除思辨笔记"""
    db.delete_spiritual_note(note_id)
    return {"status": "deleted"}


async def api_get_mastery_progress(course_id: str):
    """获取掌握进度"""
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

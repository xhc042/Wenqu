"""
问渠 (Wenqu) v1.1 — 对话/WebSocket 路由模块

负责苏格拉底式对话管理：
- WebSocket 实时对话流
- 会话/群聊管理
- 对话历史查询
"""

import json
import logging
import asyncio
import time
from datetime import datetime
from typing import Optional, Dict, List

from fastapi import WebSocket, WebSocketDisconnect, Query, HTTPException
import aiohttp

import database as db
from config import (
    ROLES_META, DEFAULT_SLIDERS, DEPTH_CONFIG,
    DEFAULT_READING_MODE, READING_MODE_CONFIG,
    MODEL_TIER_CONFIG, DEFAULT_MODEL_TIER,
    DEFAULT_MODEL, DEFAULT_TEMPERATURE, DEFAULT_TOP_P,
    MAX_CONCURRENT_SESSIONS, DEFAULT_MAX_TOKENS,
)
from state_machine import StateMachine, State
from llm_client import llm

logger = logging.getLogger(__name__)


# ==================== WebSocket 管理 ====================

active_connections: Dict[str, List[WebSocket]] = {}
connection_sessions: Dict[WebSocket, dict] = {}
session_active_connections: Dict[str, int] = {}


async def _ws_cleanup(session_id: Optional[str] = None, websocket: Optional[WebSocket] = None):
    """清理 WebSocket 连接"""
    if websocket in connection_sessions:
        conn_info = connection_sessions.pop(websocket)
        ws_id = conn_info.get("ws_id")
        if ws_id and ws_id in active_connections.get(session_id, []):
            active_connections.get(session_id, []).remove(ws_id)
        if not active_connections.get(session_id):
            active_connections.pop(session_id, None)
        if session_id:
            session_active_connections[session_id] = max(0, session_active_connections.get(session_id, 0) - 1)
    if session_id:
        connection_sessions.clear()
        active_connections.pop(session_id, None)
        session_active_connections.pop(session_id, None)


async def _broadcast_status(session_id: str, status: str):
    """广播会话状态变更"""
    connections = active_connections.get(session_id, [])
    if connections:
        payload = json.dumps({"type": "status", "status": status}, ensure_ascii=False)
        dead = []
        for conn_id in connections:
            try:
                ws = next((ws for ws, info in connection_sessions.items() if info.get("ws_id") == conn_id), None)
                if ws:
                    await ws.send_text(payload)
                else:
                    dead.append(conn_id)
            except Exception:
                dead.append(conn_id)
        for d in dead:
            if d in active_connections.get(session_id, []):
                active_connections[session_id].remove(d)


async def _check_session_limits(session_id: str, course_id: str, role_id: str, sliders: dict) -> Optional[str]:
    """检查会话限制"""
    if session_active_connections.get(session_id, 0) >= MAX_CONCURRENT_SESSIONS:
        return "当前课程同时进行的对话数量已达上限，请稍后再试。"
    return None


# ==================== 对话辅助函数 ====================

async def _get_or_create_session(
    course_id: str,
    chapter_index: int,
    role_id: str = "tutor",
    sliders: Optional[dict] = None,
    session_id: Optional[str] = None,
) -> dict:
    """获取或创建新会话"""
    if session_id:
        sessions = db.get_sessions(course_id)
        for s in sessions:
            if s.get("id") == session_id:
                return s
        raise HTTPException(404, "会话不存在")

    existing = db.get_active_session(course_id, chapter_index)
    if existing:
        return existing

    role_config = ROLES_META.get(role_id, ROLES_META["tutor"])
    slider_config = DEFAULT_SLIDERS.get(role_id, DEFAULT_SLIDERS["tutor"])

    return db.create_session(
        course_id=course_id,
        chapter_index=chapter_index,
        role_id=role_id,
        role_name=role_config["name"],
        strictness=slider_config.get("strictness", 0),
        encouragement=slider_config.get("encouragement", 0),
        verbosity=slider_config.get("verbosity", 0),
        reading_mode=READING_MODE_CONFIG.get(DEFAULT_READING_MODE, DEFAULT_READING_MODE),
    )


def _build_system_prompt(session: dict, course: dict, reading_mode: str) -> str:
    """构建系统提示词"""
    role_id = session.get("role_id", "tutor")
    role_config = ROLES_META.get(role_id, ROLES_META["tutor"])
    prompt_template = role_config.get("prompt", "")

    depth = session.get("depth", "standard")
    depth_config = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["standard"])

    system_prompt = prompt_template.replace("{depth_level}", depth_config["level"])
    system_prompt = system_prompt.replace("{depth_desc}", depth_config["description"])

    course_title = course.get("title", "")
    chapter_title = session.get("chapter_title", "")
    if chapter_title and not course_title.startswith(chapter_title):
        system_prompt = system_prompt.replace("{course_title}", f"{course_title} - {chapter_title}")
    else:
        system_prompt = system_prompt.replace("{course_title}", course_title)

    return system_prompt


async def _handle_user_message(
    session_id: str,
    course_id: str,
    chapter_index: int,
    user_message: str,
    role_id: str = "tutor",
    sliders: Optional[dict] = None,
    websocket: Optional[WebSocket] = None,
):
    """处理用户消息"""
    if sliders is None:
        sliders = DEFAULT_SLIDERS.get(role_id, DEFAULT_SLIDERS["tutor"])

    session = await _get_or_create_session(course_id, chapter_index, role_id, sliders, session_id)
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    reading_mode = course.get("reading_mode", DEFAULT_READING_MODE)

    state_machine = StateMachine(
        role_id=session["role_id"],
        role_name=session["role_name"],
        chapter_index=chapter_index,
        depth_level=session.get("depth", "standard"),
        reading_mode=reading_mode,
    )

    system_prompt = _build_system_prompt(session, course, reading_mode)

    # 获取对话历史
    group_chats = db.get_group_chats(session["id"])
    history_messages = []
    for chat in group_chats:
        if chat.get("message_type") in ["user", "ai"]:
            history_messages.append({
                "role": chat["message_type"],
                "content": chat["message_content"],
            })

    state_machine.set_history(history_messages)
    state_machine.push_history("user", user_message)

    if websocket:
        try:
            await websocket.send_text(json.dumps(
                {"type": "status", "status": "processing"}, ensure_ascii=False
            ))
        except Exception:
            pass

    try:
        role_config = ROLES_META.get(role_id, ROLES_META["tutor"])
        slider_config = DEFAULT_SLIDERS.get(role_id, DEFAULT_SLIDERS["tutor"])

        prompt = system_prompt
        prompt += f"\n\n【用户说】\n{user_message}\n\n"
        prompt += f"你现在扮演{role_config['name']}，以{role_config.get('style', '')}风格回答。\n"
        prompt += f"性格：{role_config.get('personality', '')}\n"
        prompt += f"严格度：{slider_config.get('strictness', 0)}\n"
        prompt += f"鼓励度：{slider_config.get('encouragement', 0)}\n"
        prompt += f"详细度：{slider_config.get('verbosity', 0)}\n\n"
        prompt += "请用苏格拉底式方法回答，不要直接给出答案，要通过引导帮助学生自己思考。"

        result = await llm.chat_stream(prompt, system_prompt)

        ai_response = ""
        async for chunk in result:
            ai_response += chunk
            if websocket:
                try:
                    await websocket.send_text(json.dumps(
                        {"type": "message", "content": chunk}, ensure_ascii=False
                    ))
                except Exception:
                    break

        state_machine.push_history("ai", ai_response)

        if websocket:
            try:
                await websocket.send_text(json.dumps(
                    {"type": "status", "status": "idle"}, ensure_ascii=False
                ))
            except Exception:
                pass

        db.save_group_chat(
            session_id=session["id"],
            message_type="user",
            message_content=user_message,
            course_id=course_id,
            chapter_index=chapter_index,
        )
        db.save_group_chat(
            session_id=session["id"],
            message_type="ai",
            message_content=ai_response,
            course_id=course_id,
            chapter_index=chapter_index,
        )

        state_machine.advance_state()

    except Exception as e:
        logger.error(f"对话失败: {e}")
        if websocket:
            try:
                await websocket.send_text(json.dumps(
                    {"type": "error", "error": str(e)}, ensure_ascii=False
                ))
            except Exception:
                pass


async def _handle_defense_question(
    session_id: str,
    course_id: str,
    chapter_index: int,
    user_message: str,
    role_id: str = "tutor",
    sliders: Optional[dict] = None,
    websocket: Optional[WebSocket] = None,
):
    """处理答辩相关问题"""
    if sliders is None:
        sliders = DEFAULT_SLIDERS.get(role_id, DEFAULT_SLIDERS["tutor"])

    session = await _get_or_create_session(course_id, chapter_index, role_id, sliders, session_id)
    course = db.get_course(course_id)
    if not course:
        raise HTTPException(404, "课程不存在")

    reading_mode = course.get("reading_mode", DEFAULT_READING_MODE)

    system_prompt = _build_system_prompt(session, course, reading_mode)

    if websocket:
        try:
            await websocket.send_text(json.dumps(
                {"type": "status", "status": "processing"}, ensure_ascii=False
            ))
        except Exception:
            pass

    try:
        role_config = ROLES_META.get(role_id, ROLES_META["tutor"])
        slider_config = DEFAULT_SLIDERS.get(role_id, DEFAULT_SLIDERS["tutor"])

        prompt = system_prompt
        prompt += f"\n\n【用户说】\n{user_message}\n\n"
        prompt += f"你现在扮演{role_config['name']}，以{role_config.get('style', '')}风格回答。\n"
        prompt += f"性格：{role_config.get('personality', '')}\n"
        prompt += f"严格度：{slider_config.get('strictness', 0)}\n"
        prompt += f"鼓励度：{slider_config.get('encouragement', 0)}\n"
        prompt += f"详细度：{slider_config.get('verbosity', 0)}\n\n"
        prompt += "请用苏格拉底式方法回答，不要直接给出答案，要通过引导帮助学生自己思考。"

        result = await llm.chat_stream(prompt, system_prompt)

        ai_response = ""
        async for chunk in result:
            ai_response += chunk
            if websocket:
                try:
                    await websocket.send_text(json.dumps(
                        {"type": "message", "content": chunk}, ensure_ascii=False
                    ))
                except Exception:
                    break

        if websocket:
            try:
                await websocket.send_text(json.dumps(
                    {"type": "status", "status": "idle"}, ensure_ascii=False
                ))
            except Exception:
                pass

    except Exception as e:
        logger.error(f"答辩问答失败: {e}")
        if websocket:
            try:
                await websocket.send_text(json.dumps(
                    {"type": "error", "error": str(e)}, ensure_ascii=False
                ))
            except Exception:
                pass


# ==================== 路由函数 ====================

async def api_start_session(course_id: str, chapter_index: int = 0, role_id: str = "tutor", sliders: Optional[dict] = None):
    """开始新会话"""
    if sliders is None:
        sliders = DEFAULT_SLIDERS.get(role_id, DEFAULT_SLIDERS["tutor"])

    session = await _get_or_create_session(course_id, chapter_index, role_id, sliders)
    db.add_learning_event(course_id, "lesson_start", {"action": "session_started", "session_id": session["id"], "chapter_index": chapter_index})
    return session


async def api_continue_session(session_id: str, user_message: str):
    """继续会话（非WebSocket）"""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")

    course = db.get_course(session["course_id"])
    if not course:
        raise HTTPException(404, "课程不存在")

    reading_mode = course.get("reading_mode", DEFAULT_READING_MODE)

    state_machine = StateMachine(
        role_id=session["role_id"],
        role_name=session["role_name"],
        chapter_index=session["chapter_index"],
        depth_level=session.get("depth", "standard"),
        reading_mode=reading_mode,
    )

    system_prompt = _build_system_prompt(session, course, reading_mode)

    group_chats = db.get_group_chats(session_id)
    history_messages = []
    for chat in group_chats:
        if chat.get("message_type") in ["user", "ai"]:
            history_messages.append({
                "role": chat["message_type"],
                "content": chat["message_content"],
            })

    state_machine.set_history(history_messages)
    state_machine.push_history("user", user_message)

    try:
        role_config = ROLES_META.get(session["role_id"], ROLES_META["tutor"])
        slider_config = DEFAULT_SLIDERS.get(session["role_id"], DEFAULT_SLIDERS["tutor"])

        prompt = system_prompt
        prompt += f"\n\n【用户说】\n{user_message}\n\n"
        prompt += f"你现在扮演{role_config['name']}，以{role_config.get('style', '')}风格回答。\n"
        prompt += f"性格：{role_config.get('personality', '')}\n"
        prompt += f"严格度：{slider_config.get('strictness', 0)}\n"
        prompt += f"鼓励度：{slider_config.get('encouragement', 0)}\n"
        prompt += f"详细度：{slider_config.get('verbosity', 0)}\n\n"
        prompt += "请用苏格拉底式方法回答，不要直接给出答案，要通过引导帮助学生自己思考。"

        result = await llm.chat_json(prompt, system_prompt)
        ai_response = result.get("response", "")

        state_machine.push_history("ai", ai_response)
        state_machine.advance_state()

        db.save_group_chat(
            session_id=session_id,
            message_type="user",
            message_content=user_message,
            course_id=session["course_id"],
            chapter_index=session["chapter_index"],
        )
        db.save_group_chat(
            session_id=session_id,
            message_type="ai",
            message_content=ai_response,
            course_id=session["course_id"],
            chapter_index=session["chapter_index"],
        )

        return {"response": ai_response, "history": state_machine.get_history()}
    except Exception as e:
        logger.error(f"对话失败: {e}")
        raise HTTPException(500, f"对话失败: {str(e)}")


async def api_get_session_history(session_id: str):
    """获取会话历史"""
    chats = db.get_group_chats(session_id)
    return {"messages": chats}


async def api_get_session_info(session_id: str):
    """获取会话信息"""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    return session


async def api_delete_session(session_id: str):
    """删除会话"""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    db.delete_session(session_id)
    return {"status": "deleted"}


async def api_get_active_session(course_id: str, chapter_index: int):
    """获取课程的活跃会话"""
    session = db.get_active_session(course_id, chapter_index)
    if not session:
        return {"active_session": None}
    return {"active_session": session}


async def api_update_session_settings(session_id: str, role_id: str = None, sliders: dict = None):
    """更新会话设置"""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")

    updates = {}
    if role_id:
        role_config = ROLES_META.get(role_id, ROLES_META["tutor"])
        updates["role_id"] = role_id
        updates["role_name"] = role_config["name"]

    if sliders:
        updates["strictness"] = sliders.get("strictness", 0)
        updates["encouragement"] = sliders.get("encouragement", 0)
        updates["verbosity"] = sliders.get("verbosity", 0)

    if updates:
        db.update_session_settings(session_id, **updates)

    updated_session = db.get_session(session_id)
    return updated_session


async def api_get_course_sessions(course_id: str):
    """获取课程的所有会话"""
    sessions = db.get_sessions(course_id)
    return {"sessions": sessions}


async def api_reset_session(session_id: str):
    """重置会话"""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")

    db.reset_session(session_id)
    return {"status": "reset"}


async def api_end_session(session_id: str):
    """结束会话"""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")

    db.end_session(session_id)
    return {"status": "ended"}


async def api_get_course_learning_stats(course_id: str):
    """获取课程学习统计"""
    stats = db.get_course_learning_stats(course_id)
    return stats

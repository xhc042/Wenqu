"""
问渠 (Wenqu) v1.1 — WebSocket 对话路由模块

负责苏格拉底式实时对话：
- 单聊模式（一对一问答）
- 群聊模式（多角色辩论）
- 思考标签清理
- 对话状态管理
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Optional, Dict, Any, Set

from fastapi import WebSocket, WebSocketDisconnect

import database as db
from config import ROLES_META, DEFAULT_SLIDERS, DEPTH_CONFIG, READING_MODE_CONFIG
from state_machine import DialogueStateMachine
from llm_client import llm

logger = logging.getLogger(__name__)

# v1.5 优化: 预编译思考标签正则
_THINK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)


def clean_thinking_tags(text: str) -> str:
    """移除 AI 回复中的思考标签

    v1.5 优化: 使用预编译正则，减少重复编译开销
    """
    if not text:
        return text
    cleaned = _THINK_PATTERN.sub("", text).strip()
    return cleaned


class WebSocketManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        # session_id -> {websocket, role_id, course_id, chapter_index}
        self.active_connections: Dict[str, dict] = {}
        # course_id -> set[session_ids]
        self.course_sessions: Dict[str, Set[str]] = {}
        # 并发锁
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, session_id: str,
                      role_id: str, course_id: str, chapter_index: int):
        """接受 WebSocket 连接"""
        await websocket.accept()

        # 记录群聊名称
        role_meta = ROLES_META.get(role_id, ROLES_META["tutor"])
        chapter_title = ""
        if chapter_index >= 0:
            chapter = db.get_chapter(course_id, chapter_index)
            if chapter:
                chapter_title = chapter["title"]

        group_name = f"👨‍🏫 {role_meta['emoji']} {role_meta['name']}{' - ' + chapter_title if chapter_title else ''}"
        db.update_group_chat_name(session_id, group_name)

        async with self._lock:
            self.active_connections[session_id] = {
                "websocket": websocket,
                "role_id": role_id,
                "course_id": course_id,
                "chapter_index": chapter_index,
            }
            if course_id not in self.course_sessions:
                self.course_sessions[course_id] = set()
            self.course_sessions[course_id].add(session_id)

        logger.info(f"WebSocket 已连接: {session_id} ({role_id})")

    def disconnect(self, session_id: str):
        """关闭 WebSocket 连接"""
        if session_id in self.active_connections:
            conn_info = self.active_connections.pop(session_id)
            course_id = conn_info["course_id"]
            if course_id in self.course_sessions:
                self.course_sessions[course_id].discard(session_id)
                if not self.course_sessions[course_id]:
                    del self.course_sessions[course_id]
            logger.info(f"WebSocket 已断开: {session_id}")

    async def send_message(self, session_id: str, message: dict):
        """发送消息到指定 WebSocket"""
        if session_id in self.active_connections:
            try:
                websocket = self.active_connections[session_id]["websocket"]
                await websocket.send_json(message)
                return True
            except Exception as e:
                logger.error(f"发送消息失败 {session_id}: {e}")
                self.disconnect(session_id)
                return False
        return False

    async def broadcast_to_course(self, course_id: str, message: dict, exclude_session: Optional[str] = None):
        """向课程的所有 WebSocket 广播消息"""
        if course_id not in self.course_sessions:
            return

        session_ids = list(self.course_sessions[course_id])
        for sid in session_ids:
            if sid != exclude_session:
                await self.send_message(sid, message)


# 全局 WebSocket 管理器实例
ws_manager = WebSocketManager()


async def handle_single_chat(websocket: WebSocket, session_id: str,
                             course_id: str, chapter_index: int,
                             role_id: str, sliders: dict):
    """处理单聊模式对话"""
    state = DialogueStateMachine(session_id)
    role_meta = ROLES_META.get(role_id, ROLES_META["tutor"])

    await ws_manager.send_message(session_id, {
        "type": "session_start",
        "session_id": session_id,
        "role_id": role_id,
        "role_name": role_meta["name"],
        "timestamp": datetime.now().isoformat(),
    })

    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                await ws_manager.send_message(session_id, {
                    "type": "error",
                    "message": "无效的消息格式",
                })
                continue

            user_text = message.get("message", "").strip()
            if not user_text:
                continue

            await ws_manager.send_message(session_id, {
                "type": "user_message",
                "message": user_text,
                "timestamp": datetime.now().isoformat(),
            })

            # 保存用户消息
            db.save_group_chat(
                session_id=session_id,
                message_type="user",
                message_content=user_text,
                course_id=course_id,
                chapter_index=chapter_index,
            )

            # 第一步：分享
            share_result = state.transition("SHARE", user_text)
            if share_result.get("stop"):
                await ws_manager.send_message(session_id, {
                    "type": "system",
                    "message": "该角色已达到并发对话上限",
                })
                continue

            # 第二步：追问
            probe_result = state.transition("PROBE")
            if probe_result.get("stop"):
                await ws_manager.send_message(session_id, {
                    "type": "system",
                    "message": "追问生成失败",
                })
                continue

            follow_up_q = probe_result.get("follow_up_question", "")
            if follow_up_q:
                await ws_manager.send_message(session_id, {
                    "type": "ai_followup",
                    "message": follow_up_q,
                    "role_id": role_id,
                    "timestamp": datetime.now().isoformat(),
                })

                try:
                    resp = await websocket.receive_text()
                    resp_data = json.loads(resp) if resp.strip() else {}
                    follow_up_answer = resp_data.get("message", resp) if isinstance(resp_data, dict) else resp
                except (json.JSONDecodeError, WebSocketDisconnect):
                    follow_up_answer = "（无回答）"

                db.save_group_chat(
                    session_id=session_id,
                    message_type="user",
                    message_content=follow_up_answer,
                    course_id=course_id,
                    chapter_index=chapter_index,
                )

                state.transition("WAIT_USER", follow_up_answer)

            # 第三步：评估
            eval_result = state.transition("EVAL")
            if eval_result.get("stop"):
                await ws_manager.send_message(session_id, {
                    "type": "system",
                    "message": "评估失败，请重试",
                })
                continue

            # 第四步：执行动作
            action_result = state.transition("ACTION")
            if action_result.get("stop"):
                await ws_manager.send_message(session_id, {
                    "type": "system",
                    "message": "响应生成失败",
                })
                continue

            ai_response = action_result.get("response", "")
            ai_response = clean_thinking_tags(ai_response)

            # 流式发送
            await ws_manager.send_message(session_id, {
                "type": "stream_start",
                "session_id": session_id,
                "role_id": role_id,
                "role_name": role_meta["name"],
                "role_emoji": role_meta.get("emoji", "👨‍🏫"),
                "timestamp": datetime.now().isoformat(),
            })

            chunks = ai_response.split(" ")
            for i, chunk in enumerate(chunks):
                await ws_manager.send_message(session_id, {
                    "type": "stream_chunk",
                    "data": chunk + (" " if i < len(chunks) - 1 else ""),
                    "role_id": role_id,
                    "timestamp": datetime.now().isoformat(),
                })
                await asyncio.sleep(0.02)

            await ws_manager.send_message(session_id, {
                "type": "stream_end",
                "session_id": session_id,
                "role_id": role_id,
                "timestamp": datetime.now().isoformat(),
            })

            # 保存 AI 回复
            db.save_group_chat(
                session_id=session_id,
                message_type="ai",
                message_content=ai_response,
                course_id=course_id,
                chapter_index=chapter_index,
            )

    except WebSocketDisconnect:
        ws_manager.disconnect(session_id)
        logger.info(f"WebSocket 客户端断开: {session_id}")
    except Exception as e:
        logger.error(f"单聊模式错误 {session_id}: {e}")
        await ws_manager.send_message(session_id, {
            "type": "error",
            "message": f"对话出错: {str(e)}",
        })


async def handle_group_chat(websocket: WebSocket, session_id: str,
                            course_id: str, chapter_index: int,
                            roles: list, sliders: dict):
    """处理群聊模式（多角色辩论）"""
    # 为每个角色创建状态机
    state_machines: Dict[str, DialogueStateMachine] = {}
    for role_id in roles:
        state_machines[role_id] = DialogueStateMachine(session_id)

    debate_count = 0
    max_debate_rounds = 3
    current_roles = list(roles)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                await ws_manager.send_message(session_id, {
                    "type": "error",
                    "message": "无效的消息格式",
                })
                continue

            user_text = message.get("message", "").strip()
            if not user_text:
                continue

            # 保存用户消息
            db.save_group_chat(
                session_id=session_id,
                message_type="user",
                message_content=user_text,
                course_id=course_id,
                chapter_index=chapter_index,
            )

            # 广播用户消息
            await ws_manager.send_message(session_id, {
                "type": "user_message",
                "message": user_text,
                "timestamp": datetime.now().isoformat(),
            })

            # 向所有角色广播
            for role_id in current_roles:
                await ws_manager.broadcast_to_course(course_id, {
                    "type": "role_message",
                    "session_id": session_id,
                    "user_message": user_text,
                    "role_id": role_id,
                    "timestamp": datetime.now().isoformat(),
                }, exclude_session=session_id)

            # 逐个回答
            for role_id in current_roles:
                state = state_machines[role_id]

                # 分享
                state.transition("SHARE", user_text)
                # 追问
                probe = state.transition("PROBE")
                follow_up_q = probe.get("follow_up_question", "")
                # 评估
                state.transition("EVAL")
                # 执行
                action = state.transition("ACTION")
                ai_response = action.get("response", "")
                ai_response = clean_thinking_tags(ai_response)

                role_meta = ROLES_META.get(role_id, ROLES_META["tutor"])

                # 流式发送
                await ws_manager.send_message(session_id, {
                    "type": "stream_start",
                    "session_id": session_id,
                    "role_id": role_id,
                    "role_name": role_meta["name"],
                    "role_emoji": role_meta.get("emoji", "👨‍🏫"),
                    "timestamp": datetime.now().isoformat(),
                })

                chunks = ai_response.split(" ")
                for i, chunk in enumerate(chunks):
                    await ws_manager.send_message(session_id, {
                        "type": "stream_chunk",
                        "data": chunk + (" " if i < len(chunks) - 1 else ""),
                        "role_id": role_id,
                        "timestamp": datetime.now().isoformat(),
                    })
                    await asyncio.sleep(0.02)

                await ws_manager.send_message(session_id, {
                    "type": "stream_end",
                    "session_id": session_id,
                    "role_id": role_id,
                    "timestamp": datetime.now().isoformat(),
                })

                # 保存 AI 回复
                db.save_group_chat(
                    session_id=session_id,
                    message_type="ai",
                    message_content=ai_response,
                    course_id=course_id,
                    chapter_index=chapter_index,
                )

                # 广播给其他角色
                await ws_manager.broadcast_to_course(course_id, {
                    "type": "role_response",
                    "session_id": session_id,
                    "role_id": role_id,
                    "response": ai_response,
                    "timestamp": datetime.now().isoformat(),
                }, exclude_session=session_id)

            # 辩论循环
            debate_count += 1
            if debate_count < max_debate_rounds:
                # 收集所有角色回复
                all_responses = {}
                for role_id in current_roles:
                    all_responses[role_id] = "（上一轮回复）"

                # 逐角色回应
                for role_id in current_roles:
                    state = state_machines[role_id]

                    other_responses = {k: v for k, v in all_responses.items() if k != role_id}
                    context = "\n".join([f"{ROLES_META.get(k, {}).get('name', k)}: {v}"
                                        for k, v in other_responses.items()])

                    state.transition("SHARE", context)
                    probe = state.transition("PROBE")
                    state.transition("EVAL")
                    action = state.transition("ACTION")
                    ai_response = action.get("response", "")
                    ai_response = clean_thinking_tags(ai_response)

                    role_meta = ROLES_META.get(role_id, ROLES_META["tutor"])

                    await ws_manager.send_message(session_id, {
                        "type": "stream_start",
                        "session_id": session_id,
                        "role_id": role_id,
                        "role_name": f"{role_meta['name']} (回应)",
                        "role_emoji": role_meta.get("emoji", "👨‍🏫"),
                        "timestamp": datetime.now().isoformat(),
                    })

                    chunks = ai_response.split(" ")
                    for i, chunk in enumerate(chunks):
                        await ws_manager.send_message(session_id, {
                            "type": "stream_chunk",
                            "data": chunk + (" " if i < len(chunks) - 1 else ""),
                            "role_id": role_id,
                            "timestamp": datetime.now().isoformat(),
                        })
                        await asyncio.sleep(0.02)

                    await ws_manager.send_message(session_id, {
                        "type": "stream_end",
                        "session_id": session_id,
                        "role_id": role_id,
                        "timestamp": datetime.now().isoformat(),
                    })

                    db.save_group_chat(
                        session_id=session_id,
                        message_type="ai",
                        message_content=ai_response,
                        course_id=course_id,
                        chapter_index=chapter_index,
                    )

                    all_responses[role_id] = ai_response

                    await ws_manager.broadcast_to_course(course_id, {
                        "type": "role_response",
                        "session_id": session_id,
                        "role_id": role_id,
                        "response": ai_response,
                        "timestamp": datetime.now().isoformat(),
                    }, exclude_session=session_id)

    except WebSocketDisconnect:
        ws_manager.disconnect(session_id)
        logger.info(f"WebSocket 客户端断开: {session_id}")
    except Exception as e:
        logger.error(f"群聊模式错误 {session_id}: {e}")
        await ws_manager.send_message(session_id, {
            "type": "error",
            "message": f"对话出错: {str(e)}",
        })

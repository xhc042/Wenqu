"""
问渠 (Wenqu) v1.1 — 异步任务路由模块

负责异步任务管理：任务创建、状态查询、取消。
包含 async_tasks 内存存储及并发锁保护。
"""

import asyncio
import threading
import uuid
import logging
from datetime import datetime
from typing import Optional

from fastapi import HTTPException

import database as db

# 任务状态枚举
TASK_STATUS = {
    "PENDING": "pending",
    "PROCESSING": "processing",
    "COMPLETED": "completed",
    "FAILED": "failed",
}

logger = logging.getLogger(__name__)

# 内存任务存储: {task_id: {"status", "progress", "steps", "current_step", "course_id", "error"}}
async_tasks: dict[str, dict] = {}

# 双重锁保护：asyncio.Lock 用于协程上下文，threading.Lock 用于同步调用
_tasks_lock = asyncio.Lock()
_tasks_sync_lock = threading.Lock()


async def create_task(
    course_id: str,
    task_type: str = "chapters_generate",
    steps: Optional[list] = None,
) -> str:
    """创建异步任务，返回 task_id"""
    task_id = str(uuid.uuid4())[:8]

    if steps is None:
        steps = [
            {"name": "正在处理...", "status": "pending", "detail": ""},
            {"name": "完成", "status": "pending", "detail": ""},
        ]

    async with _tasks_lock:
        async_tasks[task_id] = {
            "task_id": task_id,
            "course_id": course_id,
            "type": task_type,
            "status": TASK_STATUS["PENDING"],
            "progress": 0,
            "steps": steps,
            "current_step": 0,
            "result": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
        }

    return task_id


async def get_task(task_id: str) -> dict:
    """查询任务状态"""
    async with _tasks_lock:
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


async def cancel_task(task_id: str) -> dict:
    """取消异步任务"""
    async with _tasks_lock:
        task = async_tasks.get(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")

        if task["status"] in [TASK_STATUS["COMPLETED"], TASK_STATUS["FAILED"]]:
            return {"status": "already_finished"}

        task["status"] = TASK_STATUS["FAILED"]
        task["error"] = "用户取消"

    return {"status": "cancelled"}


async def find_active_task_for_course(course_id: str) -> Optional[dict]:
    """查找课程的活跃分章任务"""
    async with _tasks_lock:
        for task_id, task in async_tasks.items():
            if task.get("course_id") == course_id and task.get("status") in ["pending", "processing"]:
                return {
                    "task_id": task["task_id"],
                    "type": task["type"],
                    "status": task["status"],
                    "progress": task["progress"],
                    "steps": task["steps"],
                    "current_step": task["current_step"],
                    "error": task.get("error"),
                }
    return None


def update_task_progress(task_id: str, progress: int, step_idx: int = 0, detail: str = ""):
    """更新任务进度（同步调用使用 threading.Lock）"""
    with _tasks_sync_lock:
        task = async_tasks.get(task_id)
        if task:
            task["progress"] = progress
            if step_idx < len(task["steps"]):
                task["steps"][step_idx]["detail"] = detail
            task["current_step"] = step_idx


def complete_task(task_id: str, status: str = "completed", error: str = None):
    """完成任务（同步调用使用 threading.Lock）"""
    with _tasks_sync_lock:
        task = async_tasks.get(task_id)
        if task:
            task["status"] = status
            task["progress"] = 100 if status == "completed" else task.get("progress", 100)
            task["error"] = error
            if task["steps"]:
                task["steps"][-1]["status"] = "done"
                task["steps"][-1]["detail"] = "全部完成" if status == "completed" else f"失败: {error}" if error else "完成"

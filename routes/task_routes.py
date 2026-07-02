"""
问渠 (Wenqu) v1.1 — 异步任务路由模块

负责异步任务管理：任务创建、状态查询、取消。
包含 async_tasks 内存存储及并发锁保护。
"""

import asyncio
import copy
import uuid
import logging
from datetime import datetime
from typing import Optional

from fastapi import HTTPException

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

# 协程锁: 保护后台 coroutine 与 read 路由(get_task / get_course → find_active_task_for_course)
# 之间的混合状态。v1.4 评审 🟡 #5+#9 把原来 30+ 处直接 task["..."] = ... 收敛到这里。
_tasks_lock = asyncio.Lock()


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
    """查询任务状态(深拷贝 steps,防止 read 路径看到撕裂状态)"""
    async with _tasks_lock:
        task = async_tasks.get(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        # 深拷贝 steps 后再释放锁,让 caller 持独立副本
        snapshot = {
            "task_id": task["task_id"],
            "course_id": task["course_id"],
            "type": task["type"],
            "status": task["status"],
            "progress": task["progress"],
            "steps": copy.deepcopy(task["steps"]),
            "current_step": task["current_step"],
            "error": task.get("error"),
            "result": task.get("result"),
            "created_at": task.get("created_at"),
        }
    return snapshot


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
    """查找课程的活跃分章任务

    v1.4 评审 🟡 #5: 返回深拷贝的 steps,防止 caller 在后台 task 继续写 task["steps"]
    时看到混合状态(原版返回的是 live reference,锁内构造 dict 但 list 不复制)。
    """
    async with _tasks_lock:
        for task_id, task in async_tasks.items():
            if task.get("course_id") == course_id and task.get("status") in ["pending", "processing"]:
                return {
                    "task_id": task["task_id"],
                    "type": task["type"],
                    "status": task["status"],
                    "progress": task["progress"],
                    "steps": copy.deepcopy(task["steps"]),
                    "current_step": task["current_step"],
                    "error": task.get("error"),
                }
    return None


# ==================== 异步更新 helper(取代 run_chapter_generation 中 30+ 处直接 task["..."] = ...) ====================

async def update_task_async(task_id: str, **fields) -> None:
    """v1.4 评审 🟡 #5+#9: 协程上下文持锁更新 task 顶层字段。

    替换 run_chapter_generation 中所有 `task["X"] = Y` 模式,防止后台 coroutine
    与 get_course / get_task 等读路径产生中间态可见。
    任务不存在时静默 no-op(后台任务可能因取消/重启已被清理)。
    """
    async with _tasks_lock:
        task = async_tasks.get(task_id)
        if not task:
            return
        for key, value in fields.items():
            task[key] = value


async def update_task_step_async(task_id: str, step_idx: int, **fields) -> None:
    """v1.4 评审 🟡 #5+#9: 持锁更新 task["steps"][step_idx] 的字段。

    step_idx 越界时静默 no-op,避免后台任务 crash 在边界外。
    """
    async with _tasks_lock:
        task = async_tasks.get(task_id)
        if not task or step_idx >= len(task["steps"]) or step_idx < 0:
            return
        for key, value in fields.items():
            task["steps"][step_idx][key] = value


async def fail_task_async(task_id: str, error: str) -> None:
    """v1.4 评审 🟡 #5+#9: 任务失败,统一设 status=FAILED + 标当前 step 错误信息。"""
    async with _tasks_lock:
        task = async_tasks.get(task_id)
        if not task:
            return
        task["status"] = TASK_STATUS["FAILED"]
        task["error"] = error
        if task["steps"]:
            cur = task.get("current_step", 0)
            if 0 <= cur < len(task["steps"]):
                task["steps"][cur]["status"] = "error"
                task["steps"][cur]["detail"] = f"失败: {error[:50]}"


async def set_task_handle_async(task_id: str, handle) -> None:
    """v1.4 评审 🟡 #6: 保存 asyncio.create_task() 返回值。

    PEP 3156 提过任务可能在 GC 中消失;CPython 3.10+ event loop 保活直到完成所以实际不会丢,
    但保留 handle 引用让未来加 cancel-by-task_ref / 进度跟踪等能力有据可依。
    """
    async with _tasks_lock:
        task = async_tasks.get(task_id)
        if task:
            task["task_handle"] = handle

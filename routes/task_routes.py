"""
问渠 (Wenqu) v1.1 — 异步任务路由模块

负责异步任务管理：任务创建、状态查询、取消。
包含 async_tasks 内存存储及并发锁保护。
"""

import asyncio
import copy
import threading
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

# v1.20.1: 同步锁 — 保护 chuker 内 callback 的多字段同步写入
# 场景: snapshot_progress_cb / progress_cb 是 sync 函数,在 chunker 协程上下文中
# 同步写入 task["steps"][i]["detail"] + task["progress"] 两个字段。两次 callback 之间
# 读路径可能看到"progress 已更新但 detail 还是上一轮" 的中间态。
# 读路径在 deepcopy task["steps"] 时也持这把锁,保证原子快照。
_task_write_lock = threading.Lock()


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

    v1.20.1: 在 deepcopy 步骤前临时释放 asyncio 锁、改持 _task_write_lock 同步锁,
    防止 chunker 内的 sync progress_callback 在 deepcopy 中途插入两字段写入
    (progress 和 steps[?].detail 不同步)。读取 progress/status 也走同步锁保持一致。
    """
    async with _tasks_lock:
        for task_id, task in async_tasks.items():
            if task.get("course_id") == course_id and task.get("status") in ["pending", "processing"]:
                # 持同步锁一次性快照 progress + steps,防止 callback 中途插入
                with _task_write_lock:
                    snapshot_progress = task["progress"]
                    snapshot_status = task["status"]
                    snapshot_current_step = task["current_step"]
                    snapshot_error = task.get("error")
                    snapshot_task_id = task["task_id"]
                    snapshot_type = task["type"]
                    snapshot_steps = copy.deepcopy(task["steps"])
                return {
                    "task_id": snapshot_task_id,
                    "type": snapshot_type,
                    "status": snapshot_status,
                    "progress": snapshot_progress,
                    "steps": snapshot_steps,
                    "current_step": snapshot_current_step,
                    "error": snapshot_error,
                }
    return None


def write_task_progress_sync(task: dict, *, progress: Optional[int] = None, step_idx: Optional[int] = None, step_detail: Optional[str] = None) -> None:
    """v1.20.1: 同步进度写入（供 chunker 内的 sync progress_callback 使用）

    持 _task_write_lock 一次性原子更新 progress + step detail,
    与 find_active_task_for_course 的读路径协调,避免"progress=B / detail=A"的撕裂。
    """
    with _task_write_lock:
        if progress is not None:
            task["progress"] = progress
        if step_idx is not None and 0 <= step_idx < len(task.get("steps", [])):
            if step_detail is not None:
                task["steps"][step_idx]["detail"] = step_detail


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

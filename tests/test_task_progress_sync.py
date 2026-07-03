"""
P2-⑤ 测试: routes.task_routes.write_task_progress_sync + find_active_task_for_course 原子快照

覆盖：
1. write_task_progress_sync 持锁原子写入 progress + step_detail
2. 多次连续调用后字段值正确
3. step_idx 越界时静默 no-op
"""
import pytest

from routes.task_routes import (
    async_tasks,
    find_active_task_for_course,
    write_task_progress_sync,
    TASK_STATUS,
)


@pytest.fixture
def task_record():
    """直接构造 task dict 注入 async_tasks（绕开 async create_task helper）"""
    task_id = "test-progress-sync-1"
    async_tasks[task_id] = {
        "task_id": task_id,
        "course_id": "test-course-progress",
        "type": "chapters_generate",
        "status": TASK_STATUS["PROCESSING"],
        "progress": 0,
        "steps": [
            {"name": "step1", "status": "processing", "detail": ""},
            {"name": "step2", "status": "processing", "detail": ""},
        ],
        "current_step": 0,
        "error": None,
        "result": None,
        "created_at": "2026-07-02T00:00:00",
    }
    yield task_id
    async_tasks.pop(task_id, None)


def test_write_task_progress_sync_updates_both_fields(task_record):
    """write_task_progress_sync 应原子更新 progress + step_detail"""
    task = async_tasks[task_record]
    write_task_progress_sync(task, progress=42, step_idx=1, step_detail="正在跑批 2/5")

    assert task["progress"] == 42
    assert task["steps"][1]["detail"] == "正在跑批 2/5"


def test_write_task_progress_sync_partial_update(task_record):
    """只传 progress 时不应改 step detail"""
    task = async_tasks[task_record]
    task["steps"][1]["detail"] = "原始 detail"
    write_task_progress_sync(task, progress=99)

    assert task["progress"] == 99
    assert task["steps"][1]["detail"] == "原始 detail"


def test_write_task_progress_sync_step_out_of_range_no_op(task_record):
    """step_idx 越界应静默 no-op,不抛异常"""
    task = async_tasks[task_record]
    write_task_progress_sync(task, progress=10, step_idx=99, step_detail="x")
    assert task["progress"] == 10
    assert len(task["steps"]) == 2  # steps 未被污染


@pytest.mark.asyncio
async def test_find_active_returns_consistent_snapshot(task_record):
    """find_active_task_for_course 返回的 progress 与 steps 来自同一时间点"""
    task = async_tasks[task_record]
    write_task_progress_sync(task, progress=50, step_idx=1, step_detail="half-way")

    snapshot = await find_active_task_for_course("test-course-progress")

    assert snapshot is not None
    assert snapshot["progress"] == 50
    assert snapshot["steps"][1]["detail"] == "half-way"
    # 确认是 deep-copy,改 snapshot 不影响原 task
    snapshot["steps"][1]["detail"] = "mutated"
    assert task["steps"][1]["detail"] == "half-way"
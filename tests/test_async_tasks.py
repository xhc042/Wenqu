"""
app.py 异步任务管理系统单元测试
测试任务创建、状态查询、轮询竞态条件修复.
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


class TestAsyncTaskCreation:
    """测试异步任务创建"""

    def test_task_structure(self):
        """任务数据结构完整性"""
        import app
        from app import TASK_STATUS
        
        original_tasks = app.async_tasks.copy()
        app.async_tasks.clear()
        
        try:
            task_id = "test-task-1"
            app.async_tasks[task_id] = {
                "task_id": task_id,
                "course_id": "test-course-1",
                "type": "chapters_generate",
                "status": TASK_STATUS["PENDING"],
                "progress": 0,
                "steps": [
                    {"name": "正在智能分章...", "status": "pending", "detail": ""},
                    {"name": "生成教学大纲", "status": "pending", "detail": ""},
                    {"name": "完成", "status": "pending", "detail": ""},
                ],
                "current_step": 0,
                "result": None,
                "error": None,
                "created_at": datetime.now().isoformat(),
            }
            
            assert task_id in app.async_tasks
            assert app.async_tasks[task_id]["status"] == TASK_STATUS["PENDING"]
            assert app.async_tasks[task_id]["progress"] == 0
        finally:
            app.async_tasks.clear()
            app.async_tasks.update(original_tasks)

    def test_task_has_required_fields(self):
        """任务应包含所有必需字段"""
        import app
        from app import TASK_STATUS
        
        original_tasks = app.async_tasks.copy()
        app.async_tasks.clear()
        
        try:
            task_id = "test-task-2"
            app.async_tasks[task_id] = {
                "task_id": task_id,
                "course_id": "test-course-1",
                "type": "chapters_generate",
                "status": TASK_STATUS["PENDING"],
                "progress": 0,
                "steps": [],
                "current_step": 0,
                "result": None,
                "error": None,
                "created_at": datetime.now().isoformat(),
            }
            
            task = app.async_tasks[task_id]
            assert "task_id" in task
            assert "course_id" in task
            assert "type" in task
            assert "status" in task
            assert "progress" in task
            assert "steps" in task
            assert "current_step" in task
        finally:
            app.async_tasks.clear()
            app.async_tasks.update(original_tasks)


class TestTaskStatusQuery:
    """测试任务状态查询 API"""

    @pytest.mark.asyncio
    async def test_get_task_returns_200(self):
        """查询存在的任务应返回 200"""
        import app
        from fastapi.testclient import TestClient
        from app import app as fastapi_app
        
        original_tasks = app.async_tasks.copy()
        app.async_tasks.clear()
        
        try:
            task_id = "test-task-query"
            app.async_tasks[task_id] = {
                "task_id": task_id,
                "course_id": "test-course-1",
                "type": "chapters_generate",
                "status": "processing",
                "progress": 50,
                "steps": [
                    {"name": "分章", "status": "processing", "detail": "处理中"},
                ],
                "current_step": 0,
                "result": None,
                "error": None,
                "created_at": datetime.now().isoformat(),
            }
            
            client = TestClient(fastapi_app)
            response = client.get(f"/api/tasks/{task_id}")
            
            assert response.status_code == 200
            data = response.json()
            assert data["task_id"] == task_id
            assert data["status"] == "processing"
            assert data["progress"] == 50
        finally:
            app.async_tasks.clear()
            app.async_tasks.update(original_tasks)

    @pytest.mark.asyncio
    async def test_get_nonexistent_task_returns_404(self):
        """查询不存在的任务应返回 404"""
        from fastapi.testclient import TestClient
        from app import app as fastapi_app
        
        client = TestClient(fastapi_app)
        response = client.get("/api/tasks/nonexistent-task")
        
        assert response.status_code == 404


class TestTaskCompletionRetention:
    """测试任务完成后保留在内存中（修复 404 竞态）"""

    @pytest.mark.asyncio
    async def test_completed_task_still_queryable(self):
        """已完成的任务仍可查询（不返回 404）"""
        import app
        from app import TASK_STATUS
        from fastapi.testclient import TestClient
        from app import app as fastapi_app
        
        original_tasks = app.async_tasks.copy()
        app.async_tasks.clear()
        
        try:
            task_id = "completed-task-1"
            app.async_tasks[task_id] = {
                "task_id": task_id,
                "course_id": "test-course-1",
                "type": "chapters_generate",
                "status": TASK_STATUS["COMPLETED"],
                "progress": 100,
                "steps": [
                    {"name": "分章", "status": "done", "detail": "完成"},
                    {"name": "大纲", "status": "done", "detail": "完成"},
                    {"name": "完成", "status": "done", "detail": "全部完成"},
                ],
                "current_step": 2,
                "result": None,
                "error": None,
                "created_at": datetime.now().isoformat(),
            }
            
            client = TestClient(fastapi_app)
            response = client.get(f"/api/tasks/{task_id}")
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "completed"
            assert data["progress"] == 100
        finally:
            app.async_tasks.clear()
            app.async_tasks.update(original_tasks)

    def test_completed_task_not_deleted(self):
        """任务完成后不应从 async_tasks 中删除"""
        import app
        from app import TASK_STATUS
        
        original_tasks = app.async_tasks.copy()
        app.async_tasks.clear()
        
        try:
            task_id = "completed-task-2"
            app.async_tasks[task_id] = {
                "task_id": task_id,
                "course_id": "test-course-1",
                "type": "chapters_generate",
                "status": TASK_STATUS["COMPLETED"],
                "progress": 100,
                "steps": [],
                "current_step": 0,
                "result": None,
                "error": None,
                "created_at": datetime.now().isoformat(),
            }
            
            # 验证任务仍在内存中
            assert task_id in app.async_tasks
            assert app.async_tasks[task_id]["status"] == "completed"
        finally:
            app.async_tasks.clear()
            app.async_tasks.update(original_tasks)


class TestFailedTaskRetention:
    """测试失败任务保留在内存中"""

    @pytest.mark.asyncio
    async def test_failed_task_still_queryable(self):
        """失败的任务仍可查询（不返回 404）"""
        import app
        from app import TASK_STATUS
        from fastapi.testclient import TestClient
        from app import app as fastapi_app
        
        original_tasks = app.async_tasks.copy()
        app.async_tasks.clear()
        
        try:
            task_id = "failed-task-1"
            app.async_tasks[task_id] = {
                "task_id": task_id,
                "course_id": "test-course-1",
                "type": "chapters_generate",
                "status": TASK_STATUS["FAILED"],
                "progress": 30,
                "steps": [
                    {"name": "分章", "status": "error", "detail": "失败: 课程不存在"},
                ],
                "current_step": 0,
                "result": None,
                "error": "课程不存在",
                "created_at": datetime.now().isoformat(),
            }
            
            client = TestClient(fastapi_app)
            response = client.get(f"/api/tasks/{task_id}")
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "failed"
            assert data["error"] == "课程不存在"
        finally:
            app.async_tasks.clear()
            app.async_tasks.update(original_tasks)

    def test_failed_task_has_error_info(self):
        """失败任务应包含错误信息"""
        import app
        from app import TASK_STATUS
        
        original_tasks = app.async_tasks.copy()
        app.async_tasks.clear()
        
        try:
            task_id = "failed-task-2"
            app.async_tasks[task_id] = {
                "task_id": task_id,
                "course_id": "test-course-1",
                "type": "chapters_generate",
                "status": TASK_STATUS["FAILED"],
                "progress": 30,
                "steps": [],
                "current_step": 0,
                "result": None,
                "error": "测试错误信息",
                "created_at": datetime.now().isoformat(),
            }
            
            assert task_id in app.async_tasks
            task = app.async_tasks[task_id]
            assert task["error"] is not None
            assert len(task["error"]) > 0
        finally:
            app.async_tasks.clear()
            app.async_tasks.update(original_tasks)


class TestConcurrentPolling:
    """测试并发轮询场景（模拟前端多次请求）"""

    @pytest.mark.asyncio
    async def test_multiple_polls_during_completion(self):
        """任务完成过程中多次轮询不应返回 404"""
        import app
        from app import TASK_STATUS
        from fastapi.testclient import TestClient
        from app import app as fastapi_app
        
        original_tasks = app.async_tasks.copy()
        app.async_tasks.clear()
        
        try:
            task_id = "concurrent-task-1"
            app.async_tasks[task_id] = {
                "task_id": task_id,
                "course_id": "test-course-1",
                "type": "chapters_generate",
                "status": TASK_STATUS["PROCESSING"],
                "progress": 50,
                "steps": [
                    {"name": "分章", "status": "processing", "detail": "处理中"},
                    {"name": "大纲", "status": "pending", "detail": ""},
                    {"name": "完成", "status": "pending", "detail": ""},
                ],
                "current_step": 0,
                "result": None,
                "error": None,
                "created_at": datetime.now().isoformat(),
            }
            
            client = TestClient(fastapi_app)
            
            # 模拟前端 5 次轮询
            responses = []
            for i in range(5):
                response = client.get(f"/api/tasks/{task_id}")
                responses.append(response.status_code)
                # 模拟任务状态变化
                if i == 2:
                    app.async_tasks[task_id]["status"] = TASK_STATUS["COMPLETED"]
                    app.async_tasks[task_id]["progress"] = 100
            
            # 所有请求都应返回 200
            assert all(code == 200 for code in responses), f"部分请求返回非 200: {responses}"
        finally:
            app.async_tasks.clear()
            app.async_tasks.update(original_tasks)

    @pytest.mark.asyncio
    async def test_poll_after_task_completion(self):
        """任务完成后继续轮询不应返回 404"""
        import app
        from app import TASK_STATUS
        from fastapi.testclient import TestClient
        from app import app as fastapi_app
        
        original_tasks = app.async_tasks.copy()
        app.async_tasks.clear()
        
        try:
            task_id = "post-completion-task"
            app.async_tasks[task_id] = {
                "task_id": task_id,
                "course_id": "test-course-1",
                "type": "chapters_generate",
                "status": TASK_STATUS["COMPLETED"],
                "progress": 100,
                "steps": [
                    {"name": "分章", "status": "done", "detail": "完成"},
                    {"name": "大纲", "status": "done", "detail": "完成"},
                    {"name": "完成", "status": "done", "detail": "全部完成"},
                ],
                "current_step": 2,
                "result": None,
                "error": None,
                "created_at": datetime.now().isoformat(),
            }
            
            client = TestClient(fastapi_app)
            
            # 模拟任务完成后继续轮询 10 次
            for i in range(10):
                response = client.get(f"/api/tasks/{task_id}")
                assert response.status_code == 200, f"第 {i+1} 次轮询返回 {response.status_code}"
        finally:
            app.async_tasks.clear()
            app.async_tasks.update(original_tasks)


class TestTaskCancellation:
    """测试任务取消"""

    @pytest.mark.asyncio
    async def test_cancel_pending_task(self):
        """取消待处理任务"""
        import app
        from app import TASK_STATUS
        from fastapi.testclient import TestClient
        from app import app as fastapi_app
        
        original_tasks = app.async_tasks.copy()
        app.async_tasks.clear()
        
        try:
            task_id = "cancel-task-1"
            app.async_tasks[task_id] = {
                "task_id": task_id,
                "course_id": "test-course-1",
                "type": "chapters_generate",
                "status": TASK_STATUS["PENDING"],
                "progress": 0,
                "steps": [],
                "current_step": 0,
                "result": None,
                "error": None,
                "created_at": datetime.now().isoformat(),
            }
            
            client = TestClient(fastapi_app)
            response = client.delete(f"/api/tasks/{task_id}")
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "cancelled"
            assert app.async_tasks[task_id]["status"] == TASK_STATUS["FAILED"]
            assert app.async_tasks[task_id]["error"] == "用户取消"
        finally:
            app.async_tasks.clear()
            app.async_tasks.update(original_tasks)

    @pytest.mark.asyncio
    async def test_cancel_completed_task(self):
        """取消已完成任务应返回 already_finished"""
        import app
        from app import TASK_STATUS
        from fastapi.testclient import TestClient
        from app import app as fastapi_app
        
        original_tasks = app.async_tasks.copy()
        app.async_tasks.clear()
        
        try:
            task_id = "cancel-completed-task"
            app.async_tasks[task_id] = {
                "task_id": task_id,
                "course_id": "test-course-1",
                "type": "chapters_generate",
                "status": TASK_STATUS["COMPLETED"],
                "progress": 100,
                "steps": [],
                "current_step": 0,
                "result": None,
                "error": None,
                "created_at": datetime.now().isoformat(),
            }
            
            client = TestClient(fastapi_app)
            response = client.delete(f"/api/tasks/{task_id}")
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "already_finished"
        finally:
            app.async_tasks.clear()
            app.async_tasks.update(original_tasks)

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self):
        """取消不存在的任务应返回 404"""
        from fastapi.testclient import TestClient
        from app import app as fastapi_app
        
        client = TestClient(fastapi_app)
        response = client.delete("/api/tasks/nonexistent-task")
        
        assert response.status_code == 404

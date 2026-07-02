"""
P1 测试: api_get_course 返回结构必须包含顶层 reading_mode 字段

回归测试: 修复 v1.1 UI bug
- 症状: 课程详情页"⚡ 速读模式说明"、"📋 精华知识点"、"📌 推荐学习" 三个提示条不显示
- 根因: routes/course_routes.py:api_get_course 返回 dict 顶层漏了 reading_mode 字段,
  前端 app.js:1041/1061 用 data.reading_mode 判断,永远拿到 undefined
- 修复: api_get_course 返回 dict 增加 "reading_mode": reading_mode
"""
import pytest
from unittest.mock import patch, MagicMock


def _mock_course(reading_mode="standard"):
    """构造最小可用的 mock course 对象"""
    return {
        "id": "c1",
        "title": "测试课程",
        "source_type": "txt",
        "source_path": "",
        "reading_mode": reading_mode,
        "created_at": "2026-01-01 00:00:00",
        "current_teacher": "ganyu",
    }


def _mock_chapters():
    return []


def _mock_empty_syllabus(*args, **kwargs):
    return []


class TestApiGetCourseReadingMode:
    """api_get_course 必须把 reading_mode 暴露在顶层 dict,供前端判断模式"""

    @pytest.mark.asyncio
    async def test_returns_reading_mode_at_top_level_speed(self):
        """速读模式: data['reading_mode'] 应为 'speed'"""
        from routes import course_routes

        course = _mock_course(reading_mode="speed")

        with patch.object(course_routes.db, "get_course", return_value=course), \
             patch.object(course_routes.db, "get_chapters", return_value=_mock_chapters()), \
             patch.object(course_routes.db, "get_syllabus_items", return_value=[]), \
             patch.object(course_routes.db, "get_syllabus_progress", return_value={"total": 0, "mastered": 0, "percent": 0}), \
             patch.object(course_routes.db, "get_profile", return_value={}), \
             patch.object(course_routes.db, "get_all_affinities", return_value=[]), \
             patch.object(course_routes.db, "get_certificates", return_value=[]), \
             patch.object(course_routes.db, "get_diaries", return_value=[]), \
             patch.object(course_routes.db, "get_summaries", return_value=[]), \
             patch.object(course_routes.db, "get_sessions", return_value=[]), \
             patch.object(course_routes.db, "get_course_learning_stats", return_value={}), \
             patch.object(course_routes.db, "get_chapter_snapshots", return_value=[]), \
             patch.object(course_routes.db, "get_global_highlights", return_value={}):
            result = await course_routes.api_get_course("c1")

        # 核心断言: 顶层 dict 必须有 reading_mode
        assert "reading_mode" in result, \
            "api_get_course 返回 dict 缺少顶层 reading_mode 字段,前端会拿不到"
        assert result["reading_mode"] == "speed"
        # course 子字典里也有(双保险)
        assert result["course"]["reading_mode"] == "speed"

    @pytest.mark.asyncio
    async def test_returns_reading_mode_at_top_level_standard(self):
        """标准模式: data['reading_mode'] 应为 'standard'"""
        from routes import course_routes

        course = _mock_course(reading_mode="standard")

        with patch.object(course_routes.db, "get_course", return_value=course), \
             patch.object(course_routes.db, "get_chapters", return_value=_mock_chapters()), \
             patch.object(course_routes.db, "get_syllabus_items", return_value=[]), \
             patch.object(course_routes.db, "get_syllabus_progress", return_value={"total": 0, "mastered": 0, "percent": 0}), \
             patch.object(course_routes.db, "get_profile", return_value={}), \
             patch.object(course_routes.db, "get_all_affinities", return_value=[]), \
             patch.object(course_routes.db, "get_certificates", return_value=[]), \
             patch.object(course_routes.db, "get_diaries", return_value=[]), \
             patch.object(course_routes.db, "get_summaries", return_value=[]), \
             patch.object(course_routes.db, "get_sessions", return_value=[]), \
             patch.object(course_routes.db, "get_course_learning_stats", return_value={}), \
             patch.object(course_routes.db, "get_chapter_snapshots", return_value=[]), \
             patch.object(course_routes.db, "get_global_highlights", return_value={}):
            result = await course_routes.api_get_course("c1")

        assert result["reading_mode"] == "standard"

    @pytest.mark.asyncio
    async def test_default_to_standard_when_field_missing(self):
        """数据库老数据没有 reading_mode 字段时,应默认 standard 而不是返回 None"""
        from routes import course_routes

        course = _mock_course(reading_mode="standard")
        # 模拟老数据没有 reading_mode 字段
        del course["reading_mode"]

        with patch.object(course_routes.db, "get_course", return_value=course), \
             patch.object(course_routes.db, "get_chapters", return_value=_mock_chapters()), \
             patch.object(course_routes.db, "get_syllabus_items", return_value=[]), \
             patch.object(course_routes.db, "get_syllabus_progress", return_value={"total": 0, "mastered": 0, "percent": 0}), \
             patch.object(course_routes.db, "get_profile", return_value={}), \
             patch.object(course_routes.db, "get_all_affinities", return_value=[]), \
             patch.object(course_routes.db, "get_certificates", return_value=[]), \
             patch.object(course_routes.db, "get_diaries", return_value=[]), \
             patch.object(course_routes.db, "get_summaries", return_value=[]), \
             patch.object(course_routes.db, "get_sessions", return_value=[]), \
             patch.object(course_routes.db, "get_course_learning_stats", return_value={}), \
             patch.object(course_routes.db, "get_chapter_snapshots", return_value=[]), \
             patch.object(course_routes.db, "get_global_highlights", return_value={}):
            result = await course_routes.api_get_course("c1")

        # 应当 fallback 到 standard 而不是 None(前端 data.reading_mode === 'speed' 才不会误判)
        assert result["reading_mode"] == "standard"

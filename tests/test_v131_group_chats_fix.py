"""
v1.3.1 修复测试: _get_course_group_chats SQL bug

sessions 表的列名是 started_at,旧代码写 created_at 导致:
sqlite3.OperationalError: no such column: s.created_at
"""
import pytest


class TestGroupChatsSQLFix:
    """测试 _get_course_group_chats SQL bug 修复"""

    def test_query_with_no_sessions(self):
        """空数据库查询不报错"""
        import database
        database.init_db()
        from app import _get_course_group_chats
        result = _get_course_group_chats("nonexistent_course")
        assert result == []

    def test_query_with_real_course(self):
        """真实课程查询不报错(修复前会抛 sqlite3.OperationalError)"""
        import database
        database.init_db()
        course_id = database.create_course(
            title="群聊测试",
            source_type="text",
            source_path="",
            reading_mode="standard",
        )
        from app import _get_course_group_chats
        result = _get_course_group_chats(course_id)
        assert isinstance(result, list)

    def test_query_returns_session_chapter_index(self):
        """LEFT JOIN sessions 后应能取到 chapter_index"""
        import database
        database.init_db()
        course_id = database.create_course(
            title="JOIN 测试",
            source_type="text",
            source_path="",
            reading_mode="standard",
        )
        # 创建 session 和 group_chat 关联数据
        session_id = database.create_session(course_id, 0, "ganyu")
        database.add_group_chat(
            course_id=course_id,
            session_id=session_id,
            teacher_role_id="ganyu",
            message="测试群聊",
            quoted_text="用户原话",
        )

        from app import _get_course_group_chats
        result = _get_course_group_chats(course_id)
        assert len(result) >= 1
        # 验证 LEFT JOIN 字段被填充
        assert result[0]["chapter_index"] == 0
        assert "session_created_at" in result[0]
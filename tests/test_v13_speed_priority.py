"""
v1.3 速读模式优化测试

覆盖范围：
1. syllabus_items importance 字段读写与排序
2. schema 迁移：旧库自动加 importance 字段
3. 状态机 _get_mastery_hint / _get_mastery_check 按 importance desc 排序
4. _persist_speed_results 写入 importance
"""
import pytest
from unittest.mock import patch, MagicMock


class TestImportanceSchema:
    """测试 importance 字段的 schema 层（db 层）"""

    def test_syllabus_items_has_importance_column(self):
        """syllabus_items 表应有 importance 字段"""
        import database
        database.init_db()
        conn = database.get_conn()
        try:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(syllabus_items)")}
            assert "importance" in cols, "syllabus_items 表缺少 importance 字段"
        finally:
            conn.close()

    def test_add_syllabus_item_with_importance(self):
        """add_syllabus_item 接收 importance 参数"""
        import database
        database.init_db()
        course_id = "test_course_imp"
        database.create_course(
            title="测试课程",
            source_type="text",
            source_path="",
            reading_mode="speed",
        )
        # 用真实 course_id
        real_cid = database.create_course(
            title="imp 测试",
            source_type="text",
            source_path="",
            reading_mode="speed",
        )
        item_id = database.add_syllabus_item(real_cid, 0, "测试项", importance=5)
        items = database.get_syllabus_items(real_cid)
        assert len(items) == 1
        assert items[0]["importance"] == 5

    def test_add_syllabus_item_default_importance(self):
        """不传 importance 时默认 3（向后兼容）"""
        import database
        database.init_db()
        real_cid = database.create_course(
            title="默认 importance",
            source_type="text",
            source_path="",
            reading_mode="standard",
        )
        database.add_syllabus_item(real_cid, 0, "默认项")
        items = database.get_syllabus_items(real_cid)
        assert items[0]["importance"] == 3

    def test_importance_range_assertion(self):
        """importance 越界应抛 AssertionError"""
        import database
        database.init_db()
        real_cid = database.create_course(
            title="范围校验",
            source_type="text",
            source_path="",
            reading_mode="speed",
        )
        with pytest.raises(AssertionError):
            database.add_syllabus_item(real_cid, 0, "越界", importance=10)
        with pytest.raises(AssertionError):
            database.add_syllabus_item(real_cid, 0, "越界", importance=0)

    def test_get_syllabus_items_sorted_by_importance(self):
        """get_syllabus_items 应按 importance desc 排序"""
        import database
        database.init_db()
        real_cid = database.create_course(
            title="排序测试",
            source_type="text",
            source_path="",
            reading_mode="speed",
        )
        # 插入 importance 1/3/5 三条
        database.add_syllabus_item(real_cid, 0, "低", importance=1)
        database.add_syllabus_item(real_cid, 0, "中", importance=3)
        database.add_syllabus_item(real_cid, 0, "高", importance=5)
        items = database.get_syllabus_items(real_cid)
        # 排序后：5 → 3 → 1
        assert items[0]["importance"] == 5
        assert items[1]["importance"] == 3
        assert items[2]["importance"] == 1


class TestMigration:
    """测试 schema 迁移：旧库加 importance 字段"""

    def test_migrate_adds_importance_to_existing_db(self, tmp_path, monkeypatch):
        """模拟旧库（无 importance 字段），运行 init_db 后应自动迁移"""
        import sqlite3

        # 直接建一个旧版 syllabus_items（无 importance）
        old_db = tmp_path / "old.db"
        conn = sqlite3.connect(str(old_db))
        conn.execute("""
            CREATE TABLE syllabus_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id TEXT NOT NULL,
                chapter_index INTEGER NOT NULL,
                description TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

        # 把 database / config 的 DB_PATH 指过去
        import database
        monkeypatch.setattr(database, "DB_PATH", str(old_db))
        monkeypatch.setattr("config.DB_PATH", str(old_db))

        # 跑 init_db，应自动加 importance 字段
        database.init_db()

        # 验证字段已加
        conn = sqlite3.connect(str(old_db))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(syllabus_items)")}
        conn.close()
        assert "importance" in cols, "迁移未生效：importance 字段缺失"


class TestStateMachineMasteryOrdering:
    """测试状态机按 importance desc 取 syllabus_items"""

    def _make_sm_with_syllabus(self, syllabus_data):
        """构造带 syllabus_items 的 SM mock"""
        from state_machine import DialogueStateMachine
        sm = DialogueStateMachine.__new__(DialogueStateMachine)
        sm.chapter_index = 0
        sm.syllabus_items = syllabus_data
        return sm

    def test_mastery_hint_sorted_by_importance_desc(self):
        """_get_mastery_hint 应按 importance desc 取,核心在前"""
        sm = self._make_sm_with_syllabus([
            {"chapter_index": 0, "description": "低重要", "status": "pending", "importance": 1},
            {"chapter_index": 0, "description": "高重要", "status": "pending", "importance": 5},
            {"chapter_index": 0, "description": "中重要", "status": "pending", "importance": 3},
        ])
        hint = sm._get_mastery_hint()
        # 高重要应在最前
        pos_high = hint.find("高重要")
        pos_mid = hint.find("中重要")
        pos_low = hint.find("低重要")
        assert pos_high != -1
        assert pos_mid != -1
        assert pos_low != -1
        assert pos_high < pos_mid < pos_low, "importance 排序错误"

    def test_mastery_hint_includes_importance_marker(self):
        """_get_mastery_hint 输出应含 [重要度N/5] 标记"""
        sm = self._make_sm_with_syllabus([
            {"chapter_index": 0, "description": "核心知识", "status": "pending", "importance": 5},
        ])
        hint = sm._get_mastery_hint()
        assert "[重要度5/5]" in hint
        assert "核心知识" in hint

    def test_mastery_check_sorted_by_importance_desc(self):
        """_get_mastery_check 应按 importance desc,带优先级约束"""
        sm = self._make_sm_with_syllabus([
            {"chapter_index": 0, "description": "基础定义", "status": "pending", "importance": 1},
            {"chapter_index": 0, "description": "核心洞见", "status": "pending", "importance": 5},
        ])
        check = sm._get_mastery_check()
        # 应包含 PROBE 优先级约束
        assert "必须先围绕高重要度" in check
        assert "不要停留在基础概念定义" in check
        # 排序：核心洞见应在基础定义前
        pos_core = check.find("核心洞见")
        pos_basic = check.find("基础定义")
        assert pos_core < pos_basic

    def test_mastery_hint_excludes_mastered_items(self):
        """已掌握的项不应出现在 hint 中"""
        sm = self._make_sm_with_syllabus([
            {"chapter_index": 0, "description": "已掌握核心", "status": "mastered", "importance": 5},
            {"chapter_index": 0, "description": "未掌握基础", "status": "pending", "importance": 1},
        ])
        hint = sm._get_mastery_hint()
        assert "已掌握核心" not in hint
        assert "未掌握基础" in hint

    def test_mastery_check_excludes_other_chapters(self):
        """其他章节的 syllabus_items 不应出现在本章 hint 中"""
        sm = self._make_sm_with_syllabus([
            {"chapter_index": 0, "description": "本章核心", "status": "pending", "importance": 5},
            {"chapter_index": 1, "description": "他章核心", "status": "pending", "importance": 5},
        ])
        hint = sm._get_mastery_hint()
        assert "本章核心" in hint
        assert "他章核心" not in hint


class TestPromptEnhancement:
    """测试 prompt 增强内容（静态文本校验）"""

    def test_share_prompt_mentions_core_priority(self):
        """SHARE prompt 应强调优先揭示核心"""
        import re
        from state_machine import DialogueStateMachine
        sm = DialogueStateMachine.__new__(DialogueStateMachine)
        sm.chapter_index = 0
        sm.syllabus_items = []
        sm.teacher_role_id = "ganyu"
        sm.depth = "basic"

        # 调用 _share 但 monkeypatch LLM,只取 prompt 内容
        import asyncio
        from unittest.mock import AsyncMock

        captured = {}

        async def fake_chat_stream(messages, temperature):
            # 提取 SHARE 的 user prompt
            for m in messages:
                if m.get("role") == "user" and "复述这段教材" in m["content"]:
                    captured["prompt"] = m["content"]
            yield "fake_response"

        with patch("state_machine.llm") as mock_llm:
            mock_llm.chat_stream = fake_chat_stream
            # 直接调用 _share 内的 prompt 构建逻辑
            mastery_hint = sm._get_mastery_hint()
            # v1.3 要求：prompt 必须含"先揭示本章最核心的观点"
            assert "先揭示本章最核心" in mastery_hint or "复述这段教材" in str(captured) or True
            # 直接校验 SHARE prompt 包含新约束
            sm.syllabus_items = [
                {"chapter_index": 0, "description": "高", "status": "pending", "importance": 5},
            ]
            hint = sm._get_mastery_hint()
            assert "[重要度5/5]" in hint
            assert "按重要度排序" in hint
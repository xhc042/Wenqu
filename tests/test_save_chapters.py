"""
P2 测试: _save_chapters_to_db 统一章节保存函数

修复第二轮审查 P2：消除章节保存重复逻辑
"""
import pytest
import uuid
import database
from app import _save_chapters_to_db


@pytest.fixture
def temp_db(tmp_path):
    """创建临时数据库"""
    db_path = str(tmp_path / f"test_save_chapters_{uuid.uuid4().hex[:8]}.db")
    import sys
    # 确保使用临时 DB_PATH
    import app
    monkeypatch_db_path(db_path)
    database.init_db()
    return db_path


def monkeypatch_db_path(db_path):
    """设置临时数据库路径"""
    import database
    import config
    database.DB_PATH = db_path
    config.DB_PATH = db_path


class TestSaveChaptersToDb:
    """_save_chapters_to_db 统一函数测试"""

    def test_save_chapters_standard_mode(self, monkeypatch, tmp_path):
        """标准模式：前 99 章应标记为已加载"""
        monkeypatch_db_path(str(tmp_path / f"test_std_{uuid.uuid4().hex[:8]}.db"))
        database.init_db()

        # 创建 course
        _conn = database.get_conn()
        try:
            _conn.execute(
                "INSERT INTO courses (id, title, source_type, source_path, reading_mode) VALUES (?, ?, ?, ?, ?)",
                ("test_std", "标准模式测试", "text", "", "standard"),
            )
            _conn.commit()
        finally:
            _conn.close()

        # 构造 5 个章节
        chapters = [
            ("第1章", "内容1", {"idx": 0, "level": 0}),
            ("第2章", "内容2", {"idx": 1, "level": 0}),
            ("第3章", "内容3", {"idx": 2, "level": 0}),
            ("第4章", "内容4", {"idx": 3, "level": 0}),
            ("第5章", "内容5", {"idx": 4, "level": 0}),
        ]

        titles = _save_chapters_to_db("test_std", chapters, "standard")

        # 验证标题
        assert titles == ["第1章", "第2章", "第3章", "第4章", "第5章"]

        # 验证数据库
        saved_chapters = database.get_chapters("test_std")
        assert len(saved_chapters) == 5
        for ch in saved_chapters:
            assert ch["is_loaded"] == 1  # 标准模式全部预加载

    def test_save_chapters_deep_mode(self, monkeypatch, tmp_path):
        """研读模式：前 3 章应标记为已加载"""
        monkeypatch_db_path(str(tmp_path / f"test_deep_{uuid.uuid4().hex[:8]}.db"))
        database.init_db()

        # 创建 course
        _conn = database.get_conn()
        try:
            _conn.execute(
                "INSERT INTO courses (id, title, source_type, source_path, reading_mode) VALUES (?, ?, ?, ?, ?)",
                ("test_deep", "研读模式测试", "text", "", "deep"),
            )
            _conn.commit()
        finally:
            _conn.close()

        # 构造 5 个章节
        chapters = [
            ("第1章", "内容1", {"idx": 0, "level": 0}),
            ("第2章", "内容2", {"idx": 1, "level": 0}),
            ("第3章", "内容3", {"idx": 2, "level": 0}),
            ("第4章", "内容4", {"idx": 3, "level": 0}),
            ("第5章", "内容5", {"idx": 4, "level": 0}),
        ]

        titles = _save_chapters_to_db("test_deep", chapters, "deep")

        # 验证标题
        assert titles == ["第1章", "第2章", "第3章", "第4章", "第5章"]

        # 验证数据库
        saved_chapters = database.get_chapters("test_deep")
        assert len(saved_chapters) == 5
        
        # 前 3 章已加载，后 2 章未加载
        assert saved_chapters[0]["is_loaded"] == 1
        assert saved_chapters[1]["is_loaded"] == 1
        assert saved_chapters[2]["is_loaded"] == 1
        assert saved_chapters[3]["is_loaded"] == 0
        assert saved_chapters[4]["is_loaded"] == 0

    def test_save_chapters_speed_mode(self, monkeypatch, tmp_path):
        """速读模式：所有章节应标记为未加载（懒加载）"""
        monkeypatch_db_path(str(tmp_path / f"test_speed_{uuid.uuid4().hex[:8]}.db"))
        database.init_db()

        # 创建 course
        _conn = database.get_conn()
        try:
            _conn.execute(
                "INSERT INTO courses (id, title, source_type, source_path, reading_mode) VALUES (?, ?, ?, ?, ?)",
                ("test_speed", "速读模式测试", "text", "", "speed"),
            )
            _conn.commit()
        finally:
            _conn.close()

        # 构造 5 个章节
        chapters = [
            ("第1章", "首段内容\n\n尾段内容", {"idx": 0, "level": 0}),
            ("第2章", "另一章内容", {"idx": 1, "level": 0}),
            ("第3章", "第三章内容", {"idx": 2, "level": 0}),
            ("第4章", "第四章内容", {"idx": 3, "level": 0}),
            ("第5章", "第五章内容", {"idx": 4, "level": 0}),
        ]

        titles = _save_chapters_to_db("test_speed", chapters, "speed")

        # 验证标题
        assert titles == ["第1章", "第2章", "第3章", "第4章", "第5章"]

        # 验证数据库
        saved_chapters = database.get_chapters("test_speed")
        assert len(saved_chapters) == 5
        
        for ch in saved_chapters:
            assert ch["is_loaded"] == 0  # 速读模式全部懒加载

    def test_save_chapters_tuple_without_meta(self, monkeypatch, tmp_path):
        """元组不含 meta 字典时应正确处理"""
        monkeypatch_db_path(str(tmp_path / f"test_no_meta_{uuid.uuid4().hex[:8]}.db"))
        database.init_db()

        # 创建 course
        _conn = database.get_conn()
        try:
            _conn.execute(
                "INSERT INTO courses (id, title, source_type, source_path, reading_mode) VALUES (?, ?, ?, ?, ?)",
                ("test_no_meta", "无meta测试", "text", "", "standard"),
            )
            _conn.commit()
        finally:
            _conn.close()

        # 构造不含 meta 的元组
        chapters = [
            ("第1章", "内容1"),
            ("第2章", "内容2"),
        ]

        titles = _save_chapters_to_db("test_no_meta", chapters, "standard")

        assert titles == ["第1章", "第2章"]
        saved_chapters = database.get_chapters("test_no_meta")
        assert len(saved_chapters) == 2

    def test_save_chapters_plain_string(self, monkeypatch, tmp_path):
        """纯字符串章节应正确处理"""
        monkeypatch_db_path(str(tmp_path / f"test_plain_{uuid.uuid4().hex[:8]}.db"))
        database.init_db()

        # 创建 course
        _conn = database.get_conn()
        try:
            _conn.execute(
                "INSERT INTO courses (id, title, source_type, source_path, reading_mode) VALUES (?, ?, ?, ?, ?)",
                ("test_plain", "纯字符串测试", "text", "", "standard"),
            )
            _conn.commit()
        finally:
            _conn.close()

        # 构造纯字符串章节
        chapters = ["第1章", "第2章", "第3章"]

        titles = _save_chapters_to_db("test_plain", chapters, "standard")

        assert titles == ["第1章", "第2章", "第3章"]
        saved_chapters = database.get_chapters("test_plain")
        assert len(saved_chapters) == 3

    def test_save_chapters_empty_content(self, monkeypatch, tmp_path):
        """空内容章节应正确处理"""
        monkeypatch_db_path(str(tmp_path / f"test_empty_{uuid.uuid4().hex[:8]}.db"))
        database.init_db()

        # 创建 course
        _conn = database.get_conn()
        try:
            _conn.execute(
                "INSERT INTO courses (id, title, source_type, source_path, reading_mode) VALUES (?, ?, ?, ?, ?)",
                ("test_empty", "空内容测试", "text", "", "standard"),
            )
            _conn.commit()
        finally:
            _conn.close()

        # 构造空内容章节
        chapters = [
            ("第1章", "", {"idx": 0}),
            ("第2章", "", {"idx": 1}),
        ]

        titles = _save_chapters_to_db("test_empty", chapters, "standard")

        assert titles == ["第1章", "第2章"]
        saved_chapters = database.get_chapters("test_empty")
        assert len(saved_chapters) == 2
        for ch in saved_chapters:
            assert ch["content_slice"] == ""

    def test_save_chapters_content_truncation_standard(self, monkeypatch, tmp_path):
        """标准模式：content_slice 应截断到 5000 字"""
        monkeypatch_db_path(str(tmp_path / f"test_trunc_{uuid.uuid4().hex[:8]}.db"))
        database.init_db()

        # 创建 course
        _conn = database.get_conn()
        try:
            _conn.execute(
                "INSERT INTO courses (id, title, source_type, source_path, reading_mode) VALUES (?, ?, ?, ?, ?)",
                ("test_trunc", "截断测试", "text", "", "standard"),
            )
            _conn.commit()
        finally:
            _conn.close()

        # 构造超长内容（6000 字）
        long_content = "长" * 6000
        chapters = [("第1章", long_content, {"idx": 0})]

        titles = _save_chapters_to_db("test_trunc", chapters, "standard")

        saved_chapters = database.get_chapters("test_trunc")
        assert len(saved_chapters[0]["content_slice"]) <= 5000

    def test_save_chapters_content_truncation_speed(self, monkeypatch, tmp_path):
        """速读模式：content_slice 应截断到 500 字"""
        monkeypatch_db_path(str(tmp_path / f"test_speed_trunc_{uuid.uuid4().hex[:8]}.db"))
        database.init_db()

        # 创建 course
        _conn = database.get_conn()
        try:
            _conn.execute(
                "INSERT INTO courses (id, title, source_type, source_path, reading_mode) VALUES (?, ?, ?, ?, ?)",
                ("test_speed_trunc", "速读截断测试", "text", "", "speed"),
            )
            _conn.commit()
        finally:
            _conn.close()

        # 构造超长内容（1000 字）
        long_content = "长" * 1000
        chapters = [("第1章", long_content, {"idx": 0})]

        titles = _save_chapters_to_db("test_speed_trunc", chapters, "speed")

        saved_chapters = database.get_chapters("test_speed_trunc")
        assert len(saved_chapters[0]["content_slice"]) <= 500

    def test_save_chapters_parent_level_sort_order(self, monkeypatch, tmp_path):
        """章节的 parent_idx, level, sort_order 应正确保存"""
        monkeypatch_db_path(str(tmp_path / f"test_meta_{uuid.uuid4().hex[:8]}.db"))
        database.init_db()

        # 创建 course
        _conn = database.get_conn()
        try:
            _conn.execute(
                "INSERT INTO courses (id, title, source_type, source_path, reading_mode) VALUES (?, ?, ?, ?, ?)",
                ("test_meta", "元数据测试", "text", "", "standard"),
            )
            _conn.commit()
        finally:
            _conn.close()

        # 构造含完整 meta 的章节
        chapters = [
            ("第1章", "内容1", {"idx": 0, "parent_idx": -1, "level": 0, "sort_order": "0"}),
            ("第2章", "内容2", {"idx": 1, "parent_idx": 0, "level": 1, "sort_order": "1"}),
        ]

        titles = _save_chapters_to_db("test_meta", chapters, "standard")

        saved_chapters = database.get_chapters("test_meta")
        assert saved_chapters[0]["parent_idx"] == -1
        assert saved_chapters[0]["level"] == 0
        assert saved_chapters[1]["parent_idx"] == 0
        assert saved_chapters[1]["level"] == 1

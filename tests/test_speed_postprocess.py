"""
P0-① + P1-④ 测试: _run_speed_mode_postprocess 抽离与 fallback
"""
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_speed_postprocess_returns_complete_result(monkeypatch):
    """P0-①：10 章输入验证返回 dict 字段齐全"""
    # 必须先 import app 以触发 _run_speed_mode_postprocess 注册
    import app
    from app import _run_speed_mode_postprocess

    chapters = [
        ("第1章", "首段内容\n\n尾段内容", {"idx": 0, "level": 0}),
        ("第2章", "另一章内容", {"idx": 1, "level": 0}),
    ]
    chapter_titles = ["第1章", "第2章"]

    fake_snapshots = [
        {
            "keywords": ["A"],
            "core_viewpoint": "观点1",
            "importance": 5,
            "learning_goal": "能理解A",
            "difficulty": "中等",
        },
        {
            "keywords": ["B"],
            "core_viewpoint": "观点2",
            "importance": 3,
            "learning_goal": "能解释B",
            "difficulty": "简单",
        },
    ]

    fake_highlights = {
        "key_points": ["能掌握A", "能区分B"],
        "chapter_priorities": ["第1章"],
        "relationships": "A→B",
        "core_chapter_indices": ["第1章"],
        "chapter_dependencies": {},
    }
    fake_syllabus = [(0, "能掌握A"), (1, "能区分B")]

    async def fake_snapshot(content, title):
        # 根据 title 返回对应 snapshot
        for s in fake_snapshots:
            if s["keywords"] and title in s["keywords"][0]:
                return s
        return fake_snapshots[0]

    async def fake_highlights_fn(course_id, snaps, titles):
        return fake_highlights

    async def fake_syllabus_fn(course_id, chs, snaps):
        return fake_syllabus

    with patch("app.extract_chapter_snapshots_batch", new_callable=AsyncMock) as mock_batch, \
         patch("app.generate_global_highlights", new_callable=AsyncMock) as mock_high, \
         patch("app.generate_speed_read_syllabus", new_callable=AsyncMock, create=True) as mock_syll:
        # mock 批量接口
        async def fake_batch(chs, concurrency=3):
            return {ch_idx: s for (ch_idx, _, _), s in zip(chs, fake_snapshots)}
        mock_batch.side_effect = fake_batch
        mock_high.side_effect = fake_highlights_fn
        mock_syll.side_effect = fake_syllabus_fn

        result = await _run_speed_mode_postprocess(
            "test_course", chapters, chapter_titles,
            source_type="text", source_path="", concurrency=2,
        )

    assert "snapshots" in result
    assert "snapshots_by_idx" in result
    assert "highlights" in result
    assert "syllabus_items" in result

    assert len(result["snapshots"]) == 2
    assert len(result["syllabus_items"]) == 2
    assert result["highlights"]["key_points"] == ["能掌握A", "能区分B"]


@pytest.mark.asyncio
async def test_speed_syllabus_fallback_to_learning_goal(monkeypatch):
    """P1-④：generate_speed_read_syllabus 返回空时，应使用 snapshot.learning_goal 兜底"""
    import app
    from app import _run_speed_mode_postprocess

    chapters = [
        ("第1章", "内容1", {"idx": 0}),
    ]
    chapter_titles = ["第1章"]

    async def fake_batch(chs, concurrency=3):
        return {0: {
            "keywords": ["A"],
            "core_viewpoint": "观点",
            "importance": 3,
            "learning_goal": "能理解A",
            "difficulty": "中等",
        }}

    async def fake_highlights_fn(course_id, snaps, titles):
        return {"key_points": []}   # 关键：highlights 为空

    async def fake_syllabus_fn(course_id, chs, snaps):
        return []   # 关键：syllabus 返回空

    with patch("app.extract_chapter_snapshots_batch", new_callable=AsyncMock) as mock_batch, \
         patch("app.generate_global_highlights", new_callable=AsyncMock) as mock_high, \
         patch("app.generate_speed_read_syllabus", new_callable=AsyncMock, create=True) as mock_syll:
        mock_batch.side_effect = fake_batch
        mock_high.side_effect = fake_highlights_fn
        mock_syll.side_effect = fake_syllabus_fn

        result = await _run_speed_mode_postprocess(
            "test", chapters, chapter_titles, "text", "",
        )

    # 应触发 fallback：使用 learning_goal
    assert len(result["syllabus_items"]) >= 1
    assert result["syllabus_items"][0][1] == "能理解A"


@pytest.mark.asyncio
async def test_speed_syllabus_exception_isolated(monkeypatch):
    """P1-④ 异常隔离：syllabus 函数抛异常不影响整体流程"""
    import app
    from app import _run_speed_mode_postprocess

    chapters = [("第1章", "内容1", {"idx": 0})]
    chapter_titles = ["第1章"]

    async def fake_batch(chs, concurrency=3):
        return {0: {
            "keywords": ["A"],
            "core_viewpoint": "观点",
            "importance": 3,
            "learning_goal": "能理解A",
            "difficulty": "中等",
        }}

    async def fake_highlights_fn(course_id, snaps, titles):
        return {"key_points": []}

    async def fake_syllabus_fn(course_id, chs, snaps):
        raise RuntimeError("模拟 LLM 抛异常")

    with patch("app.extract_chapter_snapshots_batch", new_callable=AsyncMock) as mock_batch, \
         patch("app.generate_global_highlights", new_callable=AsyncMock) as mock_high, \
         patch("app.generate_speed_read_syllabus", new_callable=AsyncMock, create=True) as mock_syll:
        mock_batch.side_effect = fake_batch
        mock_high.side_effect = fake_highlights_fn
        mock_syll.side_effect = fake_syllabus_fn

        result = await _run_speed_mode_postprocess(
            "test", chapters, chapter_titles, "text", "",
        )

    # 异常被捕获，走 fallback
    assert len(result["snapshots"]) == 1
    assert len(result["syllabus_items"]) >= 1
    assert result["syllabus_items"][0][1] == "能理解A"


@pytest.mark.asyncio
async def test_speed_persist_writes_to_db(monkeypatch, tmp_path):
    """P0-①：_persist_speed_results 正确写入 chapter_snapshots / global_highlights / syllabus_items"""
    import os
    import uuid
    import app
    from app import _persist_speed_results

    # 用 uuid 命名 DB 文件，避开 tmp_path 复用 + sqlite WAL 残留
    db_path = str(tmp_path / f"test_{uuid.uuid4().hex[:8]}.db")
    import database
    # 关键: 必须 patch database.DB_PATH 而不是 config.DB_PATH
    # (database.py 顶部 `from config import DB_PATH` 是导入时绑定)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr("config.DB_PATH", db_path)
    database.init_db()

    # 创建 course 以满足外键约束（指定 id 以匹配测试用例）
    _conn = database.get_conn()
    try:
        _conn.execute(
            "INSERT INTO courses (id, title, source_type, source_path, reading_mode) VALUES (?, ?, ?, ?, ?)",
            ("test_course", "测试课程", "text", "", "speed"),
        )
        _conn.execute("INSERT INTO course_profiles (course_id) VALUES (?)", ("test_course",))
        _conn.commit()
    finally:
        _conn.close()

    snapshots_by_idx = {
        0: {"keywords": ["A"], "core_viewpoint": "观点0", "importance": 5, "learning_goal": "目标0", "difficulty": "中等"},
        1: {"keywords": ["B"], "core_viewpoint": "观点1", "importance": 3, "learning_goal": "目标1", "difficulty": "简单"},
    }
    highlights = {
        "key_points": ["KP1", "KP2"],
        "chapter_priorities": ["第1章"],
        "relationships": "A→B",
        "core_chapter_indices": ["第1章"],
        "chapter_dependencies": {},
    }
    syllabus_items = [(0, "目标0"), (1, "目标1")]

    result = {
        "snapshots": list(snapshots_by_idx.values()),
        "snapshots_by_idx": snapshots_by_idx,
        "highlights": highlights,
        "syllabus_items": syllabus_items,
    }

    await _persist_speed_results("test_course", result)

    # 验证：DB 中应有 2 个快照、1 条精华、2 条掌握项
    db_snapshots = database.get_chapter_snapshots("test_course")
    assert len(db_snapshots) == 2
    db_highlights = database.get_global_highlights("test_course")
    assert db_highlights is not None
    assert db_highlights["key_points"] == ["KP1", "KP2"]
    db_syllabus = database.get_syllabus_items("test_course")
    assert len(db_syllabus) == 2

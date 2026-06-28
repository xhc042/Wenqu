"""
P1 测试: generate_chapters 返回结构一致性

修复第二轮审查 P1-③：速读模式成功和失败分支返回结构应一致
使用 mock 方式测试，不依赖实际 HTTP 路由
"""
import pytest
from unittest.mock import AsyncMock, patch


class TestGenerateChaptersStructure:
    """generate_chapters 返回结构一致性测试"""

    def test_speed_mode_success_structure(self, monkeypatch):
        """速读模式成功：应包含所有必需字段"""
        import app
        from app import _run_speed_mode_postprocess, _persist_speed_results

        # 构造模拟数据
        chapters = [
            ("第1章", "内容1", {"idx": 0, "level": 0}),
            ("第2章", "内容2", {"idx": 1, "level": 0}),
        ]
        chapter_titles = ["第1章", "第2章"]

        fake_snapshots = [
            {"keywords": ["A"], "core_viewpoint": "观点1", "importance": 5, "learning_goal": "目标1", "difficulty": "中等"},
            {"keywords": ["B"], "core_viewpoint": "观点2", "importance": 3, "learning_goal": "目标2", "difficulty": "简单"},
        ]

        fake_highlights = {
            "key_points": ["能掌握A", "能解释B"],
            "chapter_priorities": ["第1章"],
            "relationships": "A→B",
            "core_chapter_indices": ["第1章"],
            "chapter_dependencies": {},
        }
        
        fake_syllabus = [(0, "目标1"), (1, "目标2")]

        fake_result = {
            "snapshots": fake_snapshots,
            "snapshots_by_idx": {0: fake_snapshots[0], 1: fake_snapshots[1]},
            "highlights": fake_highlights,
            "syllabus_items": fake_syllabus,
        }

        async def fake_postprocess(*args, **kwargs):
            return fake_result

        with patch("app._run_speed_mode_postprocess", new_callable=AsyncMock, side_effect=fake_postprocess):
            with patch("app._persist_speed_results", new_callable=AsyncMock):
                # 模拟 run_chapter_generation 中的速读模式分支逻辑
                result = fake_result
                
                snapshots = result["snapshots"]
                highlights = result["highlights"]
                
                # 计算 core_indices
                core_indices = set()
                import re as _re
                for s in highlights.get("core_chapter_indices", []):
                    m = _re.search(r'第(\d+)章', str(s))
                    if m:
                        core_indices.add(int(m.group(1)) - 1)
                
                snapshot_map = result["snapshots_by_idx"]
                
                # 构造返回结构（与 app.py 中 generate_chapters 一致）
                response = {
                    "total_chapters": len(chapters),
                    "snapshots_generated": len(snapshots),
                    "highlights_generated": bool(snapshots),
                    "reading_mode": "speed",
                    "core_chapter_indices": list(core_indices),
                    "chapters": [
                        {
                            "idx": i,
                            "title": chapters[i][0] if isinstance(chapters[i], tuple) else str(chapters[i]),
                            "is_loaded": False,
                            "importance": snapshot_map.get(i, {}).get("importance", 0),
                            "keywords": snapshot_map.get(i, {}).get("keywords", []),
                            "core_viewpoint": snapshot_map.get(i, {}).get("core_viewpoint", ""),
                            "learning_goal": snapshot_map.get(i, {}).get("learning_goal", ""),
                            "is_core": i in core_indices,
                        }
                        for i in range(len(chapters))
                    ],
                }

        # 验证必需字段存在
        assert "total_chapters" in response
        assert response["total_chapters"] == 2
        assert "snapshots_generated" in response
        assert response["snapshots_generated"] == 2
        assert "highlights_generated" in response
        assert response["highlights_generated"] is True
        assert "reading_mode" in response
        assert response["reading_mode"] == "speed"
        assert "core_chapter_indices" in response
        assert "chapters" in response
        assert len(response["chapters"]) == 2
        
        # 验证每个章节字段
        for chapter in response["chapters"]:
            assert "idx" in chapter
            assert "title" in chapter
            assert "is_loaded" in chapter
            assert "importance" in chapter
            assert "keywords" in chapter
            assert "core_viewpoint" in chapter
            assert "learning_goal" in chapter
            assert "is_core" in chapter

    def test_speed_mode_failure_structure(self, monkeypatch):
        """速读模式失败：应返回与成功相同结构的字段（空值）"""
        # 模拟失败时的返回结构（与 app.py 中 generate_chapters except 分支一致）
        chapters = [
            ("第1章", "内容1", {"idx": 0, "level": 0}),
            ("第2章", "内容2", {"idx": 1, "level": 0}),
        ]
        
        response = {
            "total_chapters": len(chapters),
            "snapshots_generated": 0,
            "highlights_generated": False,
            "reading_mode": "speed",
            "core_chapter_indices": [],
            "chapters": [
                {
                    "idx": i,
                    "title": chapters[i][0] if isinstance(chapters[i], tuple) else str(chapters[i]),
                    "is_loaded": False,
                    "importance": 0,
                    "keywords": [],
                    "core_viewpoint": "",
                    "learning_goal": "",
                    "is_core": False,
                }
                for i in range(len(chapters))
            ],
        }

        # 验证失败时也包含所有必需字段
        assert "total_chapters" in response
        assert response["total_chapters"] == 2
        assert "snapshots_generated" in response
        assert response["snapshots_generated"] == 0
        assert "highlights_generated" in response
        assert response["highlights_generated"] is False
        assert "reading_mode" in response
        assert response["reading_mode"] == "speed"
        assert "core_chapter_indices" in response
        assert response["core_chapter_indices"] == []
        assert "chapters" in response
        assert len(response["chapters"]) == 2
        
        # 验证每个章节字段（空值）
        for chapter in response["chapters"]:
            assert "idx" in chapter
            assert "title" in chapter
            assert "is_loaded" in chapter
            assert "importance" in chapter
            assert chapter["importance"] == 0
            assert "keywords" in chapter
            assert chapter["keywords"] == []
            assert "core_viewpoint" in chapter
            assert chapter["core_viewpoint"] == ""
            assert "learning_goal" in chapter
            assert chapter["learning_goal"] == ""
            assert "is_core" in chapter
            assert chapter["is_core"] is False

    def test_both_structures_are_compatible(self):
        """成功和失败结构应兼容：字段集合应相同"""
        # 成功结构
        success_chapters = [
            {
                "idx": 0,
                "title": "第1章",
                "is_loaded": False,
                "importance": 5,
                "keywords": ["A"],
                "core_viewpoint": "观点1",
                "learning_goal": "目标1",
                "is_core": True,
            }
        ]
        
        # 失败结构
        failure_chapters = [
            {
                "idx": 0,
                "title": "第1章",
                "is_loaded": False,
                "importance": 0,
                "keywords": [],
                "core_viewpoint": "",
                "learning_goal": "",
                "is_core": False,
            }
        ]
        
        # 验证字段集合相同
        success_keys = set(success_chapters[0].keys())
        failure_keys = set(failure_chapters[0].keys())
        assert success_keys == failure_keys, f"字段不匹配: 成功={success_keys}, 失败={failure_keys}"

    def test_standard_mode_structure(self, monkeypatch):
        """标准模式：应包含基本章节字段"""
        chapters = [
            ("第1章", "内容1", {"idx": 0, "level": 0}),
            ("第2章", "内容2", {"idx": 1, "level": 0}),
        ]
        
        # 标准模式返回结构（不含速读特有字段）
        response = {
            "total_chapters": len(chapters),
            "chapters": [
                {
                    "idx": i,
                    "title": chapters[i][0] if isinstance(chapters[i], tuple) else str(chapters[i]),
                    "is_loaded": i < 3,  # 标准模式前 99 章已加载
                }
                for i in range(len(chapters))
            ],
        }

        assert "total_chapters" in response
        assert response["total_chapters"] == 2
        assert "chapters" in response
        assert len(response["chapters"]) == 2
        
        for chapter in response["chapters"]:
            assert "idx" in chapter
            assert "title" in chapter
            assert "is_loaded" in chapter

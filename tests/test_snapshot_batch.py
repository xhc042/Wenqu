"""
P0-② 测试: extract_chapter_snapshots_batch 并发批量生成
"""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch

from chunker import extract_chapter_snapshots_batch


@pytest.mark.asyncio
async def test_batch_returns_correct_mapping():
    """批量返回 {idx: snapshot} 映射，键与输入一致"""
    chapters = [(i, f"章{i}", f"内容{i}") for i in range(5)]

    async def fake_snap(content, title):
        return {
            "keywords": [title],
            "core_viewpoint": f"观点-{title}",
            "importance": 3,
            "learning_goal": f"目标-{title}",
            "difficulty": "中等",
        }

    with patch("chunker.extract_chapter_snapshot", new_callable=AsyncMock) as mock:
        mock.side_effect = fake_snap
        result = await extract_chapter_snapshots_batch(chapters, concurrency=3)

    assert set(result.keys()) == {0, 1, 2, 3, 4}
    for i in range(5):
        assert result[i]["keywords"] == [f"章{i}"]
        assert result[i]["core_viewpoint"] == f"观点-章{i}"


@pytest.mark.asyncio
async def test_batch_concurrency_speedup():
    """并发度生效：10 章 / concurrency=3 应明显快于串行"""
    chapters = [(i, f"章{i}", f"内容{i}") for i in range(10)]
    delay = 0.2  # 每次 LLM 模拟耗时 200ms

    async def fake_snap(content, title):
        await asyncio.sleep(delay)
        return {"keywords": [], "importance": 3}

    with patch("chunker.extract_chapter_snapshot", new_callable=AsyncMock) as mock:
        mock.side_effect = fake_snap

        start = time.time()
        result = await extract_chapter_snapshots_batch(chapters, concurrency=3)
        elapsed = time.time() - start

    # 串行需要 10 * 0.2 = 2.0s，并发 3 约需 ceil(10/3) * 0.2 = 0.8s
    # 留 30% buffer，应 < 1.2s
    assert elapsed < 1.2, f"并发未生效: {elapsed:.2f}s"
    assert len(result) == 10


@pytest.mark.asyncio
async def test_batch_isolates_failures():
    """单章失败不影响其他章（P0-② 风险缓解：异常隔离）"""
    chapters = [(0, "章0", "内容0"), (1, "章1", "内容1"), (2, "章2", "内容2")]

    call_count = {"n": 0}

    async def fake_snap(content, title):
        call_count["n"] += 1
        if "章1" in title:
            raise RuntimeError("模拟 LLM 失败")
        return {"keywords": [title], "importance": 3}

    with patch("chunker.extract_chapter_snapshot", new_callable=AsyncMock) as mock:
        mock.side_effect = fake_snap
        result = await extract_chapter_snapshots_batch(chapters, concurrency=2)

    # 章0 和 章2 应成功
    assert 0 in result
    assert 2 in result
    # 章1 失败被隔离
    assert 1 not in result
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_batch_empty_input():
    """空输入返回空 dict，不报错"""
    with patch("chunker.extract_chapter_snapshot", new_callable=AsyncMock):
        result = await extract_chapter_snapshots_batch([], concurrency=3)
    assert result == {}

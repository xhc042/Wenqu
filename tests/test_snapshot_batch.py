"""
P0-② 测试: extract_chapter_snapshots_batch 并发批量生成
"""
import asyncio
import time
import inspect
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
@pytest.mark.asyncio
async def test_batch_progress_callback_per_completion():
    """
    验证：progress_callback 每完成一章调用一次，参数为 (current, total, title)
    精度为"每批完成"（as_completed 实现）
    """
    chapters = [(i, f"第{i+1}章", f"内容{i}") for i in range(4)]
    calls = []

    def progress_cb(current, total, title):
        calls.append((current, total, title))

    async def fake_snap(content, title):
        return {"keywords": [title], "importance": 3}

    with patch("chunker.extract_chapter_snapshot", new_callable=AsyncMock) as mock:
        mock.side_effect = fake_snap
        result = await extract_chapter_snapshots_batch(
            chapters, concurrency=2, progress_callback=progress_cb,
        )

    assert len(result) == 4
    assert len(calls) == 4
    # 验证 total 始终为 4
    assert all(calls[i][1] == 4 for i in range(4))
    # 验证 current 从 1 递增到 4（as_completed 不保证顺序，用集合验证）
    assert {calls[i][0] for i in range(4)} == {1, 2, 3, 4}
    # 验证所有章节都被触发（title 不一定按顺序）
    assert {call[2] for call in calls} == {"第1章", "第2章", "第3章", "第4章"}


@pytest.mark.asyncio
async def test_batch_progress_callback_on_failure():
    """
    验证：某章失败时，callback 仍被调用（current 计入该章）
    """
    chapters = [(0, "章0", "内容0"), (1, "章1", "内容1"), (2, "章2", "内容2")]
    calls = []

    def progress_cb(current, total, title):
        calls.append((current, total, title))

    async def fake_snap(content, title):
        if "章1" in title:
            raise RuntimeError("LLM 失败")
        return {"keywords": [title], "importance": 3}

    with patch("chunker.extract_chapter_snapshot", new_callable=AsyncMock) as mock:
        mock.side_effect = fake_snap
        result = await extract_chapter_snapshots_batch(
            chapters, concurrency=2, progress_callback=progress_cb,
        )

    # 章1 失败，但 callback 仍被调用（3次，对应3个任务）
    assert len(calls) == 3
    # current 仍从 1 递增（失败的任务也算完成）
    assert calls[0][0] == 1
    assert calls[1][0] == 2
    assert calls[2][0] == 3


# ==================== v1.x: 默认并发 = 2 ====================


def test_batch_default_concurrency_is_2():
    """默认并发从 3 改为 2（业务诉求：避免瞬时高并发打 LLM 配额）"""
    sig = inspect.signature(extract_chapter_snapshots_batch)
    assert sig.parameters["concurrency"].default == 2, (
        f"默认并发应为 2，实际 {sig.parameters['concurrency'].default}"
    )


def test_postprocess_default_concurrency_is_2():
    """_run_speed_mode_postprocess 默认并发也是 2（保持一致）"""
    from app import _run_speed_mode_postprocess
    sig = inspect.signature(_run_speed_mode_postprocess)
    assert sig.parameters["concurrency"].default == 2


@pytest.mark.asyncio
async def test_batch_default_concurrency_runtime_is_2():
    """
    运行时验证：不传 concurrency 时，并发上限确实为 2
    通过 Semaphore 的 _value 间接观测（注入并发计数 fake）
    """
    chapters = [(i, f"章{i}", f"内容{i}") for i in range(6)]
    in_flight = {"max": 0, "cur": 0}

    async def fake_snap(content, title):
        in_flight["cur"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["cur"])
        await asyncio.sleep(0.05)
        in_flight["cur"] -= 1
        return {"keywords": [title], "importance": 3}

    with patch("chunker.extract_chapter_snapshot", new_callable=AsyncMock) as mock:
        mock.side_effect = fake_snap
        # 不传 concurrency → 使用默认值
        result = await extract_chapter_snapshots_batch(chapters)

    assert len(result) == 6
    assert in_flight["max"] <= 2, (
        f"默认 concurrency 应为 2，但实测瞬时并发={in_flight['max']}"
    )
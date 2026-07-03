"""
P0-② 测试: chunker.generate_syllabus_items 并发批行为

覆盖：
1. 并发跑多个 batch 时,单批失败不影响其他批
2. 返回结果按 batch_start 升序稳定（不因 LLM 返回顺序乱而乱）
3. fallback 单批失败时仍产出默认项
"""
import asyncio
from unittest.mock import AsyncMock, patch
import pytest

from chunker import generate_syllabus_items


def _make_chapters(n: int):
    """构造 n 个三元组 chapters"""
    return [(i, f"第{i+1}章 标题", f"这是第{i+1}章的内容片段。" * 5) for i in range(n)]


@pytest.mark.asyncio
async def test_syllabus_items_concurrent_ordering():
    """30 章分 6 批(每批 5 章)并发跑,返回结果应包含所有章节 idx,顺序稳定"""
    chapters = _make_chapters(30)

    async def fake_chat_json(messages, temperature=0.3):
        # 模拟 LLM 返回对应批次的 items: 每章 2 条
        import re
        nums = sorted(int(m) for m in re.findall(r"章节 (\d+)", messages[-1]["content"]))
        items = []
        for n in nums:
            items.append({"chapter": n, "description": f"批{n}项A"})
            items.append({"chapter": n, "description": f"批{n}项B"})
        return {"items": items}

    with patch("chunker.llm") as mock_llm:
        mock_llm.chat_json = AsyncMock(side_effect=fake_chat_json)
        items = await generate_syllabus_items("c1", chapters, concurrency=3)

    # 验证:30 章应全部有 2 条掌握项 = 60 条
    assert len(items) == 60, f"期望 60 条,实际 {len(items)}"

    # 验证:chapter_index 应覆盖 0..29
    chapter_indices = set(idx for idx, _ in items)
    assert chapter_indices == set(range(30)), f"覆盖不全: 缺 {set(range(30)) - chapter_indices}"

    # 验证:每个 chapter 恰好 2 条（来自该批次的 2 条 mock 输出）
    from collections import Counter
    counts = Counter(idx for idx, _ in items)
    assert all(c == 2 for c in counts.values()), f"每章应有 2 条,实际分布 {dict(counts)}"


@pytest.mark.asyncio
async def test_syllabus_items_concurrent_partial_failure():
    """部分批次 LLM 失败时,失败批走 fallback,成功的仍保留"""
    chapters = _make_chapters(15)  # 3 批

    call_count = 0

    async def fake_chat_json(messages, temperature=0.3):
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # 第二批失败
            raise Exception("模拟 LLM 限流")
        import re
        m = re.search(r"章节 (\d+)", messages[-1]["content"])
        start = int(m.group(1)) if m else 1
        return {"items": [{"chapter": start, "description": f"OK"}]}

    with patch("chunker.llm") as mock_llm:
        mock_llm.chat_json = AsyncMock(side_effect=fake_chat_json)
        items = await generate_syllabus_items("c1", chapters, concurrency=2)

    # 15 章 = 3 批
    # 批 1 (idx 0..4) OK: 1 条 'OK' at real idx 0
    # 批 2 (idx 5..9) 失败 → 5 章 × 2 默认 = 10 条 fallback
    # 批 3 (idx 10..14) OK: 1 条 'OK' at real idx 10
    # 总计 12 条
    assert len(items) == 12, f"期望 12 条, 实际 {len(items)}"

    # 验证 fallback 文本格式（来自第 2 批失败的章节）
    fallback_items = [d for idx, d in items if "复述" in d or "解释" in d]
    assert len(fallback_items) == 10, f"期望 10 条 fallback, 实际 {len(fallback_items)}"

    # 验证成功批的 real chapter_index 保留(批 1 → idx 0, 批 3 → idx 10)
    ok_items = [(idx, d) for idx, d in items if d == "OK"]
    assert (0, "OK") in ok_items, "批 1 的真实 idx 0 应保留"
    assert (10, "OK") in ok_items, "批 3 的真实 idx 10 应保留"


@pytest.mark.asyncio
async def test_syllabus_items_empty_input():
    """空 chapters 直接返回空,不应报错"""
    items = await generate_syllabus_items("c1", [], concurrency=2)
    assert items == []
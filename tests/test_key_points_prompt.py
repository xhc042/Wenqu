"""
v1.x 测试: 核心知识点 prompt 改造

覆盖三项改动:
1. key_points prompt 不再含 "25 字" 字数限制（金融/医学等领域实体名长）
2. key_points prompt 强化"动宾短语"描述规则
3. _generate_fallback_key_points 不再截断 viewpoint
"""
import inspect
import pytest
from unittest.mock import AsyncMock, patch


def _make_fake_llm(captured):
    """构造一个能捕获 prompt 的 fake llm 实例"""

    class FakeLLM:
        async def chat_json(self, messages, temperature=None):
            captured["user"] = messages[1]["content"]
            return {
                "key_points": ["能列出X"],
                "chapter_priorities": [],
                "chapter_dependencies": {},
                "core_chapter_indices": [],
                "relationships": "",
            }

    return FakeLLM()


@pytest.mark.asyncio
async def test_key_points_prompt_no_25_char_limit_in_5to10_branch():
    """5-10 个分支的核心知识点 prompt 必须不再含 "25 字" 字数限制"""
    import chunker
    captured = {}

    with patch.object(chunker, "llm", _make_fake_llm(captured)):
        snapshots = [
            {"keywords": ["A"], "core_viewpoint": "vA", "importance": 3},
            {"keywords": ["B"], "core_viewpoint": "vB", "importance": 3},
            {"keywords": ["C"], "core_viewpoint": "vC", "importance": 3},
        ]
        await chunker.generate_global_highlights(
            "c1", snapshots, ["第1章", "第2章", "第3章"]
        )

    prompt = captured["user"]
    assert "25字" not in prompt and "25 字" not in prompt, (
        f"5-10 分支 prompt 不应再含 25 字限制，实际片段: {prompt[:300]}"
    )


@pytest.mark.asyncio
async def test_key_points_prompt_no_25_char_limit_in_3to8_branch():
    """3-8 个分支的简化 prompt 也必须去掉 25 字限制"""
    import chunker
    captured = {}

    with patch.object(chunker, "llm", _make_fake_llm(captured)):
        # < 3 个 snapshot → 触发 3-8 分支
        snapshots = [
            {"keywords": ["A"], "core_viewpoint": "vA", "importance": 3},
        ]
        await chunker.generate_global_highlights(
            "c1", snapshots, ["第1章"]
        )

    prompt = captured["user"]
    assert "25字" not in prompt and "25 字" not in prompt


@pytest.mark.asyncio
async def test_key_points_prompt_emphasizes_verb_object_phrase():
    """prompt 必须显式包含"动宾短语"规则"""
    import chunker
    captured = {}

    with patch.object(chunker, "llm", _make_fake_llm(captured)):
        snapshots = [
            {"keywords": ["A"], "core_viewpoint": "vA", "importance": 3},
            {"keywords": ["B"], "core_viewpoint": "vB", "importance": 3},
            {"keywords": ["C"], "core_viewpoint": "vC", "importance": 3},
        ]
        await chunker.generate_global_highlights(
            "c1", snapshots, ["第1章", "第2章", "第3章"]
        )

    prompt = captured["user"]
    assert "动宾短语" in prompt, f"prompt 应包含动宾短语规则，实际片段: {prompt[:300]}"
    # v1.3 P0-② 引入的好/坏例子应当保留（避免回退）
    assert "能列出ETF" in prompt or "主动型与被动型" in prompt


def test_fallback_source_no_longer_truncates_viewpoint():
    """_generate_fallback_key_points 源码不应再 [:30] 截断 viewpoint"""
    from chunker import _generate_fallback_key_points
    src = inspect.getsource(_generate_fallback_key_points)
    assert "viewpoint[:30]" not in src, (
        "fallback 不应再截断 viewpoint；实体并列场景会丢失信息"
    )


def test_fallback_long_viewpoint_kept_intact():
    """
    fallback 兜底链路：长 viewpoint（金融指数、医学术语并列场景）应完整保留
    用户案例："能理解而标普500指数、道琼斯工业指数（DJIA）、纳斯达克指数（…"
    """
    long_vp = (
        "标普500指数（S&P 500）、道琼斯工业指数（DJIA）、"
        "纳斯达克综合指数（NASDAQ Composite）三大美股指数的编制方法、"
        "覆盖范围与权重计算方式"
    )
    snapshots = [
        {"keywords": [], "core_viewpoint": long_vp, "importance": 5},
    ]
    titles = ["第1章"]

    from chunker import _generate_fallback_key_points
    points = _generate_fallback_key_points(snapshots, titles, max_points=5)

    viewpoint_points = [p for p in points if p.startswith("能理解")]
    assert viewpoint_points, "应至少生成一条 viewpoint 兜底知识点"

    point = viewpoint_points[0]
    # 完整 viewpoint（不截断）必须出现
    assert "纳斯达克综合指数" in point, (
        f"viewpoint 被截断，丢失实体名。实际: {point!r}"
    )
    assert "权重计算方式" in point, (
        f"viewpoint 末尾被截断，丢失补语。实际: {point!r}"
    )

def test_build_highlights_fallback_no_truncation():
    """
    _build_highlights_fallback 是 generate_global_highlights 三次 LLM 重试全失败后的
    终极兜底路径（与 _generate_fallback_key_points 是两条独立路径）。

    用户反馈的金融指数并列 case 在 LLM 完全失败场景下若被 vp[:30] 截断，仍会丢信息。
    回归测试：vp 必须完整保留。
    """
    long_vp = (
        "标普500指数（S&P 500）、道琼斯工业指数（DJIA）、"
        "纳斯达克综合指数（NASDAQ Composite）三大美股指数的编制方法、"
        "覆盖范围与权重计算方式"
    )
    snapshots = [
        {"keywords": [], "core_viewpoint": long_vp, "importance": 5},
    ]
    titles = ["第1章"]

    from chunker import _build_highlights_fallback
    result = _build_highlights_fallback(snapshots, titles)

    key_points = result["key_points"]
    assert key_points, "_build_highlights_fallback 应返回至少一条 key_point"

    viewpoint_points = [p for p in key_points if p.startswith("能理解")]
    assert viewpoint_points, "应至少生成一条基于 vp 的兜底知识点"

    point = viewpoint_points[0]
    assert "纳斯达克综合指数" in point, (
        f"vp 被 [:30] 截断，丢失实体名。实际: {point!r}"
    )
    assert "权重计算方式" in point, (
        f"vp 末尾被截断，丢失补语。实际: {point!r}"
    )


def test_build_highlights_fallback_source_no_truncation():
    """_build_highlights_fallback 源码中不应再含 vp[:30]"""
    import inspect
    from chunker import _build_highlights_fallback
    src = inspect.getsource(_build_highlights_fallback)
    assert "vp[:30]" not in src, (
        "_build_highlights_fallback 不应再截断 vp；与 _generate_fallback_key_points 同源要求"
    )

"""
chunker.generate_syllabus_items 与 syllabus 落库一致性测试

回归 bug: ETF 这类书的 chapters.idx 是非连续的（如 4~15），
但 syllabus_items 的 chapter_index 被错误地写成了 enumerate 顺序 idx（0~N），
导致对话状态机在 PROBE 阶段取不到当前章节的 syllabus，
不同课程的对话题目变得相似/重复。

覆盖：
1. generate_syllabus_items 接三元组 (idx, title, content)：chapter_index 用真实 idx
2. generate_syllabus_items 接二元组 (title, content)（向后兼容）：用 enumerate idx
3. generate_syllabus_items 异常 fallback：用真实 idx
4. _add_default_syllabus_items 接三元组：用真实 idx
5. api_load_chapter_content 反复加载同一章节：旧 syllabus 被清掉，不累积
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from chunker import generate_syllabus_items


def _run(coro):
    """方便把 async 测试写成同步风格"""
    return asyncio.run(coro)


# ==================== generate_syllabus_items 单元测试 ====================

@pytest.mark.asyncio
async def test_generate_syllabus_items_uses_real_chapter_index_for_3tuple():
    """三元组 (idx, title, content) → 输出用真实 idx，不被 enumerate 覆盖"""
    # chapters 表的真实 idx 是 4~7（4 章，非连续 0~3）
    chapters = [
        (4, "第四章 ETF基础", "内容 A"),
        (5, "第五章 十年十倍", "内容 B"),
        (6, "第六章 交易机制", "内容 C"),
        (7, "第七章 交易难点", "内容 D"),
    ]

    # 让 LLM 返回每章 2 条 items（chapter 编号是 batch 内 1-based 顺序）
    async def fake_chat_json(messages, *args, **kwargs):
        return {
            "items": [
                {"chapter": 1, "description": "能复述第四章"},
                {"chapter": 1, "description": "能解释第四章"},
                {"chapter": 2, "description": "能复述第五章"},
                {"chapter": 3, "description": "能复述第六章"},
                {"chapter": 4, "description": "能复述第七章"},
                {"chapter": 4, "description": "能解释第七章"},
            ]
        }

    with patch("chunker.llm") as mock_llm:
        mock_llm.chat_json = AsyncMock(side_effect=fake_chat_json)
        items = await generate_syllabus_items("c1", chapters)

    # 所有 chapter_index 必须是真实 idx（4/5/6/7），不能是 enumerate 0~3
    actual_idx_list = sorted({idx for idx, _ in items})
    assert actual_idx_list == [4, 5, 6, 7], f"应保留真实 idx，但实际: {actual_idx_list}"

    # 每条 item 落到对应真实 idx 上
    by_idx = {}
    for idx, desc in items:
        by_idx.setdefault(idx, []).append(desc)
    assert len(by_idx[4]) == 2  # 第 4 章 2 条
    assert len(by_idx[5]) == 1  # 第 5 章 1 条
    assert len(by_idx[6]) == 1  # 第 6 章 1 条
    assert len(by_idx[7]) == 2  # 第 7 章 2 条
    assert "能复述第四章" in by_idx[4]
    assert "能复述第七章" in by_idx[7]


@pytest.mark.asyncio
async def test_generate_syllabus_items_2tuple_uses_enumerate_index():
    """二元组 (title, content)（向后兼容）：行为保持旧版——用 enumerate idx"""
    chapters = [
        ("第四章 ETF基础", "内容 A"),
        ("第五章 十年十倍", "内容 B"),
        ("第六章 交易机制", "内容 C"),
    ]

    async def fake_chat_json(messages, *args, **kwargs):
        return {
            "items": [
                {"chapter": 1, "description": "能复述第四章"},
                {"chapter": 2, "description": "能复述第五章"},
                {"chapter": 3, "description": "能复述第六章"},
            ]
        }

    with patch("chunker.llm") as mock_llm:
        mock_llm.chat_json = AsyncMock(side_effect=fake_chat_json)
        items = await generate_syllabus_items("c1", chapters)

    # 向后兼容：二元组走 enumerate 顺序 idx（0/1/2）
    actual_idx_list = sorted({idx for idx, _ in items})
    assert actual_idx_list == [0, 1, 2], f"二元组应走 enumerate idx，但实际: {actual_idx_list}"


@pytest.mark.asyncio
async def test_generate_syllabus_items_fallback_uses_real_chapter_index():
    """LLM 异常 → fallback 默认项也应写真实 idx（用三元组时）"""
    chapters = [
        (4, "第四章 ETF基础", "内容 A"),
        (5, "第五章 十年十倍", "内容 B"),
    ]

    async def broken_chat_json(messages, *args, **kwargs):
        raise RuntimeError("LLM 罢工了")

    with patch("chunker.llm") as mock_llm:
        mock_llm.chat_json = AsyncMock(side_effect=broken_chat_json)
        items = await generate_syllabus_items("c1", chapters)

    # fallback 时也必须用真实 idx（4/5），不能是 0/1
    actual_idx_list = sorted({idx for idx, _ in items})
    assert actual_idx_list == [4, 5], f"Fallback 应保留真实 idx，但实际: {actual_idx_list}"

    # fallback 每章 2 条默认项
    assert len(items) == 4


@pytest.mark.asyncio
async def test_generate_syllabus_items_dict_format_uses_idx_field():
    """dict 格式元素：优先用 dict 里的 idx 字段"""
    chapters = [
        {"idx": 10, "title": "第十章", "content": "..."},
        {"idx": 11, "title": "第十一章", "content": "..."},
    ]

    async def fake_chat_json(messages, *args, **kwargs):
        return {
            "items": [
                {"chapter": 1, "description": "A"},
                {"chapter": 2, "description": "B"},
            ]
        }

    with patch("chunker.llm") as mock_llm:
        mock_llm.chat_json = AsyncMock(side_effect=fake_chat_json)
        items = await generate_syllabus_items("c1", chapters)

    actual_idx_list = sorted({idx for idx, _ in items})
    assert actual_idx_list == [10, 11]


# ==================== 落库行为测试（用临时 DB） ====================

@pytest.mark.asyncio
async def test_api_load_chapter_content_writes_correct_chapter_index():
    """api_load_chapter_content 单章加载：syllabus 写到真实 chapter_index，不写 ch 0"""
    import database as db
    db.init_db()
    from routes.course_routes import api_load_chapter_content

    course_id = db.create_course("测试课", "text", "", "standard")

    # chapters 表存的是 idx=10 的章节（模拟 EPUB idx 偏移）
    db.add_chapter(
        course_id=course_id,
        idx=10,
        title="第十章 真·ETF 章节",
        content_slice="短摘要",
        summary="",
        content_full="这是第十章的全文内容，描述 ETF 的核心机制。",
        is_loaded=0,
    )

    # mock LLM 返回 chapter=1（batch 内顺序）→ 应映射到真实 idx=10
    async def fake_chat_json(messages, *args, **kwargs):
        return {
            "items": [
                {"chapter": 1, "description": "能复述 ETF"},
                {"chapter": 1, "description": "能解释套利"},
            ]
        }

    with patch("chunker.llm") as mock_llm:
        mock_llm.chat_json = AsyncMock(side_effect=fake_chat_json)
        result = await api_load_chapter_content(course_id, 10)

    # 核心断言：syllabus 必须落在 chapter_index=10，不是 0
    items = db.get_syllabus_items(course_id)
    assert len(items) == 2
    chapter_indices = {it["chapter_index"] for it in items}
    assert chapter_indices == {10}, f"应全在 ch=10，实际: {chapter_indices}"
    descriptions = {it["description"] for it in items}
    assert descriptions == {"能复述 ETF", "能解释套利"}


@pytest.mark.asyncio
async def test_api_load_chapter_content_repeated_load_does_not_accumulate():
    """反复加载同一章节：旧 syllabus 会被清掉，不会越积越多"""
    import database as db
    db.init_db()
    from routes.course_routes import api_load_chapter_content

    course_id = db.create_course("测试课", "text", "", "standard")
    db.add_chapter(
        course_id=course_id, idx=10, title="第十章",
        content_slice="...", summary="",
        content_full="全文内容", is_loaded=0,
    )

    # 第一次加载
    async def fake_first(messages, *args, **kwargs):
        return {"items": [
            {"chapter": 1, "description": "能A"},
            {"chapter": 1, "description": "能B"},
        ]}

    with patch("chunker.llm") as mock_llm:
        mock_llm.chat_json = AsyncMock(side_effect=fake_first)
        await api_load_chapter_content(course_id, 10)

    assert len(db.get_syllabus_items(course_id)) == 2

    # 第二次加载前把 is_loaded 重置为 0，强制 api_load_chapter_content 走完整流程
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE chapters SET is_loaded=0 WHERE course_id=? AND idx=?",
            (course_id, 10),
        )
        conn.commit()
    finally:
        conn.close()

    # 第二次加载（LLM 返回不同的描述）
    async def fake_second(messages, *args, **kwargs):
        return {"items": [
            {"chapter": 1, "description": "能C"},
            {"chapter": 1, "description": "能D"},
            {"chapter": 1, "description": "能E"},
        ]}

    with patch("chunker.llm") as mock_llm:
        mock_llm.chat_json = AsyncMock(side_effect=fake_second)
        await api_load_chapter_content(course_id, 10)

    # 关键：第二次加载应该清掉旧的 2 条，只剩新生成的 3 条
    items = db.get_syllabus_items(course_id)
    assert len(items) == 3, f"应该只剩 3 条，实际 {len(items)} 条（被累积了）"
    descriptions = {it["description"] for it in items}
    assert descriptions == {"能C", "能D", "能E"}
    # 而且都在 ch=10
    assert {it["chapter_index"] for it in items} == {10}


@pytest.mark.asyncio
async def test_api_load_chapter_content_does_not_touch_other_chapters():
    """加载 ch=10 不应影响 ch=11 已有的 syllabus"""
    import database as db
    db.init_db()
    from routes.course_routes import api_load_chapter_content

    course_id = db.create_course("测试课", "text", "", "standard")
    db.add_chapter(
        course_id=course_id, idx=10, title="第十章",
        content_slice="...", summary="",
        content_full="第十章全文", is_loaded=0,
    )
    db.add_chapter(
        course_id=course_id, idx=11, title="第十一章",
        content_slice="...", summary="",
        content_full="第十一章全文", is_loaded=0,
    )
    # 先手动给 ch=11 加一条 syllabus
    db.add_syllabus_item(course_id, 11, "ch11 的旧 syllabus")
    db.add_syllabus_item(course_id, 11, "ch11 的另一条")

    async def fake_chat_json(messages, *args, **kwargs):
        return {"items": [{"chapter": 1, "description": "ch10 新 syllabus"}]}

    with patch("chunker.llm") as mock_llm:
        mock_llm.chat_json = AsyncMock(side_effect=fake_chat_json)
        await api_load_chapter_content(course_id, 10)

    items = db.get_syllabus_items(course_id)
    by_ch = {}
    for it in items:
        by_ch.setdefault(it["chapter_index"], []).append(it["description"])

    # ch=11 的 2 条要原封不动
    assert sorted(by_ch.get(11, [])) == ["ch11 的另一条", "ch11 的旧 syllabus"], \
        f"ch=11 不应被影响，实际: {by_ch.get(11, [])}"
    # ch=10 是新生成的那一条
    assert by_ch.get(10, []) == ["ch10 新 syllabus"]


@pytest.mark.asyncio
async def test_api_load_chapter_content_speed_mode_does_not_generate_syllabus():
    """速读模式:加载章节不应调用 generate_syllabus_items,避免破坏
    '_run_speed_mode_postprocess 集中按精华 20% 选取' 的设计,导致 syllabus 持续累积。

    复现:速读模式分章后,核心 5 章有 syllabus。用户点'推荐学习'章节(无 syllabus)
    → 触发 api_load_chapter_content → 不应再为该章节生成新 syllabus
    """
    import database as db
    db.init_db()
    from routes.course_routes import api_load_chapter_content

    # 速读模式分章完成
    course_id = db.create_course("速读测试课", "text", "", "speed")

    # 核心 5 章(由 _run_speed_mode_postprocess 集中生成 syllabus)
    for idx in range(5):
        db.add_chapter(
            course_id=course_id, idx=idx, title=f"核心章节{idx}",
            content_slice="...", summary="",
            content_full=f"核心章节{idx}全文", is_loaded=0,
        )
        db.add_syllabus_item(course_id, idx, f"核心{idx}的精华")

    # "推荐学习"章节(无 syllabus,但要能加载正文)
    db.add_chapter(
        course_id=course_id, idx=10, title="非核心章节",
        content_slice="...", summary="",
        content_full="非核心章节全文", is_loaded=0,
    )

    # 关键断言 1:加载前,ch=10 没有 syllabus(推荐学习状态)
    items_before = db.get_syllabus_items(course_id)
    chapters_with_syllabus_before = {it["chapter_index"] for it in items_before}
    assert 10 not in chapters_with_syllabus_before, \
        "测试前置条件:ch=10 速读模式下不应该有 syllabus"
    assert len(items_before) == 5, f"速读初始化应有 5 条核心 syllabus,实际 {len(items_before)}"

    # mock LLM,如果被调用就报错
    async def should_not_be_called(messages, *args, **kwargs):
        raise AssertionError("速读模式下不应调用 LLM 生成 syllabus!")

    with patch("chunker.llm") as mock_llm:
        mock_llm.chat_json = AsyncMock(side_effect=should_not_be_called)
        result = await api_load_chapter_content(course_id, 10)

    # 关键断言 2:加载后,ch=10 仍然没有 syllabus(没被偷塞)
    items_after = db.get_syllabus_items(course_id)
    chapters_with_syllabus_after = {it["chapter_index"] for it in items_after}
    assert 10 not in chapters_with_syllabus_after, \
        f"速读模式加载章节后,ch=10 不应被自动加 syllabus,实际章节: {chapters_with_syllabus_after}"
    # 总数仍是 5(没膨胀)
    assert len(items_after) == 5, \
        f"速读模式加载章节不应增加 syllabus,实际从 5 变成 {len(items_after)}"
    # 关键断言 3:加载仍然成功(返回 loaded 状态,is_loaded=1)
    assert result["status"] == "loaded", f"应返回 loaded 状态,实际 {result['status']}"
    assert result["chapter"]["is_loaded"] == 1
    # 关键断言 4:LLM 确实没被调用
    mock_llm.chat_json.assert_not_called()


@pytest.mark.asyncio
async def test_api_load_chapter_content_standard_mode_still_generates_syllabus():
    """回归保护:标准模式加载章节仍应正常生成 syllabus(防止误改影响标准模式)"""
    import database as db
    db.init_db()
    from routes.course_routes import api_load_chapter_content

    course_id = db.create_course("标准测试课", "text", "", "standard")
    db.add_chapter(
        course_id=course_id, idx=10, title="第十章",
        content_slice="...", summary="",
        content_full="第十章全文", is_loaded=0,
    )

    async def fake_chat_json(messages, *args, **kwargs):
        return {"items": [
            {"chapter": 1, "description": "标准A"},
            {"chapter": 1, "description": "标准B"},
        ]}

    with patch("chunker.llm") as mock_llm:
        mock_llm.chat_json = AsyncMock(side_effect=fake_chat_json)
        await api_load_chapter_content(course_id, 10)

    items = db.get_syllabus_items(course_id)
    assert len(items) == 2
    assert {it["chapter_index"] for it in items} == {10}


# ==================== _add_default_syllabus_items 测试 ====================

@pytest.mark.asyncio
async def test_add_default_syllabus_items_uses_real_chapter_index():
    """_add_default_syllabus_items 接三元组：用真实 chapter_index"""
    import database as db
    db.init_db()
    from routes.course_routes import _add_default_syllabus_items

    course_id = db.create_course("测试课", "text", "", "standard")

    # 三元组：(idx=10, title, content)
    _add_default_syllabus_items(course_id, [
        (10, "第十章", "内容1"),
        (11, "第十一章", "内容2"),
    ])

    items = db.get_syllabus_items(course_id)
    # 2 章 × 3 默认项 = 6 条
    assert len(items) == 6

    # 必须落在真实 idx（10 / 11），不能是 enumerate（0 / 1）
    by_ch = {}
    for it in items:
        by_ch.setdefault(it["chapter_index"], 0)
        by_ch[it["chapter_index"]] += 1

    assert by_ch == {10: 3, 11: 3}, f"默认项应落到真实 idx，实际: {by_ch}"
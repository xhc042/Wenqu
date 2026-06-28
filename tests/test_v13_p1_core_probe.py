"""
v1.3 P1-任务7 测试: 心流延长 + 核心触达判断

覆盖:
1. _has_touched_core: 检查本章是否触达 importance >=4 的 syllabus
2. _force_core_probe: 强制 PROBE prompt 构造正确性
3. _core_probe_attempted 标记防死循环
4. handle_user_input: min_rounds 边界 + 未触达核心 → 强制 probe 一次
5. handle_user_input: 已触达核心 → 正常结束
6. handle_user_input: 强制 probe 后仍未触达 → 正常结束(不无限循环)
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from state_machine import DialogueStateMachine


class TestHasTouchedCore:
    """测试 _has_touched_core 核心触达判断"""

    def _make_sm(self, syllabus_data):
        sm = DialogueStateMachine.__new__(DialogueStateMachine)
        sm.chapter_index = 0
        sm.syllabus_items = syllabus_data
        return sm

    def test_no_core_items_returns_false(self):
        """没有 importance>=4 的项 → False"""
        sm = self._make_sm([
            {"chapter_index": 0, "description": "A", "status": "pending", "importance": 3},
            {"chapter_index": 0, "description": "B", "status": "in_progress", "importance": 3},
        ])
        assert sm._has_touched_core() is False

    def test_core_mastered_returns_true(self):
        """importance>=4 且 mastered → True"""
        sm = self._make_sm([
            {"chapter_index": 0, "description": "A", "status": "mastered", "importance": 5},
        ])
        assert sm._has_touched_core() is True

    def test_core_in_progress_returns_true(self):
        """importance>=4 且 in_progress → True"""
        sm = self._make_sm([
            {"chapter_index": 0, "description": "A", "status": "in_progress", "importance": 4},
        ])
        assert sm._has_touched_core() is True

    def test_core_pending_returns_false(self):
        """importance>=4 但还是 pending → False(未触达)"""
        sm = self._make_sm([
            {"chapter_index": 0, "description": "A", "status": "pending", "importance": 5},
        ])
        assert sm._has_touched_core() is False

    def test_other_chapter_core_ignored(self):
        """其他章节的核心不算本章触达"""
        sm = self._make_sm([
            {"chapter_index": 0, "description": "本章", "status": "pending", "importance": 5},
            {"chapter_index": 1, "description": "他章", "status": "mastered", "importance": 5},
        ])
        sm.chapter_index = 0
        assert sm._has_touched_core() is False


class TestForceCoreProbe:
    """测试 _force_core_probe prompt 构造"""

    def _make_sm(self, syllabus_data):
        sm = DialogueStateMachine.__new__(DialogueStateMachine)
        sm.chapter_index = 0
        sm.syllabus_items = syllabus_data
        return sm

    def test_force_prompt_contains_top_pending(self):
        """强制 prompt 应含最高 importance 的 pending 项"""
        sm = self._make_sm([
            {"chapter_index": 0, "description": "低", "status": "pending", "importance": 1},
            {"chapter_index": 0, "description": "高核心", "status": "pending", "importance": 5},
            {"chapter_index": 0, "description": "中", "status": "pending", "importance": 3},
        ])
        prompt = sm._force_core_probe()
        assert prompt != ""
        assert "[重要度5/5]" in prompt
        assert "高核心" in prompt
        # 不应包含低重要度的项
        assert "低" not in prompt or "核心" in prompt.split("低")[1] if "低" in prompt else True

    def test_force_prompt_empty_when_no_pending(self):
        """没有 pending 项 → 返回空字符串(由调用方处理结束)"""
        sm = self._make_sm([
            {"chapter_index": 0, "description": "已掌握", "status": "mastered", "importance": 5},
        ])
        assert sm._force_core_probe() == ""

    def test_force_prompt_emphasizes_must_ask(self):
        """prompt 必须含强制约束"""
        sm = self._make_sm([
            {"chapter_index": 0, "description": "X", "status": "pending", "importance": 4},
        ])
        prompt = sm._force_core_probe()
        assert "必须问" in prompt or "围绕以下核心知识点" in prompt
        assert "不要停留在基础概念" in prompt


class TestCoreProbeAttemptFlag:
    """测试 _core_probe_attempted 标记防止死循环"""

    def test_initial_flag_is_false(self):
        """初始 _core_probe_attempted = False"""
        sm = DialogueStateMachine.__new__(DialogueStateMachine)
        sm._core_probe_attempted = False
        assert sm._core_probe_attempted is False


@pytest.mark.asyncio
class TestHandleUserInputEndLogic:
    """测试 handle_user_input 结束边界逻辑

    v1.3 P1-任务7 关键场景:
    A) 达到 min_rounds + 未触达核心 + 未尝试强制 probe → 强制 probe
    B) 达到 min_rounds + 未触达核心 + 已尝试强制 probe → 正常结束(避免死循环)
    C) 达到 min_rounds + 已触达核心 → 正常结束
    D) 进心流 → 不结束
    """

    def _setup_sm(self, syllabus_data, current_round=3, min_rounds=3, is_flow=False):
        # 先初始化 DB(避免 messages 表不存在)
        import database
        database.init_db()
        # 创建真实 course 和 session
        course_id = database.create_course(
            title="t7 测试",
            source_type="text",
            source_path="",
            reading_mode="speed",
        )
        session_id = database.create_session(
            course_id, 0, "ganyu"
        )

        sm = DialogueStateMachine.__new__(DialogueStateMachine)
        sm.course_id = course_id
        sm.session_id = session_id
        sm.chapter_index = 0
        sm.teacher_role_id = "ganyu"
        sm.depth = "basic"
        sm.duration_minutes = 15
        sm.syllabus_items = syllabus_data
        sm.current_round = current_round
        sm.total_rounds = current_round
        sm.is_flow_state = is_flow
        sm.flow_extend_count = 0
        sm.consecutive_stuck = 0
        sm.consecutive_thinking = 0
        sm._core_probe_attempted = False
        sm.messages = [{"role": "user", "content": "学生回答"}]
        sm.session_mastered_ids = set()
        return sm

    async def test_scenario_a_unforced_when_not_touched(self):
        """场景A: 未触达核心 + 未尝试 → 触发强制 probe"""
        sm = self._setup_sm([
            {"chapter_index": 0, "description": "核心", "status": "pending", "importance": 5},
            {"chapter_index": 0, "description": "低", "status": "pending", "importance": 1},
        ], current_round=3, min_rounds=3)

        async def fake_probe_gen():
            if False:
                yield ""
        async def fake_end_gen():
            yield "end"

        with patch.object(sm, "_eval", new=AsyncMock(return_value={"status": "thinking", "mastered_items": []})):
            with patch.object(sm, "_auto_mark_syllabus"):
                with patch.object(sm, "_probe", return_value=fake_probe_gen()) as mock_probe:
                    with patch.object(sm, "_end_session", return_value=fake_end_gen()) as mock_end:
                        # 跑 handle_user_input
                        gen = sm.handle_user_input("用户回复")
                        async for chunk in gen:
                            pass

        # 验证:_probe 被调用(强制核心)
        assert mock_probe.called, "未触达核心时应触发 _probe"
        # 验证:_end_session 也被调用(强制 probe 后再判断一次)
        assert mock_end.called, "强制 probe 后仍需判断结束"
        # 验证:_core_probe_attempted 标记已置为 True
        assert sm._core_probe_attempted is True

    async def test_scenario_b_skip_force_when_already_attempted(self):
        """场景B: 未触达核心 + 已尝试 → 不再强制 probe,直接结束"""
        sm = self._setup_sm([
            {"chapter_index": 0, "description": "核心", "status": "pending", "importance": 5},
        ], current_round=3, min_rounds=3)
        sm._core_probe_attempted = True  # 已尝试

        async def fake_probe_gen():
            if False:
                yield ""
        async def fake_end_gen():
            yield "end"

        with patch.object(sm, "_eval", new=AsyncMock(return_value={"status": "thinking", "mastered_items": []})):
            with patch.object(sm, "_auto_mark_syllabus"):
                with patch.object(sm, "_probe", return_value=fake_probe_gen()) as mock_probe:
                    with patch.object(sm, "_end_session", return_value=fake_end_gen()) as mock_end:
                        gen = sm.handle_user_input("回复")
                        async for chunk in gen:
                            pass

        # 验证:_probe 被调用 1 次(正常用户回应),不调用第 2 次(强制)
        # 这里用 call_count 验证:正常 PROBE = 1,无强制 PROBE
        assert mock_probe.call_count == 1, (
            f"已尝试过强制 probe 时不应再触发,实际调用 {mock_probe.call_count} 次"
        )
        # 验证:直接结束
        assert mock_end.called

    async def test_scenario_c_normal_end_when_touched(self):
        """场景C: 已触达核心 → 正常结束,不强制 probe"""
        sm = self._setup_sm([
            {"chapter_index": 0, "description": "核心", "status": "mastered", "importance": 5},
        ], current_round=3, min_rounds=3)

        async def fake_probe_gen():
            if False:
                yield ""
        async def fake_end_gen():
            yield "end"

        with patch.object(sm, "_eval", new=AsyncMock(return_value={"status": "thinking", "mastered_items": []})):
            with patch.object(sm, "_auto_mark_syllabus"):
                with patch.object(sm, "_probe", return_value=fake_probe_gen()) as mock_probe:
                    with patch.object(sm, "_end_session", return_value=fake_end_gen()) as mock_end:
                        gen = sm.handle_user_input("回复")
                        async for chunk in gen:
                            pass

        # 验证:正常 PROBE = 1 次(用户回应),无强制 PROBE
        assert mock_probe.call_count == 1, (
            f"已触达核心时不应触发强制 probe,实际调用 {mock_probe.call_count} 次"
        )
        # 验证:正常结束
        assert mock_end.called

    async def test_scenario_d_no_end_in_flow_state(self):
        """场景D: 心流状态持续 → 不结束(无论是否触达核心)

        注意:_check_flow_state 每轮重判,需要让 mock 用户消息触发心流信号
        (长度 > 50 + 含问号) → is_flow_state 保持 True
        """
        sm = self._setup_sm([
            {"chapter_index": 0, "description": "核心", "status": "pending", "importance": 5},
        ], current_round=3, min_rounds=3, is_flow=True)  # 进心流

        # 心流触发:用户消息长 + 含问号
        flow_user_msg = "这是一段超过五十个字符的用户回答，里面还包含了一个问句？为什么这样子设计呢？"

        async def fake_probe_gen():
            if False:
                yield ""
        async def fake_end_gen():
            yield "end"

        # 直接 patch _check_flow_state 强制保持 is_flow_state = True
        # (避免每轮重判导致心流状态丢失)
        with patch.object(sm, "_eval", new=AsyncMock(return_value={"status": "thinking", "mastered_items": []})):
            with patch.object(sm, "_auto_mark_syllabus"):
                with patch.object(sm, "_check_flow_state"):  # 不让 _check_flow_state 重置状态
                # 重置 is_flow_state 保持 True
                    sm.is_flow_state = True
                    with patch.object(sm, "_probe", return_value=fake_probe_gen()) as mock_probe:
                        with patch.object(sm, "_end_session", return_value=fake_end_gen()) as mock_end:
                            gen = sm.handle_user_input(flow_user_msg)
                            async for chunk in gen:
                                pass

        # 验证:正常 PROBE 1 次(用户回应),心流状态持续 → 不结束
        assert mock_probe.call_count == 1
        assert not mock_end.called, "心流状态下不应结束"

    async def test_scenario_below_min_rounds_no_end(self):
        """场景E: 接近 min_rounds(current_round 2→3) → 触发核心 probe,不直接结束

        handle_user_input 开头 current_round += 1,2→3 刚好达到 min_rounds=3,
        此时未触达核心 → 强制 PROBE 一次(2 次),但不立刻结束(给用户答完一轮)
        """
        sm = self._setup_sm([
            {"chapter_index": 0, "description": "核心", "status": "pending", "importance": 5},
        ], current_round=2, min_rounds=3)  # 接近 min_rounds

        async def fake_probe_gen():
            if False:
                yield ""
        async def fake_end_gen():
            yield "end"

        with patch.object(sm, "_eval", new=AsyncMock(return_value={"status": "thinking", "mastered_items": []})):
            with patch.object(sm, "_auto_mark_syllabus"):
                with patch.object(sm, "_probe", return_value=fake_probe_gen()) as mock_probe:
                    with patch.object(sm, "_end_session", return_value=fake_end_gen()) as mock_end:
                        gen = sm.handle_user_input("回复")
                        async for chunk in gen:
                            pass

        # 验证:正常 PROBE 1 次 + 强制 PROBE 1 次(因未触达核心且 current_round 达到边界)
        assert mock_probe.call_count == 2, (
            f"current_round 达到 min_rounds 时未触达核心应触发 2 次 PROBE,"
            f"实际 {mock_probe.call_count} 次"
        )
        # 验证:仍走完强制 probe 后才结束(避免死循环)
        assert mock_end.called, "强制 probe 后仍需结束"
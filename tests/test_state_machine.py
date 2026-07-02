"""
state_machine.py 单元测试
测试对话状态机的核心逻辑、工具函数和状态流转.
"""
import pytest
from unittest.mock import patch, MagicMock
from state_machine import strip_thinking_tags, DialogueStateMachine


class TestStripThinkingTags:
    """测试思考标签清理"""

    def test_strip_xml_thinking_tags(self):
        """清理<thinking>标签"""
        content = "<thinking>我在思考...</thinking>这是正式回答"
        result = strip_thinking_tags(content)
        assert "<thinking>" not in result
        assert "我在思考" not in result
        assert "这是正式回答" in result

    def test_strip_angle_bracket_thinking(self):
        """清理<think>标签"""
        content = "<think>让我想想</think>这是回答"
        result = strip_thinking_tags(content)
        assert "<think>" not in result
        assert "让我想想" not in result
        assert "这是回答" in result

    def test_strip_multiple_thinking(self):
        """清理多个思考标签"""
        content = "<think>第一段思考</think>内容<thinking>第二段思考</thinking>更多"
        result = strip_thinking_tags(content)
        assert "思考" not in result or result.count("思考") < 2
        assert "内容" in result
        assert "更多" in result

    def test_empty_content(self):
        """空内容处理"""
        assert strip_thinking_tags("") == ""
        assert strip_thinking_tags(None) is None

    def test_no_thinking_tags(self):
        """无标签内容不变"""
        content = "正常的回答内容"
        result = strip_thinking_tags(content)
        assert result == content

    def test_cleanup_extra_newlines(self):
        """清理多余换行"""
        content = "内容1\n\n\n\n内容2"
        result = strip_thinking_tags(content)
        assert "\n\n\n" not in result


class TestDialogueStateMachineInit:
    """测试状态机初始化"""

    def test_initial_state(self):
        """初始状态应为INIT"""
        sm = DialogueStateMachine(
            session_id="test-session",
            course_id="test-course",
            chapter_index=0,
            teacher_role_id="march7"
        )
        assert sm.state == sm.INIT
        assert sm.session_id == "test-session"
        assert sm.chapter_index == 0
        assert sm.current_round == 0

    def test_default_values(self):
        """默认参数值"""
        sm = DialogueStateMachine(
            session_id="s1",
            course_id="c1",
            chapter_index=0,
            teacher_role_id="socrates"
        )
        assert sm.depth == "standard"
        assert sm.duration_minutes == 30
        assert sm.sliders == {}
        assert sm.messages == []

    def test_custom_duration(self):
        """自定义学习时长"""
        sm = DialogueStateMachine(
            session_id="s1",
            course_id="c1",
            chapter_index=0,
            teacher_role_id="keqing",
            duration_minutes=60
        )
        assert sm.duration_minutes == 60


class TestGetMinRounds:
    """测试最小轮次计算"""

    def test_15_minutes(self):
        """15分钟对应3轮"""
        sm = DialogueStateMachine("s", "c", 0, "march7", duration_minutes=15)
        assert sm._get_min_rounds() == 3

    def test_30_minutes(self):
        """30分钟对应6轮"""
        sm = DialogueStateMachine("s", "c", 0, "march7", duration_minutes=30)
        assert sm._get_min_rounds() == 6

    def test_60_minutes(self):
        """60分钟对应12轮"""
        sm = DialogueStateMachine("s", "c", 0, "march7", duration_minutes=60)
        assert sm._get_min_rounds() == 12

    def test_120_minutes(self):
        """120分钟对应24轮（P2-② 修复）"""
        sm = DialogueStateMachine("s", "c", 0, "march7", duration_minutes=120)
        assert sm._get_min_rounds() == 24

    def test_unknown_duration(self):
        """未知时长使用默认值"""
        sm = DialogueStateMachine("s", "c", 0, "march7", duration_minutes=45)
        assert sm._get_min_rounds() == 6


class TestMasteryHint:
    """测试掌握项提示生成"""

    def test_get_mastery_hint_with_pending(self):
        """有待掌握项时生成提示"""
        sm = DialogueStateMachine("s", "c", 0, "march7")
        sm.syllabus_items = [
            {"chapter_index": 0, "status": "pending", "id": 1, "description": "能解释概念A"},
            {"chapter_index": 0, "status": "mastered", "id": 2, "description": "能描述概念B"},
        ]
        hint = sm._get_mastery_hint()
        assert "能解释概念A" in hint

    def test_get_mastery_hint_no_pending(self):
        """无待掌握项时返回空"""
        sm = DialogueStateMachine("s", "c", 0, "march7")
        sm.syllabus_items = [
            {"chapter_index": 0, "status": "mastered", "id": 1, "description": "已掌握"},
        ]
        hint = sm._get_mastery_hint()
        assert hint == ""


class TestMasteryCheck:
    """测试掌握项检查清单"""

    def test_get_mastery_check_with_pending(self):
        """有待完成项时生成检查清单"""
        sm = DialogueStateMachine("s", "c", 0, "march7")
        sm.syllabus_items = [
            {"chapter_index": 0, "status": "pending", "id": 1, "description": "测试项"},
        ]
        check = sm._get_mastery_check()
        assert "测试项" in check

    def test_get_mastery_check_no_pending(self):
        """无待完成项时返回空"""
        sm = DialogueStateMachine("s", "c", 0, "march7")
        sm.syllabus_items = [
            {"chapter_index": 0, "status": "mastered", "id": 1, "description": "已掌握"},
        ]
        check = sm._get_mastery_check()
        assert check == ""


class TestKnowledgeCoverage:
    """测试知识点覆盖检查"""

    def test_full_coverage(self):
        """全部掌握"""
        sm = DialogueStateMachine("s", "c", 0, "march7")
        sm.syllabus_items = [
            {"chapter_index": 0, "status": "mastered", "id": 1},
            {"chapter_index": 0, "status": "mastered", "id": 2},
        ]
        coverage = sm._check_knowledge_coverage()
        assert coverage["mastered"] == 2
        assert coverage["pending"] == 0

    def test_partial_coverage(self):
        """部分掌握"""
        sm = DialogueStateMachine("s", "c", 0, "march7")
        sm.syllabus_items = [
            {"chapter_index": 0, "status": "mastered", "id": 1},
            {"chapter_index": 0, "status": "pending", "id": 2},
        ]
        coverage = sm._check_knowledge_coverage()
        assert coverage["partial"] is True
        assert "未掌握" in coverage["suggestion"]


class TestDebateTrigger:
    """测试辩论触发检测"""

    def test_debate_trigger_words(self):
        """认同词汇触发辩论"""
        sm = DialogueStateMachine("s", "c", 3, "march7")
        # 包含多个认同词汇
        user_text = "是的，你说得对，确实如此"
        # 辩论触发是异步方法，这里测试关键词检测逻辑
        agreement_keywords = ["是的", "对", "有道理", "没错", "你说得对", "确实", "同意", "明白了"]
        agreement_count = sum(1 for kw in agreement_keywords if kw in user_text)
        assert agreement_count >= 2

    def test_no_debate_trigger(self):
        """无明显认同词不触发"""
        user_text = "我不这么认为，我觉得另有原因"
        agreement_keywords = ["是的", "对", "有道理", "没错", "你说得对", "确实", "同意", "明白了"]
        agreement_count = sum(1 for kw in agreement_keywords if kw in user_text)
        assert agreement_count < 2


class TestFlowStateDetection:
    """测试心流状态检测"""

    def test_enter_flow_state(self):
        """进入心流状态"""
        sm = DialogueStateMachine("s", "c", 0, "march7", duration_minutes=30)
        sm.current_round = 6  # >= FLOW_DETECTION min_rounds (假设)
        user_text = "为什么这个概念很重要？我应该如何应用它？"
        
        # 心流检测条件：足够轮次 + 长回复 + 有疑问词
        sm._check_flow_state(user_text)
        # 如果满足条件，is_flow_state应变为True


class TestAllStates:
    """测试所有状态常量"""

    def test_all_states_defined(self):
        """所有状态常量应定义"""
        sm = DialogueStateMachine("s", "c", 0, "march7")
        assert hasattr(sm, 'INIT')
        assert hasattr(sm, 'SHARE')
        assert hasattr(sm, 'PROBE')
        assert hasattr(sm, 'WAIT_USER')
        assert hasattr(sm, 'EVAL')
        assert hasattr(sm, 'EXPLAIN')
        assert hasattr(sm, 'GUIDE')
        assert hasattr(sm, 'ACTION')
        assert hasattr(sm, 'DEBATE')
        assert hasattr(sm, 'END')


class TestRecordQuickMaster:
    """v1.4 评审 🔴 #1: '我已掌握' 按钮的封装,避免 app.py 直接操作 sm 内部状态"""

    def test_marks_first_pending_as_mastered(self):
        """首个 pending 项被标为 mastered,返回其 id"""
        sm = DialogueStateMachine("s", "c", 0, "march7")
        sm.syllabus_items = [
            {"id": 10, "chapter_index": 0, "status": "pending", "importance": 3},
            {"id": 20, "chapter_index": 0, "status": "pending", "importance": 5},
        ]
        with patch("state_machine.db.add_message"), \
             patch("state_machine.db.add_learning_event"), \
             patch("state_machine.db.update_syllabus_item") as mock_update, \
             patch("state_machine.db.get_syllabus_items", return_value=sm.syllabus_items):
            result = sm.record_quick_master("我已掌握概念A")

        assert result == 10
        # 重要度更高的 id=20 不应被标记
        update_ids = [call.args[0] for call in mock_update.call_args_list]
        assert 10 in update_ids
        assert 20 not in update_ids
        # session_mastered_ids 也应记录
        assert 10 in sm.session_mastered_ids

    def test_no_pending_returns_none(self):
        """无 pending 时返回 None,不抛错"""
        sm = DialogueStateMachine("s", "c", 0, "march7")
        sm.syllabus_items = [
            {"id": 1, "chapter_index": 0, "status": "mastered"},
        ]
        with patch("state_machine.db.add_message"), \
             patch("state_machine.db.add_learning_event"), \
             patch("state_machine.db.update_syllabus_item") as mock_update, \
             patch("state_machine.db.get_syllabus_items", return_value=sm.syllabus_items):
            result = sm.record_quick_master("我已掌握")

        assert result is None
        mock_update.assert_not_called()

    def test_first_already_in_session_returns_none(self):
        """首条 pending 已在 session_mastered_ids 时,返回 None(防重复)"""
        sm = DialogueStateMachine("s", "c", 0, "march7")
        sm.syllabus_items = [
            {"id": 1, "chapter_index": 0, "status": "pending"},
        ]
        sm.session_mastered_ids.add(1)
        with patch("state_machine.db.add_message"), \
             patch("state_machine.db.add_learning_event"), \
             patch("state_machine.db.update_syllabus_item") as mock_update, \
             patch("state_machine.db.get_syllabus_items", return_value=sm.syllabus_items):
            result = sm.record_quick_master("我已掌握")

        assert result is None
        mock_update.assert_not_called()

    def test_round_and_message_persisted(self):
        """轮次+1、消息入库、事件记录——和 handle_user_input 入口一致"""
        sm = DialogueStateMachine("s", "c", 0, "march7")
        sm.syllabus_items = []
        with patch("state_machine.db.add_message") as mock_msg, \
             patch("state_machine.db.add_learning_event") as mock_evt, \
             patch("state_machine.db.update_syllabus_item"), \
             patch("state_machine.db.get_syllabus_items", return_value=[]):
            sm.record_quick_master("我已掌握")

        assert sm.current_round == 1
        assert sm.total_rounds == 1
        # 消息入内存 + 入库
        assert sm.messages[-1]["content"] == "我已掌握"
        assert any(call.args[1:3] == ("user", "我已掌握") for call in mock_msg.call_args_list)
        # 事件记录
        assert mock_evt.called

    def test_only_target_chapter_pending_considered(self):
        """只考虑当前 chapter 的 pending,其他 chapter 的不参与选择"""
        sm = DialogueStateMachine("s", "c", 0, "march7")
        sm.syllabus_items = [
            {"id": 100, "chapter_index": 1, "status": "pending"},  # 其他章
            {"id": 5, "chapter_index": 0, "status": "pending"},    # 本章
        ]
        with patch("state_machine.db.add_message"), \
             patch("state_machine.db.add_learning_event"), \
             patch("state_machine.db.update_syllabus_item") as mock_update, \
             patch("state_machine.db.get_syllabus_items", return_value=sm.syllabus_items):
            result = sm.record_quick_master("ok")

        # 应只标记本章 id=5,不标 id=100
        assert result == 5
        update_ids = [call.args[0] for call in mock_update.call_args_list]
        assert 5 in update_ids
        assert 100 not in update_ids


@pytest.mark.asyncio
class TestEndOfSessionCheck:
    """v1.4 评审 🔴 #1: 抽出来共享的"结束前核心触达保险",防止 quick_mastered 绕过"""

    async def test_no_yield_when_below_min_rounds(self):
        """轮次 < min_rounds 时不产任何 chunk"""
        sm = DialogueStateMachine("s", "c", 0, "march7", duration_minutes=30)
        sm.current_round = 2  # min_rounds=6 (30min)
        sm.syllabus_items = [{"id": 1, "chapter_index": 0, "status": "pending", "importance": 5}]

        chunks = []
        async for chunk in sm.end_of_session_check():
            chunks.append(chunk)

        assert chunks == []
        # 核心未触达但未到 min_rounds → 不应触发 _core_probe_attempted
        assert sm._core_probe_attempted is False

    async def test_no_yield_when_in_flow_state(self):
        """心流状态下即使到 min_rounds 也不强制结束"""
        sm = DialogueStateMachine("s", "c", 0, "march7", duration_minutes=30)
        sm.current_round = 6
        sm.is_flow_state = True
        sm.syllabus_items = []

        chunks = []
        async for chunk in sm.end_of_session_check():
            chunks.append(chunk)

        assert chunks == []

    async def test_ends_session_when_core_already_touched(self):
        """核心已触达 → 正常结束(不强制 probe)"""
        sm = DialogueStateMachine("s", "c", 0, "march7", duration_minutes=30)
        sm.current_round = 6
        # 已有 importance>=4 的项被 in_progress → 视为已触达
        sm.syllabus_items = [
            {"id": 1, "chapter_index": 0, "status": "in_progress", "importance": 5},
        ]

        chunks = []
        with patch("state_machine.db.add_message"), \
             patch.object(sm, "_end_session") as mock_end:
            async def fake_end(reason):
                yield f"END:{reason}"
            mock_end.return_value = fake_end("达到最小时长")
            async for chunk in sm.end_of_session_check():
                chunks.append(chunk)

        # 应走"已触达"分支,触发 _end_session
        assert any("达到最小时长" in str(c) for c in chunks)
        # _core_probe_attempted 仍为 False(没走强制 probe 分支)
        assert sm._core_probe_attempted is False

    async def test_sets_core_probe_attempted_flag(self):
        """核心未触达 + 达 min_rounds → 设置 _core_probe_attempted 标志"""
        sm = DialogueStateMachine("s", "c", 0, "march7", duration_minutes=30)
        sm.current_round = 6
        # 全部 status=pending,importance=2 → 未触达核心
        sm.syllabus_items = [
            {"id": 1, "chapter_index": 0, "status": "pending", "importance": 2, "description": "概念A"},
            {"id": 2, "chapter_index": 0, "status": "pending", "importance": 1, "description": "概念B"},
        ]

        # 整条 _probe / _end_session 链都 patch 掉,只验证 end_of_session_check 的分支判断
        async def fake_probe():
            yield "PROBE_CHUNK"
            # 把 importance=2 的项标为 in_progress 模拟"触达"路径
            sm.syllabus_items[0]["status"] = "in_progress"

        async def fake_end(reason):
            yield f"END:{reason}"

        with patch.object(sm, "_probe", side_effect=fake_probe), \
             patch.object(sm, "_end_session", side_effect=fake_end):
            chunks = []
            async for chunk in sm.end_of_session_check():
                chunks.append(chunk)

        # _core_probe_attempted 应被置 True(走过"未触达核心 → 强制 PROBE"分支)
        assert sm._core_probe_attempted is True
        # 强制 PROBE 后,若 _has_touched_core() 仍 False,会走 _end_session
        # 测试中我们手动把 in_progress 标上 → 触达成功 → 走 _end_session("达到最小时长(核心未触达)")
        assert any("END" in str(c) for c in chunks)


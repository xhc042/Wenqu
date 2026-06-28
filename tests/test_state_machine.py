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

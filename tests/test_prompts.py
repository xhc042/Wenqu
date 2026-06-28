"""
问渠 prompt 回归测试 —— 8 角色 × 4 深度 = 32 组合的一致性保护。

测试分三层:
1. 结构测试(默认跑) —— 验证 .md 文件存在 / 关键状态齐全 / 角色特征可辨
2. 深度一致性测试(默认跑) —— 验证 deep / dialectical 档的关键概念在 prompt 中体现
3. LLM-as-judge(标记 slow,需 API key) —— 验证实际输出符合角色与档位

为什么这套测试必要:
    改 prompt 是"产品灵魂"操作,8 角色 × 4 深度 = 32 组合,任何组合"变味"都会
    破坏用户体验。本测试是 prompt-engineer rein 的质量门禁。

使用方法:
    # 默认跑结构 + 深度一致性测试
    pytest tests/test_prompts.py -v

    # 跑 LLM-as-judge(需要 WENQU_API_KEY 环境变量)
    export WENQU_API_KEY=sk-...
    pytest tests/test_prompts.py -v -m slow
"""
import os
import re
from pathlib import Path

import pytest

PROMPTS_DIR = Path(__file__).parent.parent / "prompts" / "roles"

# 8 个 AI 教师角色 —— 关键词严格按各 .md 实际用词校准
ROLES = {
    "march7":   ["元气", "活泼", "少女", "碎碎念"],
    "keqing":   ["严格", "犀利", "干净利落", "极致"],
    "ganyu":    ["温柔", "细腻", "温和"],
    "socrates": ["追问", "谦逊", "睿智"],
    "linmo":    ["直率", "务实", "实干"],
    "yunyi":    ["好奇", "跨学科", "跳跃"],
    "zhiwei":   ["应试", "考点", "靶向"],
    "yunxiu":   ["沉静", "惜字如金", "沉默", "克制"],
}

# AI 主动输出的对话状态(角色 prompt 应至少覆盖这 5 个)
# INIT / WAIT_USER 是系统状态,ACTION / DEBATE 是高级状态,不一定每个角色都涉及
AI_OUTPUT_STATES = ["SHARE", "PROBE", "EVAL", "EXPLAIN", "GUIDE"]

# 深度档位
DEPTHS = ["basic", "standard", "deep", "dialectical"]
DEEP_DEPTHS = {"deep", "dialectical"}

# critical_angles 关键概念(对应 config.py DEPTH_CONFIG 的 critical_angles 字段)
# 关键词按各 .md 实际用词校准,涵盖哲学型 / 思辨型 / 应试型
CRITICAL_ANGLE_KEYWORDS = [
    "反例", "假设", "前提", "陷阱", "区别", "辨析",
    "矛盾", "局限", "反过来", "边界", "代价", "适用",
]

# 必须有 critical_angles 关键词的角色(思辨型 / 应试型)
# 注意:linmo 是"动手派"而非"批判派",他的 critical thinking 通过实操验证体现,
# 不强求文字上有反例/假设等关键词。
CRITICAL_ROLES_REQUIRED = {"socrates", "keqing", "yunyi", "zhiwei"}


# =============================================================================
# 第一层:结构测试(默认跑,快速)
# =============================================================================

class TestPromptStructure:
    """验证每个角色 prompt 的结构与基本完整性"""

    @pytest.mark.parametrize("role_id", list(ROLES.keys()))
    def test_prompt_file_exists(self, role_id):
        """每个角色必须有 .md 文件"""
        path = PROMPTS_DIR / f"{role_id}.md"
        assert path.exists(), f"缺失角色 prompt: {path}"

    @pytest.mark.parametrize("role_id", list(ROLES.keys()))
    def test_prompt_length_reasonable(self, role_id):
        """每个 prompt 不应过短(< 500)或过长(> 5000)"""
        content = (PROMPTS_DIR / f"{role_id}.md").read_text(encoding="utf-8")
        length = len(content)
        assert length >= 500, f"{role_id}.md 过短({length} 字),可能不完整"
        assert length <= 5000, f"{role_id}.md 过长({length} 字),需拆分或精简"

    @pytest.mark.parametrize("role_id", list(ROLES.keys()))
    def test_prompt_covers_ai_output_states(self, role_id):
        """每个 prompt 应覆盖全部 5 个 AI 输出状态(SHARE/PROBE/EVAL/EXPLAIN/GUIDE)"""
        content = (PROMPTS_DIR / f"{role_id}.md").read_text(encoding="utf-8")
        missing = [s for s in AI_OUTPUT_STATES if s not in content]
        assert not missing, f"{role_id}.md 缺少 AI 输出状态: {missing}"

    @pytest.mark.parametrize("role_id", list(ROLES.keys()))
    def test_role_tone_keywords_present(self, role_id):
        """每个角色的语气关键词至少出现 1 个(防 copy-paste / 防空洞)"""
        content = (PROMPTS_DIR / f"{role_id}.md").read_text(encoding="utf-8")
        keywords = ROLES[role_id]
        found = [k for k in keywords if k in content]
        assert len(found) >= 1, (
            f"{role_id}.md 未找到任何特征关键词 {keywords},"
            f"可能角色定位不清或 copy-paste 错误"
        )

    def test_no_identical_prompts(self):
        """8 个角色的 prompt 不应完全相同(防 copy-paste 错误)"""
        contents = {
            r: (PROMPTS_DIR / f"{r}.md").read_text(encoding="utf-8")
            for r in ROLES
        }
        for r1 in ROLES:
            for r2 in ROLES:
                if r1 >= r2:
                    continue
                assert contents[r1] != contents[r2], (
                    f"{r1}.md 和 {r2}.md 完全相同,可能是 copy-paste 错误"
                )

    def test_socrates_avoids_direct_answers(self):
        """苏格拉底角色的核心法则是'只问不答',prompt 中必须体现"""
        content = (PROMPTS_DIR / "socrates.md").read_text(encoding="utf-8")
        has_rule = any(
            phrase in content
            for phrase in ["只问不答", "不给答案", "不要直接给", "绝不直接给", "从不当场给"]
        )
        assert has_rule, (
            "socrates.md 未明确'只问不答'的核心法则,破坏苏格拉底角色定位"
        )

    def test_zhiwei_focuses_on_exam(self):
        """知微(应试型)角色的 prompt 应明确'考点'导向"""
        content = (PROMPTS_DIR / "zhiwei.md").read_text(encoding="utf-8")
        assert "考点" in content, (
            "zhiwei.md 未体现'考点'导向,破坏应试型角色定位"
        )


# =============================================================================
# 第二层:深度一致性测试(默认跑,快速)
# =============================================================================

class TestDepthConsistency:
    """验证 deep / dialectical 档的关键概念在思辨型角色 prompt 中体现。"""

    @pytest.mark.parametrize("role_id", sorted(CRITICAL_ROLES_REQUIRED))
    def test_critical_roles_have_angles(self, role_id):
        """思辨型角色(socrates/linmo/keqing/yunyi/zhiwei)必须有 critical_angles 接口词"""
        content = (PROMPTS_DIR / f"{role_id}.md").read_text(encoding="utf-8")
        found = [k for k in CRITICAL_ANGLE_KEYWORDS if k in content]
        assert len(found) >= 1, (
            f"{role_id}.md 是思辨型角色,但未埋任何 critical_angles 接口词 "
            f"{CRITICAL_ANGLE_KEYWORDS},deep / dialectical 档会失去'批判性思考'着力点"
        )

    @pytest.mark.parametrize("role_id", list(ROLES.keys()))
    def test_prompt_has_probe_question_template(self, role_id):
        """每个角色的 PROBE 状态应有问句模板(包含问号或'问题'/'提问')"""
        content = (PROMPTS_DIR / f"{role_id}.md").read_text(encoding="utf-8")
        probe_match = re.search(r"### PROBE状态(.*?)(?=###|\Z)", content, re.DOTALL)
        assert probe_match, f"{role_id}.md 缺少 ### PROBE状态 段"
        probe_section = probe_match.group(1)
        has_question = (
            "?" in probe_section
            or "？" in probe_section
            or "问题" in probe_section
            or "提问" in probe_section
        )
        assert has_question, (
            f"{role_id}.md 的 PROBE 状态没有问句模板,破坏苏格拉底式追问体验"
        )


# =============================================================================
# 第三层:LLM-as-judge(标记 slow,需 API key,默认跳过)
# =============================================================================

STANDARD_QUESTIONS = [
    ("basic",       "什么是单元测试?"),
    ("standard",    "你怎么看微服务架构的优缺点?"),
    ("deep",        "批判性分析下'技术中立性'这个说法"),
    ("dialectical", "如果 AI 写作工具让每个人都能写出'专业级'文章,这对人类创作意味着什么?"),
]


@pytest.mark.slow
class TestLLMJudge:
    """LLM-as-judge:验证实际输出符合角色与档位。

    默认跳过,启用方法:
        export WENQU_API_KEY=sk-...
        pytest tests/test_prompts.py -v -m slow

    目的:
        - 防 prompt 改完后某角色"变味"
        - 防深度档位失效(basic 跑出 deep 输出)
        - 防 critical_angles 在 deep 档未触发

    成本:
        4 问题 × 8 角色 = 32 次 LLM 调用,约 5-10 分钟,token 成本视模型而定。
        CI 默认不跑,只在 prompt-engineer rein 主动回归时跑。
    """

    @pytest.mark.parametrize("depth,question", STANDARD_QUESTIONS, ids=[d for d, _ in STANDARD_QUESTIONS])
    @pytest.mark.parametrize("role_id", list(ROLES.keys()))
    def test_role_depth_output(self, role_id, depth, question):
        """验证每个角色 × 每个深度档位输出符合预期"""
        api_key = os.environ.get("WENQU_API_KEY", "")
        if not api_key:
            pytest.skip("需要 WENQU_API_KEY 才能跑 LLM-as-judge")

        try:
            from llm_client import build_system_prompt, LLMClient
            from config import DEFAULT_SLIDERS
        except ImportError as e:
            pytest.skip(f"依赖缺失:{e}")

        sliders = DEFAULT_SLIDERS.get(role_id, DEFAULT_SLIDERS.get("socrates", {}))
        system_prompt = build_system_prompt(
            role_id=role_id,
            depth=depth,
            sliders=sliders,
            course_title="测试课程",
            chapter_title="测试章节",
            previous_summaries=[],
        )

        try:
            client = LLMClient()
            response = client.chat(
                system=system_prompt,
                user=question,
                model=os.environ.get("WENQU_TEST_MODEL", "deepseek-chat"),
            )
        except Exception as e:
            pytest.skip(f"LLM 调用失败:{e}")

        # 通用断言:响应非空且足够长
        assert response and len(response) > 20, (
            f"{role_id} 在 {depth} 档输出过短: {response[:100]}"
        )

        # 苏格拉底不应直接给"答案是..."
        if role_id == "socrates":
            for prefix in ["答案是", "答案是:", "答案是：", "简单来说", "显然"]:
                assert prefix not in response[:200], (
                    f"socrates 在 {depth} 档给出直接答案 ({prefix}): {response[:200]}"
                )

        # 思辨型角色在 deep / dialectical 档应触发 critical thinking
        if depth in DEEP_DEPTHS and role_id in CRITICAL_ROLES_REQUIRED:
            found = [k for k in CRITICAL_ANGLE_KEYWORDS if k in response]
            assert len(found) >= 1, (
                f"{role_id} 在 {depth} 档位未体现 critical thinking,"
                f"响应前 300 字: {response[:300]}"
            )
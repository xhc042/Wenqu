"""
问渠（Wenqu）v1.1 配置管理模块
"""

import os
import json
import sys
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).parent.resolve()

# 应用版本
VERSION = "1.20"

# 资源目录（static/prompts）：PyInstaller onefile 模式下解压到 sys._MEIPASS
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    _RESOURCES_DIR = Path(sys._MEIPASS)
else:
    _RESOURCES_DIR = BASE_DIR

# 数据目录：支持环境变量覆盖（PyInstaller 打包后指向 exe 同级目录，确保数据库持久化）
_data_dir_env = os.environ.get("WENQU_DATA_DIR")
if _data_dir_env:
    DATA_DIR = Path(_data_dir_env)
else:
    DATA_DIR = BASE_DIR / "wenqu_data"
PROMPTS_DIR = _RESOURCES_DIR / "prompts" / "roles"
STATIC_DIR = _RESOURCES_DIR / "static"

# 确保数据目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)
# 打包后 prompts 目录是只读资源，不需要创建；开发模式下确保存在
if not getattr(sys, 'frozen', False):
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

# 数据库路径
DB_PATH = DATA_DIR / "wenqu.db"

# LLM 配置
LLM_CONFIG = {
    "api_key": os.getenv("WENQU_API_KEY", ""),
    "base_url": os.getenv("WENQU_BASE_URL", "https://api.deepseek.com"),
    "model": os.getenv("WENQU_MODEL", "deepseek-chat"),
    "temperature": 0.7,
    "max_tokens": 2048,
    "timeout": 60,
}

# 角色默认风格滑块值（严谨⇄发散、鼓励⇄严厉、简练⇄详实）
DEFAULT_SLIDERS = {
    "march7": {"strictness": -1, "encouragement": 2, "verbosity": 1},
    "keqing": {"strictness": 4, "encouragement": -3, "verbosity": -2},
    "ganyu": {"strictness": -2, "encouragement": 3, "verbosity": 2},
    "socrates": {"strictness": 1, "encouragement": 0, "verbosity": -1},
    "linmo": {"strictness": 3, "encouragement": -2, "verbosity": -3},
    "yunyi": {"strictness": -3, "encouragement": 1, "verbosity": 3},
    "zhiwei": {"strictness": 4, "encouragement": -4, "verbosity": -1},
    "yunxiu": {"strictness": -4, "encouragement": 1, "verbosity": -4},
}

# 认知深度配置
DEPTH_CONFIG = {
    "basic": {
        "share_max_length": 80,
        "probe_complexity": "仅涉及概念定义层面",
        "explain_detail": "多举例，少术语",
        "description": "聚焦基础",
    },
    "standard": {
        "share_max_length": 150,
        "probe_complexity": "角色默认风格",
        "explain_detail": "角色默认风格",
        "description": "标准",
    },
    "deep": {
        "share_max_length": 0,  # 不限
        "probe_complexity": "强制对比前序章节，允许指出教材逻辑瑕疵",
        "explain_detail": "引用前序章节矛盾点",
        "description": "深入探究",
        "critical_angles": [
            "作者假设是否合理？",
            "不同章节之间是否存在矛盾？",
            "这个理论的反面观点是什么？",
            "在实际应用中可能遇到什么局限？",
        ],
    },
    "dialectical": {
        "share_max_length": 0,
        "probe_complexity": "必须对比至少2个章节的观点，指出潜在矛盾或统一关系",
        "explain_detail": "引用教材原文+外部学术观点进行对比分析",
        "description": "辩证分析",
        "critical_angles": [
            "作者假设是否合理？",
            "不同章节之间是否存在矛盾？",
            "这个理论的反面观点是什么？",
            "在实际应用中可能遇到什么局限？",
        ],
    },
}

# ==================== 阅读模式配置 ====================
READING_MODE_CONFIG = {
    "speed": {
        "label": "速读·精华",
        "emoji": "🚀",
        "extract_depth": "full_toc_first",
        "content_sample": "llm_summary",
        "llm_tier": "balanced",
        "generate_syllabus": True,
        "syllabus_strategy": "key_points_only",
        "max_chapters_dialogue": 5,
        "description": "快速掌握全书最重要知识点",
    },
    "standard": {
        "label": "细读",
        "emoji": "📖",
        "extract_depth": "full",
        "content_sample": "full_5000",
        "llm_tier": "balanced",
        "generate_syllabus": True,
        "description": "系统学习，逐章推进",
    },
    "deep": {
        "label": "研读·辩证",
        "emoji": "🔬",
        "extract_depth": "full_lazy",
        "content_sample": "full",
        "llm_tier": "flagship",
        "generate_syllabus": True,
        "generate_socratic_questions": True,
        "dialectical_analysis": True,
        "cross_chapter_comparison": True,
        "critical_thinking_prompts": True,
        "max_depth_rounds": 20,
        "description": "学术研究，专业精进，苏格拉底式追问",
    },
}

DEFAULT_READING_MODE = "standard"

# 心流检测参数
FLOW_DETECTION = {
    "min_rounds": 3,
    "reply_length_threshold": 50,
    "auto_extend_rounds": 3,
    "probe_timeout_seconds": 120,
}

# 学习时长选项（分钟）
DURATION_OPTIONS = [15, 30, 60, 120]

# ==================== 模式-契约联动默认值 ====================
MODE_CONTRACT_DEFAULTS = {
    "speed": {
        "depth": "basic",
        "duration": 15,
        "goal": "完成知识地图，掌握全书最重要的5-10个核心概念",
        "expected_output": "知识快照 + 核心观点列表",
    },
    "standard": {
        "depth": "standard",
        "duration": 30,
        "goal": "逐章学习，覆盖全部掌握项",
        "expected_output": "知识点掌握报告 + 复习总结",
    },
    "deep": {
        "depth": "deep",
        "duration": 60,
        "goal": "深度理解+批判性思考，形成个人辩证分析",
        "expected_output": "思辨笔记 + 读书笔记",
    },
}

# ==================== 时长配置 ====================
DURATION_CONFIG = {
    15: {
        "label": "快速浏览",
        "target": "complete_1_chapter_or_2_key_points",
        "min_rounds": 3,
        "max_chapters": 1,
        "strategy": "focus_on_high_yield",
    },
    30: {
        "label": "标准学习",
        "target": "complete_1_chapter_thoroughly",
        "min_rounds": 6,
        "max_chapters": 1,
        "strategy": "balanced",
    },
    60: {
        "label": "深度学习",
        "target": "complete_2_chapters_or_master_all",
        "min_rounds": 12,
        "max_chapters": 2,
        "strategy": "comprehensive",
    },
    120: {
        "label": "沉浸式学习",
        "target": "complete_remaining_or_master_weak_areas",
        "min_rounds": 24,
        "max_chapters": 999,
        "strategy": "mastery_based",
    },
}

# 角色推荐规则（关键词匹配）
ROLE_RECOMMENDATION = {
    "编程|代码|算法|数据": ["linmo", "keqing", "ganyu"],
    "哲学|政治|伦理|理论": ["socrates", "ganyu", "yunxiu"],
    "考试|考研|考证|真题": ["zhiwei", "keqing", "ganyu"],
    "创意|写作|设计|艺术": ["yunyi", "march7", "socrates"],
    "文学|小说|诗歌|历史": ["yunxiu", "ganyu", "yunyi"],
}

DEFAULT_ROLES = ["ganyu", "march7", "keqing"]

ROLES_META = {
    "march7": {
        "name": "三月七", "name_en": "March 7", "emoji": "🌸",
        "style": "活泼鼓励式追问",
        "personality": "元气少女、爱碎碎念、善于鼓励",
        "best_for": "创意发散、初学入门、需要建立信心的学习阶段",
        "tags": ["创意", "入门", "鼓励", "趣味"],
    },
    "keqing": {
        "name": "刻晴", "name_en": "Keqing", "emoji": "⚡",
        "style": "严格高效式追问",
        "personality": "外冷内热、追求卓越、逻辑清晰",
        "best_for": "技能训练、应试备考、需要高强度训练的阶段",
        "tags": ["严格", "逻辑", "高效", "备考"],
    },
    "ganyu": {
        "name": "甘雨", "name_en": "Ganyu", "emoji": "🌿",
        "style": "温柔引导式追问",
        "personality": "温柔腼腆、博学多识、耐心细致",
        "best_for": "知识探究、阅读内化、需要深度理解的学习阶段",
        "tags": ["温柔", "博学", "耐心", "深度"],
    },
    "socrates": {
        "name": "苏格拉底", "name_en": "Socrates", "emoji": "🏛️",
        "style": "纯正苏格拉底式追问",
        "personality": "睿智、谦逊、善于反问，从不直接给答案",
        "best_for": "哲学、政治学、理论思辨、深度学习",
        "tags": ["哲学", "思辨", "反问", "深度"],
    },
    "linmo": {
        "name": "林墨", "name_en": "LinMo", "emoji": "🔧",
        "style": "问题驱动式教学",
        "personality": "直率、务实、厌恶空谈",
        "best_for": "编程、数据分析、工程实践、实操类学习",
        "tags": ["编程", "实战", "直率", "工程"],
    },
    "yunyi": {
        "name": "云逸", "name_en": "YunYi", "emoji": "🌈",
        "style": "联想式追问",
        "personality": "好奇、跳脱、知识面极广",
        "best_for": "创意写作、设计、跨学科学习",
        "tags": ["创意", "跨学科", "联想", "开放"],
    },
    "zhiwei": {
        "name": "知微", "name_en": "ZhiWei", "emoji": "🎯",
        "style": "靶向式追问",
        "personality": "敏锐、务实、结果导向",
        "best_for": "考研、考证、标准化考试",
        "tags": ["考试", "精准", "应试", "高效"],
    },
    "yunxiu": {
        "name": "云岫", "name_en": "YunXiu", "emoji": "☁️",
        "style": "留白式追问",
        "personality": "沉静、深邃、惜字如金",
        "best_for": "文学名著、哲学原典、历史深度研读",
        "tags": ["文学", "哲学", "沉静", "深度"],
    },
}

# 群聊发言维度
GROUP_CHAT_DIMENSIONS = {
    "march7": "生活化比喻",
    "keqing": "指出1个逻辑漏洞",
    "ganyu": "补充1个背景知识",
    "socrates": "提出1个反问",
    "linmo": "实际应用建议",
    "yunyi": "跨领域联想",
    "zhiwei": "考点提醒",
    "yunxiu": "一句精辟总结",
}

# 情感分配置
AFFINITY_BASE = 10
AFFINITY_UNLOCK_THRESHOLDS = [60, 80, 100]

# WebSocket
WS_HEARTBEAT_INTERVAL = 30

# 服务器配置
HOST = os.getenv("WENQU_HOST", "127.0.0.1")
PORT = int(os.getenv("WENQU_PORT", "8766"))

# ==================== LLM 多模型层级配置 ====================
MODEL_TIER_CONFIG = {
    "fast": {
        "label": "轻量级",
        "description": "用于目录提取、摘要、简单问答（如 Claude Haiku / GPT-4o-mini）",
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    "balanced": {
        "label": "标准级",
        "description": "用于掌握项生成、常规对话（如 Claude Sonnet / GPT-4o）",
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    "flagship": {
        "label": "旗舰级",
        "description": "用于深度研读、苏格拉底追问（如 Claude Opus / GPT-4o）",
        "base_url": "",
        "api_key": "",
        "model": "",
    },
}

DEFAULT_MODEL_TIER = "balanced"


def get_model_tier_config(tier: str) -> dict:
    """获取指定层级的模型配置，优先从数据库读取，未配则继承主模型"""
    tier_cfg = MODEL_TIER_CONFIG.get(tier, MODEL_TIER_CONFIG["balanced"])
    cfg = dict(tier_cfg)
    # 如果该层级没有独立配置，复用主模型
    if not cfg.get("model"):
        cfg["base_url"] = get_llm_config()["base_url"]
        cfg["api_key"] = get_llm_config()["api_key"]
        cfg["model"] = get_llm_config()["model"]
    return cfg

# LLM API 配置（可动态修改）
def get_llm_config():
    """获取当前LLM配置"""
    cfg = dict(LLM_CONFIG)
    if not cfg["api_key"]:
        cfg["api_key"] = os.getenv("WENQU_API_KEY", "")
    if not cfg["api_key"]:
        cfg["api_key"] = os.getenv("OPENAI_API_KEY", "")
    if not cfg["api_key"]:
        cfg["api_key"] = os.getenv("DEEPSEEK_API_KEY", "")
    return cfg

def update_llm_config(**kwargs):
    """更新LLM配置"""
    LLM_CONFIG.update(kwargs)

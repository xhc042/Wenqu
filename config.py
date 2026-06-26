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
VERSION = "1.1.2"

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
    },
}

# 心流检测参数
FLOW_DETECTION = {
    "min_rounds": 3,
    "reply_length_threshold": 50,
    "auto_extend_rounds": 3,
    "probe_timeout_seconds": 120,
}

# 学习时长选项（分钟）
DURATION_OPTIONS = [15, 30, 60]

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
PORT = int(os.getenv("WENQU_PORT", "8765"))

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

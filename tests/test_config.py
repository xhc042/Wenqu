"""
config.py 单元测试
测试配置加载、验证和默认值.
"""
import pytest


class TestConfigValues:
    """测试配置值"""

    def test_llm_config_exists(self):
        """LLM配置存在"""
        import config
        assert hasattr(config, "LLM_CONFIG")
        assert "api_key" in config.LLM_CONFIG
        assert "base_url" in config.LLM_CONFIG
        assert "model" in config.LLM_CONFIG

    def test_llm_temperature_default(self):
        """temperature默认值"""
        import config
        assert config.LLM_CONFIG["temperature"] == 0.7

    def test_llm_max_tokens(self):
        """max_tokens默认值"""
        import config
        assert config.LLM_CONFIG["max_tokens"] == 2048

    def test_get_llm_config_function(self):
        """获取LLM配置函数"""
        import config
        assert hasattr(config, "get_llm_config")
        cfg = config.get_llm_config()
        assert isinstance(cfg, dict)

    def test_version(self):
        """版本号"""
        import config
        assert hasattr(config, "VERSION")

    def test_db_path_exists(self):
        """数据库路径配置"""
        import config
        assert hasattr(config, "DB_PATH")

    def test_data_dir_exists(self):
        """数据目录配置"""
        import config
        assert hasattr(config, "DATA_DIR")


class TestDepthConfig:
    """测试深度配置"""

    def test_depth_config_exists(self):
        """深度配置存在"""
        import config
        assert hasattr(config, "DEPTH_CONFIG")
        assert "standard" in config.DEPTH_CONFIG

    def test_basic_depth(self):
        """基础深度"""
        import config
        assert "basic" in config.DEPTH_CONFIG

    def test_deep_depth(self):
        """深度配置"""
        import config
        assert "deep" in config.DEPTH_CONFIG


class TestReadingModeConfig:
    """测试阅读模式配置"""

    def test_reading_mode_config_exists(self):
        """阅读模式配置存在"""
        import config
        assert hasattr(config, "READING_MODE_CONFIG")

    def test_standard_mode(self):
        """标准阅读模式"""
        import config
        assert "standard" in config.READING_MODE_CONFIG

    def test_default_reading_mode(self):
        """默认阅读模式"""
        import config
        assert config.DEFAULT_READING_MODE == "standard"


class TestFlowDetection:
    """测试心流检测配置"""

    def test_flow_detection_exists(self):
        """心流检测配置存在"""
        import config
        assert hasattr(config, "FLOW_DETECTION")

    def test_flow_min_rounds(self):
        """最小轮次"""
        import config
        assert config.FLOW_DETECTION["min_rounds"] == 3


class TestDurationConfig:
    """测试时长配置"""

    def test_duration_options(self):
        """时长选项"""
        import config
        assert hasattr(config, "DURATION_OPTIONS")
        assert 30 in config.DURATION_OPTIONS

    def test_duration_config_exists(self):
        """时长配置存在"""
        import config
        assert hasattr(config, "DURATION_CONFIG")


class TestRolesConfig:
    """测试角色配置"""

    def test_roles_meta_exists(self):
        """角色元数据配置"""
        import config
        assert hasattr(config, "ROLES_META")
        assert isinstance(config.ROLES_META, dict)
        assert "march7" in config.ROLES_META
        assert "socrates" in config.ROLES_META

    def test_default_roles(self):
        """默认角色"""
        import config
        assert hasattr(config, "DEFAULT_ROLES")
        assert isinstance(config.DEFAULT_ROLES, list)

    def test_group_chat_dimensions(self):
        """群聊维度配置"""
        import config
        assert hasattr(config, "GROUP_CHAT_DIMENSIONS")

    def test_affinity_config(self):
        """情感分配置"""
        import config
        assert hasattr(config, "AFFINITY_BASE")
        assert config.AFFINITY_BASE == 10


class TestModelTierConfig:
    """测试模型层级配置"""

    def test_model_tier_config_exists(self):
        """模型层级配置存在"""
        import config
        assert hasattr(config, "MODEL_TIER_CONFIG")
        assert "balanced" in config.MODEL_TIER_CONFIG

    def test_get_model_tier_config(self):
        """获取模型层级配置函数"""
        import config
        assert hasattr(config, "get_model_tier_config")


class TestServerConfig:
    """测试服务器配置"""

    def test_host_config(self):
        """主机配置"""
        import config
        assert hasattr(config, "HOST")

    def test_port_config(self):
        """端口配置"""
        import config
        assert hasattr(config, "PORT")

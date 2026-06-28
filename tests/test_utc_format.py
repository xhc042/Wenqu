"""
P0 测试: _utc 和 _utc_dict 时间戳格式化函数

修复第二轮审查 P0-①：_utc 函数空字符串处理错误
"""
import pytest
from app import _utc, _utc_dict


class TestUtcFunction:
    """_utc 函数单元测试"""

    def test_utc_adds_z_suffix(self):
        """正常 ISO 时间字符串应添加 Z 后缀"""
        assert _utc("2024-01-15T10:30:00") == "2024-01-15T10:30:00Z"

    def test_utc_preserves_existing_z(self):
        """已有 Z 后缀的时间不应重复添加"""
        assert _utc("2024-01-15T10:30:00Z") == "2024-01-15T10:30:00Z"

    def test_utc_empty_string_returns_empty(self):
        """空字符串应返回空字符串（修复 P0-① 核心 bug）"""
        assert _utc("") == ""

    def test_utc_none_returns_none(self):
        """None 应返回 None"""
        assert _utc(None) is None

    def test_utc_whitespace_only_returns_original(self):
        """空白字符串应返回原值（不添加 Z）"""
        assert _utc("   ") == "   "

    def test_utc_non_string_returns_as_is(self):
        """非字符串类型应原样返回"""
        assert _utc(123) == 123
        assert _utc([]) == []
        assert _utc({}) == {}

    def test_utc_with_microseconds(self):
        """带微秒的时间应正确处理"""
        assert _utc("2024-01-15T10:30:00.123456") == "2024-01-15T10:30:00.123456Z"

    def test_utc_chinese_datetime(self):
        """中文时间字符串应正确处理"""
        assert _utc("2024-06-28T14:00:00") == "2024-06-28T14:00:00Z"


class TestUtcDictFunction:
    """_utc_dict 字典时间戳格式化函数"""

    def test_utc_dict_adds_z_to_timestamp(self):
        """应为 dict 中的 timestamp 字段添加 Z 后缀"""
        d = {"timestamp": "2024-01-15T10:30:00", "name": "test"}
        result = _utc_dict(d, "timestamp")
        assert result["timestamp"] == "2024-01-15T10:30:00Z"
        assert result["name"] == "test"  # 其他字段不变

    def test_utc_dict_missing_field(self):
        """字段不存在时应不报错"""
        d = {"name": "test"}
        result = _utc_dict(d, "timestamp")
        assert "timestamp" not in result

    def test_utc_dict_empty_dict(self):
        """空字典应返回空字典"""
        assert _utc_dict({}, "timestamp") == {}

    def test_utc_dict_none_value(self):
        """None 值应返回 None"""
        assert _utc_dict(None, "timestamp") is None

    def test_utc_dict_empty_string_field(self):
        """空字符串字段应返回空字符串（修复 P0-①）"""
        d = {"timestamp": "", "name": "test"}
        result = _utc_dict(d, "timestamp")
        assert result["timestamp"] == ""

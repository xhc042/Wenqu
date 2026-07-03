"""
P1-③ 测试: config.get_generation_concurrency + 默认值

覆盖：
1. 默认返回 DEFAULT_GENERATION_CONCURRENCY (3)
2. override=None 时回退到默认值
3. override 越界自动 clamp 到 [MIN, MAX]
4. 非整数 override 走默认值
"""
from config import (
    get_generation_concurrency,
    DEFAULT_GENERATION_CONCURRENCY,
    MAX_GENERATION_CONCURRENCY,
    MIN_GENERATION_CONCURRENCY,
)


def test_get_generation_concurrency_default():
    """不传 override 应返回默认并发度"""
    assert get_generation_concurrency() == DEFAULT_GENERATION_CONCURRENCY


def test_get_generation_concurrency_none():
    """override=None 应回退到默认"""
    assert get_generation_concurrency(None) == DEFAULT_GENERATION_CONCURRENCY


def test_get_generation_concurrency_within_range():
    """合法范围内 override 原样返回"""
    assert get_generation_concurrency(1) == 1
    assert get_generation_concurrency(3) == 3
    assert get_generation_concurrency(6) == 6


def test_get_generation_concurrency_clamp_low():
    """override 小于 MIN 提升到 MIN"""
    assert get_generation_concurrency(0) == MIN_GENERATION_CONCURRENCY
    assert get_generation_concurrency(-5) == MIN_GENERATION_CONCURRENCY


def test_get_generation_concurrency_clamp_high():
    """override 大于 MAX 降到 MAX"""
    assert get_generation_concurrency(100) == MAX_GENERATION_CONCURRENCY
    assert get_generation_concurrency(99) == MAX_GENERATION_CONCURRENCY


def test_get_generation_concurrency_invalid_type():
    """非整数 override 走默认值"""
    assert get_generation_concurrency("abc") == DEFAULT_GENERATION_CONCURRENCY
    assert get_generation_concurrency(None) == DEFAULT_GENERATION_CONCURRENCY
    assert get_generation_concurrency([1, 2]) == DEFAULT_GENERATION_CONCURRENCY
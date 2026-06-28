# 问渠 v1.1 - 单元测试指南

## 快速开始

### 安装测试依赖

```bash
pip install -r requirements-test.txt
```

### 运行所有测试

```bash
pytest tests/ -v
```

### 运行特定测试文件

```bash
pytest tests/test_chunker.py -v
pytest tests/test_state_machine.py -v
pytest tests/test_database.py -v
pytest tests/test_config.py -v
pytest tests/test_llm_client.py -v
```

### 查看测试覆盖率

```bash
pytest tests/ -v --cov=. --cov-report=html
```

打开 `htmlcov/index.html` 查看覆盖率报告。

### 运行快速测试集（跳过慢速测试）

```bash
pytest tests/ -v -m "not slow"
```

## 测试架构

### 目录结构

```
tests/
├── __init__.py              # 测试包标识
├── conftest.py              # 全局 fixtures (临时数据库、配置覆盖)
├── test_chunker.py          # 分块算法测试 (P0 - 纯函数，最可靠)
├── test_state_machine.py    # 状态机测试 (P0 - 纯逻辑)
├── test_config.py           # 配置测试 (P1)
├── test_llm_client.py       # LLM客户端测试 (P1 - 使用mock)
└── test_database.py         # 数据库测试 (P2 - 使用临时SQLite)
```

### Fixture 说明

| Fixture | 作用 | 自动使用 |
|---------|------|----------|
| `_temp_db` | 为每个测试创建临时SQLite数据库 | ✅ 是 |
| `temp_upload_dir` | 提供临时上传目录 | ❌ 需显式请求 |
| `temp_data_dir` | 提供临时wenqu_data目录 | ❌ 需显式请求 |
| `sample_text` | 示例中文文本 | ❌ 需显式请求 |
| `sample_config_overrides` | 测试配置覆盖 | ❌ 需显式请求 |

### 测试原则

1. **隔离性**: 每个测试独立运行，互不影响
2. **临时数据**: 所有测试使用临时目录，不污染开发环境
3. **Mock外部依赖**: LLM API调用使用mock模拟
4. **命名规范**: 测试函数以 `test_` 开头
5. **断言清晰**: 使用明确的assert语句，便于调试

## 新增测试指南

### 添加新测试文件

1. 在 `tests/` 目录下创建 `test_<module>.py`
2. 使用 `pytest` 类和 `test_` 前缀方法
3. 需要数据库时添加 `db_setup` fixture参数
4. 需要配置覆盖时添加 `sample_config_overrides` fixture参数

### 示例

```python
import pytest

def test_new_feature():
    """测试新功能"""
    assert True  # 替换为实际断言

class TestNewModule:
    """测试新模块"""
    
    def test_something(self, sample_text):
        """使用sample_text fixture"""
        assert len(sample_text) > 0
```

## CI/CD 集成

### GitHub Actions 示例

```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - run: pip install -r requirements.txt
      - run: pip install -r requirements-test.txt
      - run: pytest tests/ -v --cov=. --cov-report=xml
      - uses: codecov/codecov-action@v3
```

## 测试覆盖率目标

| 模块 | 当前 | 目标 |
|------|------|------|
| chunker.py | ~0% | 90%+ |
| state_machine.py | ~0% | 95%+ |
| config.py | ~0% | 80%+ |
| llm_client.py | ~0% | 70%+ |
| database.py | ~0% | 75%+ |
| app.py | ~0% | 50%+ (集成测试) |

## 常见问题

### Q: 测试失败提示数据库锁定？
A: 确保使用了 `_temp_db` fixture，它会自动为每个测试创建临时数据库。

### Q: 如何测试需要真实API调用的代码？
A: 使用 `@patch` 装饰器或 `unittest.mock` 模拟HTTP请求。参见 `test_llm_client.py`。

### Q: 如何调试失败的测试？
A: 使用 `pytest -v -s` 查看详细输出，使用 `pytest --tb=long` 查看完整堆栈。

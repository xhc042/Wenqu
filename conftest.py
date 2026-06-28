"""
问渠 v1.1 - 测试配置文件
"""
import os
import tempfile
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def _temp_db(monkeypatch, tmp_path):
    """
    全局 fixture: 为每个测试自动创建临时 SQLite 数据库目录.
    覆盖 DB_PATH 以避免污染开发数据库.
    """
    db_path = tmp_path / "wenqu.db"
    # 关键: database.py 顶部 `from config import DB_PATH` 是导入时绑定,
    # 改 config.DB_PATH 不会影响 database.DB_PATH 已绑定的引用.
    # 必须同时 patch database.DB_PATH.
    import database
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    yield


@pytest.fixture
def temp_upload_dir(monkeypatch, tmp_path):
    """为需要 UPLOAD_DIR 的测试提供临时上传目录."""
    upload_dir = str(tmp_path / "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    monkeypatch.setattr("config.UPLOAD_DIR", upload_dir)
    return upload_dir


@pytest.fixture
def temp_data_dir(monkeypatch, tmp_path):
    """提供临时 wenqu_data 目录."""
    data_dir = str(tmp_path / "wenqu_data")
    os.makedirs(data_dir, exist_ok=True)
    monkeypatch.setattr("config.DATA_DIR", data_dir)
    return data_dir


@pytest.fixture
def sample_text():
    """提供一段示例中文文本用于分块测试."""
    return (
        "问渠那得清如许？为有源头活水来。\n\n"
        "这是一段关于软件测试的长文本。单元测试是软件开发中的重要环节，"
        "它可以帮助开发者在代码提交之前发现潜在的bug。通过编写测试用例，"
        "我们可以验证代码的行为是否符合预期。\n\n"
        "在Python中，pytest是最流行的测试框架之一。它提供了丰富的断言功能，"
        "fixtures机制以及插件生态系统。使用pytest可以大大提升测试编写的效率。\n\n"
        "除了单元测试，测试还包括集成测试、端到端测试等不同层次。"
        "每一层测试都有其独特的价值和作用。良好的测试覆盖率可以保证代码质量，"
        "减少回归错误的发生概率。"
    )


@pytest.fixture
def sample_config_overrides(monkeypatch):
    """提供临时配置覆盖，避免使用真实的API密钥."""
    monkeypatch.setattr("config.OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setattr("config.OPENAI_BASE_URL", "http://localhost:9999/test")
    monkeypatch.setattr("config.DEFAULT_MODEL", "gpt-4o-mini")
    monkeypatch.setattr("config.MAX_TOKENS", 512)
    monkeypatch.setattr("config.TEMPERATURE", 0.0)
    yield

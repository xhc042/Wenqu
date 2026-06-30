"""
测试 LLM 配置检查功能
确保所有 LLM 调用都检查配置，没有模型时提示用户
"""
import pytest
import asyncio
from pathlib import Path
import sys
import tempfile

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))


@pytest.mark.asyncio
async def test_llm_client_without_config():
    """测试没有配置模型时，LLMClient 的行为"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import database as db
        original_db = db.DB_PATH
        db.DB_PATH = Path(tmpdir) / "test.db"
        db.init_db()
        
        try:
            # 不要使用全局 llm 单例，创建全新的 LLMClient
            # 传入空配置的参数来测试
            from llm_client import LLMClient
            
            # 创建新的客户端，直接传入空配置
            client = LLMClient(tier="balanced")
            
            # 调用 chat 应该返回错误提示
            messages = [{"role": "user", "content": "测试"}]
            result = await client.chat(messages)
            
            # 检查返回的警告信息
            assert "尚未配置模型" in result or "API密钥" in result, f"Expected model not configured message, got: {result}"
        finally:
            db.DB_PATH = original_db


@pytest.mark.asyncio
async def test_llm_client_stream_without_config():
    """测试没有配置模型时，流式对话的行为"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import database as db
        original_db = db.DB_PATH
        db.DB_PATH = Path(tmpdir) / "test.db"
        db.init_db()
        
        try:
            from llm_client import LLMClient
            
            client = LLMClient(tier="balanced")
            
            messages = [{"role": "user", "content": "测试"}]
            
            # 收集所有输出
            outputs = []
            async for chunk in client.chat_stream(messages):
                outputs.append(chunk)
            
            # 第一个输出应该是错误提示
            assert outputs, "No output received"
            assert "尚未配置模型" in outputs[0] or "API密钥" in outputs[0], f"Expected warning, got: {outputs[0]}"
        finally:
            db.DB_PATH = original_db


@pytest.mark.asyncio
async def test_llm_chat_json_without_config():
    """测试没有配置模型时，chat_json 的行为"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import database as db
        original_db = db.DB_PATH
        db.DB_PATH = Path(tmpdir) / "test.db"
        db.init_db()
        
        try:
            from llm_client import LLMClient
            
            client = LLMClient(tier="balanced")
            
            messages = [{"role": "user", "content": "测试"}]
            result = await client.chat_json(messages)
            
            # 应该返回默认的 thinking 结构
            assert result.get("status") == "thinking", f"Expected status=thinking, got: {result}"
            assert result.get("items") == [], f"Expected empty items, got: {result}"
        finally:
            db.DB_PATH = original_db


@pytest.mark.asyncio
async def test_llm_client_with_config():
    """测试配置模型后，LLMClient 的行为"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import database as db
        original_db = db.DB_PATH
        db.DB_PATH = Path(tmpdir) / "test.db"
        db.init_db()
        
        try:
            # 导入 LLMClient
            from llm_client import LLMClient
            
            # 添加测试 provider
            provider_id = db.add_llm_provider(
                name="Test Provider",
                base_url="https://api.example.com",
                api_key="test-key"
            )
            
            # 添加测试模型
            model_id = db.add_llm_model(provider_id, "test-model")
            
            # 激活 provider 和 model
            db.set_active_provider(provider_id)
            db.set_active_model(model_id)
            
            client = LLMClient(tier="balanced")
            assert client.api_key == "test-key", f"Expected test-key, got: {client.api_key}"
            assert client.base_url == "https://api.example.com", f"Expected https://api.example.com, got: {client.base_url}"
            assert client.model == "test-model", f"Expected test-model, got: {client.model}"
        finally:
            db.DB_PATH = original_db


def test_get_llm_config():
    """测试 get_llm_config 函数"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import database as db
        original_db = db.DB_PATH
        db.DB_PATH = Path(tmpdir) / "test.db"
        db.init_db()
        
        try:
            from config import get_llm_config
            
            # 初始状态应该返回空配置
            cfg = get_llm_config()
            # 检查配置中包含必要字段
            assert "api_key" in cfg, f"Expected api_key in config: {cfg}"
            assert "base_url" in cfg, f"Expected base_url in config: {cfg}"
            assert "model" in cfg, f"Expected model in config: {cfg}"
        finally:
            db.DB_PATH = original_db


def test_get_model_tier_config():
    """测试 get_model_tier_config 函数"""
    from config import get_model_tier_config
    
    # 测试不同层级
    fast_cfg = get_model_tier_config("fast")
    balanced_cfg = get_model_tier_config("balanced")
    flagship_cfg = get_model_tier_config("flagship")
    
    # 检查配置结构
    for tier, cfg in [("fast", fast_cfg), ("balanced", balanced_cfg), ("flagship", flagship_cfg)]:
        assert "base_url" in cfg, f"Expected base_url in {tier} config: {cfg}"
        assert "api_key" in cfg, f"Expected api_key in {tier} config: {cfg}"
        assert "model" in cfg, f"Expected model in {tier} config: {cfg}"


@pytest.mark.asyncio
async def test_multi_llm_without_config():
    """测试 MultiModelClient 在没有配置时的行为"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import database as db
        original_db = db.DB_PATH
        db.DB_PATH = Path(tmpdir) / "test.db"
        db.init_db()
        
        try:
            from llm_client import multi_llm
            
            messages = [{"role": "user", "content": "测试"}]
            
            # 调用 balanced 层级
            result = await multi_llm.chat("balanced", messages)
            # 检查返回的错误信息（可能是配置错误或网络错误）
            assert "尚未配置模型" in result or "API密钥" in result or "网络好像有点问题" in result, f"Expected error message, got: {result}"
            
            # 调用 fast 层级
            result = await multi_llm.chat("fast", messages)
            assert "尚未配置模型" in result or "API密钥" in result or "网络好像有点问题" in result, f"Expected error message, got: {result}"
        finally:
            db.DB_PATH = original_db


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

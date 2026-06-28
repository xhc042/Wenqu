"""
P1 测试: llm_client.LLMClient.chat_json 防御逻辑

覆盖：
1. LLM 返回纯数组时的包装处理
2. 响应为空或 ⚠️ 错误前缀的降级
3. JSON 解析失败的兜底
"""
import json
import pytest
from unittest.mock import AsyncMock, patch

from llm_client import LLMClient


@pytest.fixture
def client():
    """不带任何 API key 的客户端（测试网络错误路径）"""
    with patch.dict("os.environ", {
        "WENQU_API_KEY": "sk-test-key",
        "WENQU_BASE_URL": "https://api.deepseek.com",
        "WENQU_MODEL": "deepseek-chat",
    }):
        return LLMClient()


# ==================== chat_json 防御测试 ====================

@pytest.mark.asyncio
async def test_chat_json_returns_array_becomes_items(client):
    """
    场景：LLM 返回纯数组 ["ETF", "指数基金"]
    期望：chat_json 将其包装为 {"status": "thinking", "items": [...]}，
          后续 extract_chapter_snapshot 会从中提取 keywords
    """
    messages = [{"role": "user", "content": "用关键词描述"}]

    # 模拟 chat 方法返回 JSON 数组字符串
    with patch.object(client, "chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = '["ETF", "指数基金", "分散投资"]'

        result = await client.chat_json(messages)

    assert result["status"] == "thinking"
    assert result["items"] == ["ETF", "指数基金", "分散投资"]


@pytest.mark.asyncio
async def test_chat_json_empty_response_returns_empty_items(client):
    """
    场景：LLM 返回空字符串（网络超时 / API 限流）
    期望：返回空结构的降级响应
    """
    messages = [{"role": "user", "content": "描述"}]

    with patch.object(client, "chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = ""

        result = await client.chat_json(messages)

    assert result == {"status": "thinking", "items": []}


@pytest.mark.asyncio
async def test_chat_json_warning_prefix_returns_empty_items(client):
    """
    场景：chat 方法返回 "⚠️ API错误" 等警告字符串
    期望：视为失败，返回空结构
    """
    messages = [{"role": "user", "content": "描述"}]

    with patch.object(client, "chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = "⚠️ API错误"

        result = await client.chat_json(messages)

    assert result == {"status": "thinking", "items": []}


@pytest.mark.asyncio
async def test_chat_json_malformed_json_returns_empty_items(client):
    """
    场景：LLM 返回无法解析的文本（如纯 Markdown 包裹的 JSON）
    期望：json.JSONDecodeError 被捕获，返回空结构
    """
    messages = [{"role": "user", "content": "描述"}]

    with patch.object(client, "chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = "以下是JSON：```json\n{\"key\": \"value\"}\n```"

        result = await client.chat_json(messages)

    assert result == {"status": "thinking", "items": []}


@pytest.mark.asyncio
async def test_chat_json_valid_object_returns_unchanged(client):
    """
    场景：LLM 返回正常的 JSON 对象
    期望：原样返回，不做任何修改
    """
    messages = [{"role": "user", "content": "描述"}]
    valid_json = json.dumps({"keywords": ["ETF"], "core_viewpoint": "可交易基金", "importance": 3})

    with patch.object(client, "chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = valid_json

        result = await client.chat_json(messages)

    assert result == {"keywords": ["ETF"], "core_viewpoint": "可交易基金", "importance": 3}


@pytest.mark.asyncio
async def test_chat_json_nested_array_becomes_items(client):
    """
    场景：LLM 返回嵌套数组（如 [[1,2], [3,4]]）
    期望：包装为 {"status": "thinking", "items": [[1,2], [3,4]]}
    """
    messages = [{"role": "user", "content": "描述"}]

    with patch.object(client, "chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = '[["ETF", "指数"], ["债券", "股票"]]'

        result = await client.chat_json(messages)

    assert result["status"] == "thinking"
    assert result["items"] == [["ETF", "指数"], ["债券", "股票"]]
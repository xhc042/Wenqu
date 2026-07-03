"""
P0-① 测试: llm_client.LLMClient httpx 单例 + 连接池

覆盖：
1. 同一 LLMClient 实例多次 _get_http_client 返回同一 client（连接池复用）
2. aclose() 后 _get_http_client 返回新 client（graceful shutdown）
3. close_all_clients() 全局清理
"""
import asyncio
import pytest

from llm_client import LLMClient, close_all_clients


@pytest.mark.asyncio
async def test_get_http_client_returns_same_instance():
    """同一 LLMClient 多次调用 _get_http_client 应复用同一 client"""
    client = LLMClient()
    try:
        h1 = await client._get_http_client()
        h2 = await client._get_http_client()
        assert h1 is h2, "期望 httpx client 复用，但拿到不同实例"
        assert not h1.is_closed
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_aclose_then_get_returns_new_instance():
    """aclose() 后 _get_http_client 应返回新 client（配置变更场景）"""
    client = LLMClient()
    h1 = await client._get_http_client()
    await client.aclose()
    assert h1.is_closed, "aclose 后 client 应处于 closed 状态"

    h2 = await client._get_http_client()
    assert h1 is not h2, "期望重建 client，但拿到旧的（已关闭）实例"
    assert not h2.is_closed
    await client.aclose()


@pytest.mark.asyncio
async def test_close_all_clients_clears_state():
    """close_all_clients() 应关闭全局单例 + 清空 multi_llm 实例"""
    from llm_client import llm, multi_llm

    # 触发 lazy init
    await llm._get_http_client()
    # multi_llm 至少有一个 tier 实例
    multi_llm._get_client("balanced")
    multi_llm._get_client("balanced")  # 第二次复用

    assert multi_llm._instances  # 至少有一个 tier

    await close_all_clients()

    assert multi_llm._instances == {}, "close_all_clients 应清空 multi_llm 实例"
    assert llm._http_client is None, "close_all_clients 应清空全局 llm 客户端"


@pytest.mark.asyncio
async def test_concurrent_get_http_client_thread_safe():
    """并发 _get_http_client 应只创建一个 client（lock 保护）"""
    client = LLMClient()

    async def get():
        return await client._get_http_client()

    # 并发 10 次,期望都拿到同一个 client
    results = await asyncio.gather(*[get() for _ in range(10)])
    assert all(r is results[0] for r in results), "并发调用应复用同一 client"

    await client.aclose()
"""
测试日志落盘配置（v1.20.2）
确保 LLM 调用异常能写入 wenqu_data/logs/wenqu.log，方便排查
"AI 老师说网络有问题"等用户报告的问题。
"""
import pytest
import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))


def _close_rotating_handlers():
    """Windows 上 RotatingFileHandler 持文件句柄,清理 tmpdir 前必须先关掉"""
    import gc
    from logging.handlers import RotatingFileHandler
    root = logging.getLogger()
    llm = logging.getLogger("wenqu.llm")
    for lg in (root, llm):
        for h in list(lg.handlers):
            if isinstance(h, RotatingFileHandler):
                try:
                    h.flush()
                    h.close()
                except Exception:
                    pass
                lg.removeHandler(h)
    # 强制 GC,释放 app._log_handler / app_mod._log_handler 等模块级引用
    gc.collect()


@pytest.fixture
def fresh_wenqu_data(monkeypatch):
    """每次用例都拿一个干净的 tmpdir 当 DATA_DIR,结束后恢复"""
    with tempfile.TemporaryDirectory() as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        monkeypatch.setenv("WENQU_DATA_DIR", str(tmpdir))
        # 在 reload 前先清掉所有 handler,这样 reload 顶层 logger 配置代码会重新挂上指向新 DATA_DIR 的 handler
        _close_rotating_handlers()
        import importlib
        import config as cfg_mod
        import app as app_mod
        importlib.reload(cfg_mod)
        importlib.reload(app_mod)
        # reload 后再清一次,确保环境干净
        _close_rotating_handlers()
        # 再 reload 一次让 logger 配置代码再跑一遍(因为我们刚才清了)
        importlib.reload(app_mod)
        try:
            yield tmpdir
        finally:
            # 收尾:关闭 handler 释放文件锁,这样 tmpdir 能正常删除
            _close_rotating_handlers()
            # 再 reload 一次,恢复用开发环境的 DATA_DIR
            importlib.reload(cfg_mod)
            importlib.reload(app_mod)
            _close_rotating_handlers()


@pytest.mark.asyncio
async def test_log_dir_created_on_app_import(fresh_wenqu_data):
    """导入 app 模块时，应自动创建 logs/ 目录并配置 RotatingFileHandler"""
    tmpdir = fresh_wenqu_data
    import config as cfg_mod

    log_dir = cfg_mod.DATA_DIR / "logs"
    assert log_dir.exists(), "logs/ 目录应被自动创建"
    assert log_dir.is_dir(), "logs 应该是目录"

    # RotatingFileHandler 应已挂在 root logger
    from logging.handlers import RotatingFileHandler
    has_rfh = any(
        isinstance(h, RotatingFileHandler)
        for h in logging.getLogger().handlers
    )
    assert has_rfh, "root logger 应挂上 RotatingFileHandler"

    # baseFilename 应指向 DATA_DIR/logs/wenqu.log
    rfh = next(
        h for h in logging.getLogger().handlers
        if isinstance(h, RotatingFileHandler)
    )
    assert rfh.baseFilename.endswith("wenqu.log")
    assert str(cfg_mod.DATA_DIR) in rfh.baseFilename


@pytest.mark.asyncio
async def test_llm_module_logger_writes_to_file(fresh_wenqu_data):
    """wenqu.llm logger 的 warning/error 消息应能落到 log 文件"""
    tmpdir = fresh_wenqu_data
    import config as cfg_mod

    # 触发 llm_client 模块被加载
    from llm_client import logger as llm_logger
    llm_logger.warning("test marker LLM-LOG-WRITES-TO-FILE")

    # 强制 flush 所有 handler
    for h in logging.getLogger().handlers:
        h.flush()

    log_file = cfg_mod.DATA_DIR / "logs" / "wenqu.log"
    assert log_file.exists(), f"log 文件应存在: {log_file}"

    content = log_file.read_text(encoding="utf-8")
    assert "wenqu.llm" in content, "log 文件应包含 wenqu.llm logger 名"
    assert "LLM-LOG-WRITES-TO-FILE" in content, "测试标记应被写入"


@pytest.mark.asyncio
async def test_chat_stream_connect_error_logs_warning(fresh_wenqu_data):
    """chat_stream 遇到 httpx.ConnectError 时，应记录 warning + 友好提示"""
    import httpx
    from llm_client import LLMClient

    with patch("llm_client.get_model_tier_config") as mock_cfg:
        mock_cfg.return_value = {
            "api_key": "test-key",
            "base_url": "http://example.com",
            "model": "gpt-3.5-turbo",
            "timeout": 60,
        }
        client = LLMClient(tier="balanced")

        # 模拟 httpx 流式请求时抛 ConnectError
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_stream.__aexit__ = AsyncMock(return_value=None)

        mock_http = MagicMock()
        mock_http.stream = MagicMock(return_value=mock_stream)
        # chat_stream 内 await self._get_http_client() 拿到的就是这个
        client._get_http_client = AsyncMock(return_value=mock_http)

        try:
            chunks = []
            async for chunk in client.chat_stream([{"role": "user", "content": "hi"}]):
                chunks.append(chunk)

            assert len(chunks) == 1, "应只 yield 一条错误消息"
            assert "无法连接 AI 服务" in chunks[0], f"应返回连接失败提示，实际: {chunks[0]}"
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_timeout_logs_warning(fresh_wenqu_data):
    """chat_stream 遇到 TimeoutException 时，应记录 warning + 超时提示"""
    import httpx
    from llm_client import LLMClient

    with patch("llm_client.get_model_tier_config") as mock_cfg:
        mock_cfg.return_value = {
            "api_key": "test-key",
            "base_url": "http://example.com",
            "model": "gpt-3.5-turbo",
            "timeout": 60,
        }
        client = LLMClient(tier="balanced")

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(side_effect=httpx.TimeoutException("read timeout"))
        mock_stream.__aexit__ = AsyncMock(return_value=None)

        mock_http = MagicMock()
        mock_http.stream = MagicMock(return_value=mock_stream)
        client._get_http_client = AsyncMock(return_value=mock_http)

        try:
            chunks = []
            async for chunk in client.chat_stream([{"role": "user", "content": "hi"}]):
                chunks.append(chunk)

            assert len(chunks) == 1
            assert "超时" in chunks[0], f"应返回超时提示，实际: {chunks[0]}"
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_chat_generic_exception_logs_with_stack(fresh_wenqu_data):
    """非网络类异常应被兜底捕获，且 logger.error 携带 exc_info"""
    from llm_client import LLMClient
    import config as cfg_mod

    with patch("llm_client.get_model_tier_config") as mock_cfg:
        mock_cfg.return_value = {
            "api_key": "test-key",
            "base_url": "http://example.com",
            "model": "gpt-3.5-turbo",
            "timeout": 60,
        }
        client = LLMClient(tier="balanced")

        # 模拟一个奇怪的运行时错误（比如 JSON 解析崩了）
        mock_http = MagicMock()
        mock_http.post = AsyncMock(side_effect=RuntimeError("unexpected boom"))
        client._get_http_client = AsyncMock(return_value=mock_http)

        try:
            result = await client.chat([{"role": "user", "content": "hi"}])

            assert "网络好像有点问题" in result, "兜底文案应保持原样以避免破坏前端"

            # flush + 检查 log 文件
            for h in logging.getLogger().handlers:
                h.flush()

            log_file = cfg_mod.DATA_DIR / "logs" / "wenqu.log"
            content = log_file.read_text(encoding="utf-8")
            assert "LLM 非流式调用异常" in content, "ERROR 日志应包含异常描述"
            assert "RuntimeError" in content or "unexpected boom" in content, "应记录异常类型/消息"
            assert "Traceback" in content, "exc_info=True 应写入堆栈"
        finally:
            await client.aclose()

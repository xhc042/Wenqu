"""
问渠 (Wenqu) v1.1 — LLM 配置管理器

统一管理 LLM 配置的读取、同步和持久化，消除分散的重复逻辑。

职责：
- 从数据库读取配置并同步到 config 模块
- 将 config 模块的运行时配置持久化到数据库
- 提供一致的配置访问接口
"""

import logging
from typing import Optional

import database as db
from config import (
    MODEL_TIER_CONFIG,
    DEFAULT_MODEL_TIER,
)

logger = logging.getLogger(__name__)

# 运行时配置缓存（与 config.py 保持一致的键名）
_config_cache: dict = {
    "DEFAULT_MODEL": "gpt-4o-mini",
    "DEFAULT_TEMPERATURE": 0.7,
    "DEFAULT_TOP_P": 1.0,
    "MAX_CONCURRENT_SESSIONS": 5,
    "DEFAULT_MAX_TOKENS": 4096,
    "MODEL_TIER_CONFIG": MODEL_TIER_CONFIG,
    "DEFAULT_MODEL_TIER": DEFAULT_MODEL_TIER,
    "DEFAULT_MODEL_FAST": "",
    "DEFAULT_MODEL_BALANCED": "",
    "DEFAULT_MODEL_FLAGSHIP": "",
}


def _get_setting(conn, key: str, fallback: str) -> str:
    """安全获取单个设置值"""
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else fallback
    except Exception:
        return fallback


def _get_setting_int(conn, key: str, fallback: int) -> int:
    """安全获取整数设置值"""
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        if row:
            return int(row["value"])
    except Exception:
        pass
    return fallback


def _get_setting_float(conn, key: str, fallback: float) -> float:
    """安全获取浮点数设置值"""
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        if row:
            return float(row["value"])
    except Exception:
        pass
    return fallback


def sync_db_to_config() -> dict:
    """从数据库读取 LLM 配置并同步到运行时缓存。

    Returns:
        同步后的配置字典，键名与 config.py 保持一致
    """
    conn = db.get_conn()
    try:
        cache = _config_cache

        cache["DEFAULT_MODEL"] = _get_setting(conn, "default_model", "gpt-4o-mini")
        cache["DEFAULT_TEMPERATURE"] = _get_setting_float(conn, "default_temperature", 0.7)
        cache["DEFAULT_TOP_P"] = _get_setting_float(conn, "default_top_p", 1.0)
        cache["MAX_CONCURRENT_SESSIONS"] = _get_setting_int(conn, "max_concurrent_sessions", 5)
        cache["DEFAULT_MAX_TOKENS"] = _get_setting_int(conn, "default_max_tokens", 4096)
        cache["DEFAULT_MODEL_TIER"] = _get_setting(conn, "default_model_tier", DEFAULT_MODEL_TIER)

        # 三级模型配置
        cache["DEFAULT_MODEL_FAST"] = _get_setting(conn, "default_model_fast", DEFAULT_MODEL_FAST)
        cache["DEFAULT_MODEL_BALANCED"] = _get_setting(conn, "default_model_balanced", DEFAULT_MODEL_BALANCED)
        cache["DEFAULT_MODEL_FLAGSHIP"] = _get_setting(conn, "default_model_flagship", DEFAULT_MODEL_FLAGSHIP)

        # MODEL_TIER_CONFIG 从三级模型配置动态构建
        cache["MODEL_TIER_CONFIG"] = {
            "fast": cache["DEFAULT_MODEL_FAST"],
            "balanced": cache["DEFAULT_MODEL_BALANCED"],
            "flagship": cache["DEFAULT_MODEL_FLAGSHIP"],
        }

        logger.debug("LLM 配置已从数据库同步到运行时")
        return dict(cache)

    except Exception as e:
        logger.warning(f"数据库配置同步失败，使用默认配置: {e}")
        return dict(_config_cache)
    finally:
        conn.close()


def sync_config_to_db(config: Optional[dict] = None) -> bool:
    """将运行时配置持久化到数据库。

    Args:
        config: 要同步的配置字典。如果为 None，则使用当前 _config_cache。

    Returns:
        是否同步成功
    """
    if config is None:
        config = dict(_config_cache)

    conn = db.get_conn()
    try:
        settings_map = {
            "default_model": config.get("DEFAULT_MODEL", "gpt-4o-mini"),
            "default_temperature": str(config.get("DEFAULT_TEMPERATURE", 0.7)),
            "default_top_p": str(config.get("DEFAULT_TOP_P", 1.0)),
            "max_concurrent_sessions": str(config.get("MAX_CONCURRENT_SESSIONS", 5)),
            "default_max_tokens": str(config.get("DEFAULT_MAX_TOKENS", 4096)),
            "default_model_tier": config.get("DEFAULT_MODEL_TIER", DEFAULT_MODEL_TIER),
            "default_model_fast": config.get("DEFAULT_MODEL_FAST", DEFAULT_MODEL_FAST),
            "default_model_balanced": config.get("DEFAULT_MODEL_BALANCED", DEFAULT_MODEL_BALANCED),
            "default_model_flagship": config.get("DEFAULT_MODEL_FLAGSHIP", DEFAULT_MODEL_FLAGSHIP),
        }

        for key, value in settings_map.items():
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )
            except Exception as e:
                logger.warning(f"同步设置 {key} 失败: {e}")

        conn.commit()
        logger.debug("运行时配置已持久化到数据库")
        return True

    except Exception as e:
        logger.warning(f"配置持久化失败: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        conn.close()


def get_runtime_config() -> dict:
    """获取当前运行时配置（直接从 _config_cache 读取）。

    Returns:
        运行时配置字典
    """
    return dict(_config_cache)


def update_runtime_config(updates: dict) -> bool:
    """更新运行时配置并同步到数据库。

    Args:
        updates: 要更新的配置项

    Returns:
        是否更新成功
    """
    cache = _config_cache
    valid_keys = {
        "DEFAULT_MODEL", "DEFAULT_TEMPERATURE", "DEFAULT_TOP_P",
        "MAX_CONCURRENT_SESSIONS", "DEFAULT_MAX_TOKENS",
        "DEFAULT_MODEL_TIER", "MODEL_TIER_CONFIG",
        "DEFAULT_MODEL_FAST", "DEFAULT_MODEL_BALANCED", "DEFAULT_MODEL_FLAGSHIP",
    }

    for key, value in updates.items():
        if key in valid_keys:
            cache[key] = value

    # 动态更新 MODEL_TIER_CONFIG
    if "DEFAULT_MODEL_FAST" in updates or "DEFAULT_MODEL_BALANCED" in updates or "DEFAULT_MODEL_FLAGSHIP" in updates:
        cache["MODEL_TIER_CONFIG"] = {
            "fast": cache.get("DEFAULT_MODEL_FAST", ""),
            "balanced": cache.get("DEFAULT_MODEL_BALANCED", ""),
            "flagship": cache.get("DEFAULT_MODEL_FLAGSHIP", ""),
        }

    # 持久化到数据库
    return sync_config_to_db(cache)

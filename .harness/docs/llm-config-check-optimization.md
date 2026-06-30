# LLM 配置检查优化方案

## 问题描述

项目中存在以下问题：

1. **环境变量依赖**：之前使用环境变量 `WENQU_API_KEY` 等配置 LLM，不符合"统一从配置界面配置"的设计原则
2. **配置未同步**：`llm_config_manager.py` 中的 `sync_db_to_config()` 函数没有正确更新 `config.LLM_CONFIG`
3. **缺失懒加载函数**：`llm_client.py` 中调用了 `_try_load_active_model_from_db()` 但函数未定义
4. **缺少 settings 表**：`database.py` 中缺少 `settings` 表，导致配置无法持久化
5. **模型缺失检查不足**：LLM 调用时没有明确提示用户先配置模型

## 修复内容

### 1. 添加缺失的 `settings` 表（database.py）

在 `init_db()` 函数中添加以下表结构：

```sql
-- settings 表用于存储 LLM 配置
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    value TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 2. 修复 `_try_load_active_model_from_db` 函数（llm_client.py）

添加懒加载函数，从数据库读取激活的模型配置：

```python
def _try_load_active_model_from_db(client):
    """从数据库加载激活的模型配置（懒加载）"""
    try:
        active = db.get_active_model_with_provider()
        if active:
            client.api_key = active.get("api_key", "")
            client.base_url = active.get("base_url", "").rstrip("/")
            client.model = active.get("model_name", "")
    except Exception as e:
        # 懒加载失败不阻塞主流程
        pass
```

### 3. 修复 `get_llm_config()` 函数（config.py）

确保每次调用时都同步数据库配置：

```python
def get_llm_config():
    """获取当前LLM配置（从数据库读取激活的 provider/model）"""
    # 先同步数据库配置到 LLM_CONFIG
    try:
        from modules.llm_config_manager import sync_db_to_config
        sync_db_to_config()
    except Exception:
        pass
    
    cfg = dict(LLM_CONFIG)
    # 检查是否有有效配置
    if not cfg.get("model"):
        cfg["model"] = ""
    if not cfg.get("base_url"):
        cfg["base_url"] = ""
    if not cfg.get("api_key"):
        cfg["api_key"] = ""
    return cfg
```

### 4. 修复 `sync_db_to_config()` 函数（llm_config_manager.py）

确保正确从数据库读取并更新 `LLM_CONFIG`：

```python
def sync_db_to_config() -> dict:
    """从数据库读取 LLM 配置并同步到运行时缓存和 config.LLM_CONFIG。"""
    conn = db.get_conn()
    try:
        cache = _config_cache
        
        # ... 省略其他配置读取 ...
        
        # 同步到 config.py 的 LLM_CONFIG（用于 llm 单例）
        # 从数据库获取激活的 provider/model
        active = db.get_active_model_with_provider()
        if active:
            from config import LLM_CONFIG
            LLM_CONFIG["api_key"] = active.get("api_key", "")
            LLM_CONFIG["base_url"] = active.get("base_url", "")
            LLM_CONFIG["model"] = active.get("model_name", "")
        
        return dict(cache)
    finally:
        conn.close()
```

### 5. 修复 `_sync_llm_from_db()` 函数（app.py）

确保在没有激活模型时清空配置：

```python
def _sync_llm_from_db():
    """将数据库活跃配置同步到 llm 单例和 LLM_CONFIG（统一入口）"""
    active = db.get_active_model_with_provider()
    if active:
        LLM_CONFIG["api_key"] = active["api_key"]
        LLM_CONFIG["base_url"] = active["base_url"]
        LLM_CONFIG["model"] = active["model_name"]
        llm.api_key = active["api_key"]
        llm.base_url = active["base_url"].rstrip("/")
        llm.model = active["model_name"]
    else:
        # 如果没有激活的模型，清空配置并提示用户
        LLM_CONFIG["api_key"] = ""
        LLM_CONFIG["base_url"] = ""
        LLM_CONFIG["model"] = ""
        llm.api_key = ""
        llm.base_url = ""
        llm.model = ""
```

## 优化效果

### 1. 所有 LLM 调用都检查配置

- `llm.chat()`: 检查 `api_key`，未配置返回 `"⚠️ 尚未配置模型，请在设置中添加并激活一个 LLM 提供商和模型。"`
- `llm.chat_stream()`: 同上，但以流式方式输出
- `llm.chat_json()`: 调用 `chat()`，继承相同检查逻辑

### 2. 多模型分级支持

- `fast` 层级：用于快速任务（目录提取、摘要等）
- `balanced` 层级：用于常规对话
- `flagship` 层级：用于深度研读

每个层级都独立检查配置，未配置时提示用户。

### 3. 明确的用户提示

所有 LLM 调用失败时，优先返回明确的提示信息：

```
⚠️ 尚未配置模型，请在设置中添加并激活一个 LLM 提供商和模型。
```

## 测试覆盖

新增测试文件 `tests/test_llm_config_check.py`，包含以下测试：

1. `test_llm_client_without_config`: 测试未配置时的行为
2. `test_llm_client_stream_without_config`: 测试流式对话未配置时的行为
3. `test_llm_chat_json_without_config`: 测试 JSON 返回未配置时的行为
4. `test_llm_client_with_config`: 测试配置后的行为
5. `test_get_llm_config`: 测试配置读取
6. `test_get_model_tier_config`: 测试分层配置读取
7. `test_multi_llm_without_config`: 测试多模型客户端

## 使用说明

### 配置模型

1. 进入"设置"页面
2. 点击"添加 LLM 提供商"
3. 填写 Name、Base URL、API Key
4. 点击"添加模型"
5. 激活提供商和模型

### 检查配置

调用任意需要 LLM 的功能时，如果未配置模型，会看到：

```
⚠️ 尚未配置模型，请在设置中添加并激活一个 LLM 提供商和模型。
```

### 配置验证

所有 LLM 调用都会自动检查配置，确保：

1. 优先使用配置界面设置的模型
2. 不使用环境变量中的模型
3. 配置缺失时给出明确提示
4. 懒加载机制确保及时同步最新配置

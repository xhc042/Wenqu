"""问渠 v1.1 - 快速启动脚本（已配置LLM API）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 配置API
from config import LLM_CONFIG
LLM_CONFIG["api_key"] = "sk-fb050ba6f820432db5be8d6e677c6835"
LLM_CONFIG["base_url"] = "https://api.deepseek.com"
LLM_CONFIG["model"] = "deepseek-chat"

from app import app
import uvicorn

print("=" * 50)
print("  Wenqu v1.1")
print(f"  Model: {LLM_CONFIG['model']}")
print(f"  API: {LLM_CONFIG['base_url']}")
print(f"  API Key: {'OK' if LLM_CONFIG['api_key'] else 'MISSING'}")
print("=" * 50)
print("  Visit: http://127.0.0.1:8765")
print("=" * 50)

uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")

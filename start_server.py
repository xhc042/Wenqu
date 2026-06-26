"""问渠 v1.1 - 快速启动脚本（需手动配置API）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app import app
import uvicorn

print("=" * 50)
print("  Wenqu v1.1")
print("=" * 50)
print("  Visit: http://127.0.0.1:8765")
print("=" * 50)

uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")

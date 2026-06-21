"""
启动问渠 v1.1
双击此文件即可启动服务器
"""
import os, sys, subprocess, time

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

print("=" * 50)
print("  Wenqu v1.1")
print("  教材级认知唤醒引擎")
print("=" * 50)
print()

# 检查依赖
try:
    import fastapi
except ImportError:
    print("[*] 首次运行，正在安装依赖...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("[!] 依赖安装失败:", r.stderr[:200])
        input("按回车退出...")
        sys.exit(1)
    print("[+] 依赖安装成功")

# 通过管理脚本启动
from manage_server import cmd_start
if cmd_start():
    import webbrowser
    webbrowser.open("http://127.0.0.1:8765")
    print()
    print("[+] 浏览器已打开")
    print("[提示] 关闭服务器请运行「关闭问渠.py」")
else:
    print("[!] 启动失败")

print()
input("按回车退出...")

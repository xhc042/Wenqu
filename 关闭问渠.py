"""
关闭问渠 v1.1
双击此文件即可关闭服务器
"""
import os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

print("=" * 50)
print("  正在关闭问渠 v1.1...")
print("=" * 50)
print()

from manage_server import cmd_stop
cmd_stop()

print()
print("感谢使用问渠，再见！")
input("按回车退出...")

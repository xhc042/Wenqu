"""
问渠 v1.1 - 服务器管理脚本
支持: python manage_server.py start / python manage_server.py stop
"""
import os, sys, time, subprocess, signal, socket

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 8765
PID_FILE = os.path.join(BASE_DIR, "wenqu_data", "server.pid")


def is_port_open(host, port):
    """检查端口是否开放"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        result = s.connect_ex((host, port))
        s.close()
        return result == 0
    except:
        return False


def find_pid_by_port(port):
    """通过端口查找 PID"""
    try:
        output = subprocess.check_output(
            f'netstat -ano | findstr ":{port}" | findstr "LISTENING"',
            shell=True, text=True, stderr=subprocess.DEVNULL,
        )
        for line in output.strip().split("\n"):
            parts = line.strip().split()
            if parts:
                return parts[-1]
    except:
        pass
    return None


def save_pid(pid):
    """保存 PID"""
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def read_pid():
    """读取 PID"""
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            return f.read().strip()
    return None


def cmd_start():
    """启动服务器"""
    # 先关闭旧进程
    old_pid = find_pid_by_port(PORT)
    if old_pid:
        print(f"[*] 关闭旧进程 PID: {old_pid}")
        subprocess.run(f"taskkill /F /PID {old_pid}", shell=True, capture_output=True)
        time.sleep(2)

    # 启动服务器
    print("[*] 启动问渠 v1.1...")
    script = os.path.join(BASE_DIR, "start_server.py")
    proc = subprocess.Popen(
        [sys.executable, script],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    save_pid(proc.pid)

    # 等待端口就绪
    for i in range(15):
        time.sleep(1)
        if is_port_open(HOST, PORT):
            print(f"[+] 服务器已启动! http://{HOST}:{PORT}")
            return True

    print("[!] 启动超时，请检查 wenqu_data/server.log")
    return False


def cmd_stop():
    """关闭服务器"""
    # 从 PID 文件读取
    pid = read_pid()
    if pid:
        try:
            subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
            print(f"[+] 进程 {pid} 已关闭")
        except:
            pass

    # 再通过端口确认
    port_pid = find_pid_by_port(PORT)
    if port_pid:
        subprocess.run(f"taskkill /F /PID {port_pid}", shell=True, capture_output=True)
        print(f"[+] 端口进程 {port_pid} 已关闭")

    time.sleep(1)
    if is_port_open(HOST, PORT):
        print("[!] 端口仍被占用，请手动检查")
    else:
        print("[+] 问渠已关闭")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "start":
        cmd_start()
    elif action == "stop":
        cmd_stop()
    else:
        print("用法: python manage_server.py [start|stop]")

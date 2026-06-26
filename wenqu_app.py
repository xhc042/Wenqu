"""
Wenqu v1.1 Desktop App Entry
PyInstaller entry point
Double-click exe to start server and open browser.
Closing the console window gracefully shuts down the server.
"""
import os
import sys
import subprocess
import threading
import time
import webbrowser
import signal
import socket


# ==================== Path handling (PyInstaller support) ====================
if getattr(sys, 'frozen', False):
    # --onefile: sys.executable 指向临时目录，用 sys.argv[0] 获取原始 exe 路径
    BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

# Data dir override for exe persistence
if getattr(sys, 'frozen', False):
    os.environ.setdefault("WENQU_DATA_DIR", os.path.join(BASE_DIR, "wenqu_data"))


# ==================== Graceful shutdown handling ====================
_server_holder = [None]  # Mutable container for server reference


def signal_handler(signum, frame):
    """Handle Ctrl+C / SIGTERM"""
    server = _server_holder[0]
    if server:
        server.should_exit = True


# Windows console close (X button) handler
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _console_ctrl_handler = None  # Must keep reference to prevent GC

    def _setup_console_handler():
        global _console_ctrl_handler

        kernel32 = ctypes.windll.kernel32
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def handler_func(dwCtrlType):
            # CTRL_CLOSE_EVENT=0, CTRL_SHUTDOWN_EVENT=1
            if dwCtrlType in (0, 1):
                server = _server_holder[0]
                if server:
                    server.should_exit = True
                # Give server time to shutdown gracefully
                time.sleep(2)
                return True  # We handled it, prevent default handler
            return False

        _console_ctrl_handler = callback_type(handler_func)
        kernel32.SetConsoleCtrlHandler(_console_ctrl_handler, True)

    _setup_console_handler()


# ==================== Port management ====================
def kill_process_on_port(host: str, port: int) -> bool:
    """Kill any process occupying the given port. Returns True if killed."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 5:
                addr = parts[1]
                state = parts[3]
                pid = parts[4]
                if addr == f"{host}:{port}" and state == "LISTENING":
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True, timeout=3,
                            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
                        )
                        print(f"[*] Killed old process (PID {pid}) on port {port}")
                        time.sleep(1)
                        return True
                    except Exception:
                        print(f"[!] Failed to kill PID {pid} on port {port}")
        return False
    except Exception:
        return False


# ==================== Server thread ====================
def start_server():
    """Start uvicorn server and keep reference for shutdown"""
    from app import app
    from config import HOST, PORT
    import uvicorn

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="info")
    server = uvicorn.Server(config)
    _server_holder[0] = server  # Store reference for shutdown
    server.run()
    # Server thread has exited, notify others
    _server_holder[0] = None


# ==================== Main ====================
def main():
    print("=" * 50)
    print("  Wenqu v1.1  -  Textbook Cognitive Engine")
    print("=" * 50)
    print()

    from config import HOST, PORT

    # Register signal handlers for Ctrl+C / kill
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)

    # Check port availability and free it if occupied
    print(f"[*] Checking port {PORT}...")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        result = s.connect_ex((HOST, PORT))
        s.close()
        if result == 0:
            print(f"[!] Port {PORT} is already in use, attempting to free...")
            kill_process_on_port(HOST, PORT)
    except Exception:
        pass

    # Initialize database (auto-create if not exists)
    from database import init_db

    db_path = os.path.join(
        os.environ.get("WENQU_DATA_DIR", BASE_DIR),
        "wenqu.db"
    )
    print(f"[*] Data dir: {os.path.dirname(db_path)}")

    if not os.path.exists(db_path):
        print("[*] First launch detected, initializing database...")
        init_db()
        print("[+] Database initialized")
    else:
        init_db()
        print("[+] Database ready")

    print()

    # Start server in background thread (non-daemon = can clean up)
    print("[*] Starting server...")
    server_thread = threading.Thread(target=start_server, daemon=False)
    server_thread.start()

    # Wait for server to be ready (max 10s)
    server_ready = False
    for i in range(40):
        time.sleep(0.25)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            result = s.connect_ex((HOST, PORT))
            s.close()
            if result == 0:
                server_ready = True
                break
        except Exception:
            pass

    if not server_ready:
        print(f"[!] Server failed to start on {HOST}:{PORT}")
        print("[!] Possible causes: port occupied by another app, or missing dependencies.")
        print()
        input("Press Enter to exit...")
        sys.exit(1)

    url = f"http://{HOST}:{PORT}"
    print(f"[+] Server started: {url}")
    print()

    # Open default browser
    webbrowser.open(url)
    print("[+] Browser opened automatically")
    print()
    print("=" * 50)
    print("  TIP: Close this window or press Ctrl+C to exit")
    print(f"  URL: {url}")
    print("=" * 50)
    print()

    # Block until server stops (closing window / Ctrl+C triggers shutdown)
    server_thread.join()

    print("Server stopped. Thank you for using Wenqu!")


if __name__ == "__main__":
    main()

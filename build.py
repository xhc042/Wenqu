"""
Wenqu v1.1 Desktop App Build Script
Run: python build.py
Output: dist/Wenqu.exe  (single-file executable)
"""
import os
import sys
import subprocess
import shutil


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "Wenqu"
ENTRY_POINT = os.path.join(BASE_DIR, "wenqu_app.py")
DIST_FILE = os.path.join(BASE_DIR, "dist", f"{APP_NAME}.exe")
BUILD_DIR = os.path.join(BASE_DIR, "build")
OLD_DIST_DIR = os.path.join(BASE_DIR, "dist", APP_NAME)


def ensure_pyinstaller():
    try:
        import PyInstaller
        print(f"[OK] PyInstaller {PyInstaller.__version__} installed")
        return True
    except ImportError:
        print("[..] Installing PyInstaller...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            print("[OK] PyInstaller installed")
            return True
        else:
            print(f"[FAIL] PyInstaller install failed: {r.stderr}")
            return False


def build():
    print("=" * 60)
    print("  Wenqu v1.1 Desktop App Build")
    print("=" * 60)
    print()

    if not ensure_pyinstaller():
        sys.exit(1)

    # Clean old builds
    if os.path.exists(BUILD_DIR):
        print(f"[..] Cleaning {BUILD_DIR}...")
        shutil.rmtree(BUILD_DIR)
    # Clean old single-file exe
    if os.path.exists(DIST_FILE):
        os.remove(DIST_FILE)
        print(f"[..] Cleaned old {DIST_FILE}")
    # Clean old --onedir output folder from previous builds
    if os.path.exists(OLD_DIST_DIR):
        shutil.rmtree(OLD_DIST_DIR)
        print(f"[..] Cleaned old {OLD_DIST_DIR}")

    print()
    print("[..] Packaging, please wait (1-3 minutes)...")
    print()

    # Build command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onefile",
        "--noconfirm",
        "--clean",
        "--console",
        "--add-data", f"static{os.pathsep}static",
        "--add-data", f"prompts{os.pathsep}prompts",
    ]

    # Hidden imports for runtime discovery safety
    HIDDEN_IMPORTS = [
        "multipart",          # FastAPI file uploads (python-multipart)
        "config",             # local module
        "database",           # local module
        "chunker",            # local module
        "state_machine",      # local module
        "llm_client",         # local module
        "uvicorn.logging",    # uvicorn submodule
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.middleware.wsgi",
        "uvicorn.supervisors.multiprocess",
        "uvicorn.supervisors.statreload",
    ]
    for mod in HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", mod])

    # Collect all submodules for key packages
    cmd.extend(["--collect-all", "uvicorn"])
    cmd.extend(["--collect-all", "websockets"])

    # Exclude packages not used by the project
    EXCLUDES = [
        "torch", "numpy", "scipy", "pandas", "matplotlib",
        "PIL", "cv2", "pyarrow", "grpc",
        "cryptography", "psycopg2", "sqlalchemy", "redis",
        "tensorflow", "tqdm", "google",
        "aiohttp", "yarl", "multidict",
        "setuptools", "pip", "wheel", "pkg_resources",
        "flask", "django", "notebook", "jupyter",
        "mypy", "pytest", "coverage", "black", "flake8",
        "zmq", "pyzmq", "msgpack",
    ]
    for mod in EXCLUDES:
        cmd.extend(["--exclude-module", mod])

    cmd.append(ENTRY_POINT)

    print("Running PyInstaller...")
    print()
    result = subprocess.run(cmd, cwd=BASE_DIR)

    if result.returncode != 0:
        print(f"\n[FAIL] Build failed (exit code {result.returncode})")
        sys.exit(1)

    print()
    print("[OK] Build complete!")
    print()
    print("=" * 60)
    print("  Output:")
    print(f"    Exe:   {DIST_FILE}")
    print("=" * 60)
    print()
    print("Usage: Double-click Wenqu.exe to launch (first launch may be slow due to self-extract)")
    print("Database and uploads saved in wenqu_data/ (next to exe)")
    print()


if __name__ == "__main__":
    build()

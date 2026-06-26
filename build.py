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
    for d in [BUILD_DIR]:
        if os.path.exists(d):
            print(f"[..] Cleaning {d}...")
            shutil.rmtree(d)
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

    # Collect all uvicorn submodules (version-safe, no need to maintain manual list)
    cmd.extend(["--collect-all", "uvicorn"])

    # Exclude all packages not used by the project — saves ~300MB
    EXCLUDES = [
        "torch", "numpy", "scipy", "pandas", "matplotlib",
        "PIL", "Pillow", "cv2", "pyarrow", "grpc",
        "cryptography", "psycopg2", "sqlalchemy", "redis",
        "tensorflow", "tqdm", "six", "absl", "yaml",
        "google", "fontTools", "kiwisolver", "contourpy",
        "dateutil", "greenlet", "charset_normalizer",
        "aiohttp", "yarl", "multidict", "frozenlist",
        "opentelemetry", "email_validator", "xxhash",
        "simplejson", "wcwidth", "tzdata", "propcache",
        "bcrypt", "watchfiles", "httptools", "zstandard",
        "setuptools", "pip", "wheel", "pkg_resources",
        "numba", "sympy", "networkx", "h5py", "bokeh",
        "flask", "django", "notebook", "jupyter",
        "mypy", "pytest", "coverage", "black", "flake8",
        "PIL._tkinter_finder",
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

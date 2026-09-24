"""Run the local API, worker and Web together; Ctrl+C stops owned processes."""
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_ports():
    for port in (8010, 3060):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"端口 {port} 已被占用。请先停止旧服务；不会终止其他进程。") from None


def stop(processes):
    # Each subprocess owns a new session, including npm's Next.js child.
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    for process in processes:
        try:
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def main():
    if sys.platform == "win32":
        raise SystemExit("此启动方式支持 macOS / Linux；Windows 请使用 WSL2。")
    python = ROOT / ".venv/bin/python"
    if not python.exists() or not (ROOT / ".env").is_file() or not (ROOT / "frontend/node_modules/next").is_dir():
        raise SystemExit("请先运行 python3.11 scripts/setup.py 并创建本地账户。")
    if not shutil.which("npm"):
        raise SystemExit("未找到 npm，请安装 Node.js 22。")
    check_ports()
    # This launcher always runs the documented local ports, not the QA build.
    env = {**os.environ, "HEXING_QA": "0", "WEB_ORIGIN": "http://127.0.0.1:3060"}
    commands = [
        [str(python), "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8010", "--no-access-log", "--no-proxy-headers"],
        [str(python), "-m", "backend.app.worker"],
        ["npm", "--prefix", "frontend", "run", "dev", "--", "--hostname", "127.0.0.1", "--port", "3060"],
    ]
    processes = []
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        for command in commands:
            processes.append(subprocess.Popen(command, cwd=ROOT, env=env, start_new_session=True))
        print("AI-Hexing 启动中。待 Web 显示 Ready 后打开 http://127.0.0.1:3060/tasks；Ctrl+C 停止全部服务。", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        raise SystemExit("某个服务已退出，正在停止本次启动的其他服务。请检查上方日志。")
    except KeyboardInterrupt:
        print("\n正在停止 AI-Hexing…", flush=True)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        stop(processes)
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()

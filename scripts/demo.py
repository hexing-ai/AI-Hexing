"""Isolated interview demo: mock data, dedicated ports, no live provider calls."""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_PORT = 28060
WEB_PORT = 23060
WEB_ORIGIN = f"http://127.0.0.1:{WEB_PORT}"


def check_ports():
    for port in (API_PORT, WEB_PORT):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"演示端口 {port} 已被占用。请先停止旧的演示进程；不会终止其他服务。") from None


def stop(processes):
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


def directory():
    return ROOT / "data" / "demo"


def credentials_path():
    return directory() / "credentials.json"


def prepare(reset):
    python = ROOT / ".venv/bin/python"
    if not python.exists() or not (ROOT / "frontend/node_modules/next").is_dir():
        raise SystemExit("请先运行 python3.11 scripts/setup.py，再启动演示。演示不读取你的 .env。")
    if not shutil.which("npm"):
        raise SystemExit("未找到 npm，请安装 Node.js 22。")
    target = directory()
    if reset and target.exists():
        shutil.rmtree(target)
    if not credentials_path().is_file():
        print("正在写入隔离演示数据（模拟服务，不会产生费用）…", flush=True)
        subprocess.check_call(
            [str(python), "-m", "backend.demo.seed", str(target)],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
        )
    return json.loads(credentials_path().read_text())


def process_env():
    return {
        **os.environ,
        "DATABASE_URL": "sqlite:///" + str(directory() / "demo.sqlite"),
        "DATA_DIR": str(directory()),
        "APP_ENV": "test",
        "PROVIDER_MODE": "mock",
        "LIVE_CALLS_ENABLED": "false",
        "WEB_ORIGIN": WEB_ORIGIN,
        "PRICE_VALID_UNTIL": "2099-01-01",
        "ENROLLMENT_PRICE_VALID_UNTIL": "2099-01-01",
        "QWEN_API_KEY": "",
        "VOICE_API_KEY": "",
        "GITHUB_TOKEN": "",
        "HEXING_QA": "0",
        "HEXING_DIST_DIR": ".next-demo",
        "HEXING_API_ORIGIN": f"http://127.0.0.1:{API_PORT}",
    }


def main():
    if sys.platform == "win32":
        raise SystemExit("此启动方式支持 macOS / Linux；Windows 请使用 WSL2。")
    parser = argparse.ArgumentParser(description="启动带完整示例数据的本机演示工作台")
    parser.add_argument("--reset", action="store_true", help="清空 data/demo 后重新写入示例")
    args = parser.parse_args()
    check_ports()
    creds = prepare(args.reset)
    python = str(ROOT / ".venv/bin/python")
    env = process_env()
    commands = [
        [
            python,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(API_PORT),
            "--no-access-log",
            "--no-proxy-headers",
        ],
        [python, "-m", "backend.demo.worker"],
        [
            "npm",
            "--prefix",
            "frontend",
            "run",
            "dev",
            "--",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(WEB_PORT),
        ],
    ]
    processes = []

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        for command in commands:
            processes.append(subprocess.Popen(command, cwd=ROOT, env=env, start_new_session=True))
        print(f"演示工作台：{WEB_ORIGIN}/discover", flush=True)
        print(f"登录账户：{creds['login_name']}", flush=True)
        print(f"密码：{creds['password']}", flush=True)
        print(f"访客邀请码：{creds['invite_code']}（只读，可多次使用）", flush=True)
        print("隔离模拟环境，不读写正式数据，也不调用付费服务。Ctrl+C 停止。", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        raise SystemExit("某个演示服务已退出，正在停止其余进程。请检查上方日志。")
    except KeyboardInterrupt:
        print("\n正在停止演示…", flush=True)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        stop(processes)
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()

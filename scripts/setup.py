"""Install AI-Hexing locally without overwriting existing configuration."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform == "win32":
        raise SystemExit("此启动方式支持 macOS / Linux；Windows 请使用 WSL2。")
    if sys.version_info < (3, 11):
        raise SystemExit("需要 Python 3.11 或更新版本；推荐已验收的 3.11。")
    if not shutil.which("node") or not shutil.which("npm"):
        raise SystemExit("请先安装 Node.js 22（包含 npm）。")
    version = subprocess.check_output(["node", "--version"], text=True).strip()
    if int(version.lstrip("v").split(".")[0]) < 22:
        raise SystemExit("需要 Node.js 22 或更新版本；推荐 22 LTS。")
    python = ROOT / ".venv/bin/python"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "-r", "backend/requirements-dev.txt"], cwd=ROOT, check=True)
    subprocess.run(["npm", "--prefix", "frontend", "ci"], cwd=ROOT, check=True)
    # Exclusive creation also protects against overwriting a concurrent edit.
    try:
        with (ROOT / ".env").open("x") as output:
            output.write((ROOT / ".env.example").read_text())
        (ROOT / ".env").chmod(0o600)
        print("已创建默认 mock 配置，不会调用真实模型。", flush=True)
    except FileExistsError:
        print("保留已有 .env；请确认其中的数据库和供应商配置。", flush=True)
    subprocess.run([str(python), "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"], cwd=ROOT, check=True)
    print("安装完成。首次使用请创建账户：\n.venv/bin/python -m backend.app.cli create-owner\n然后启动：python3.11 scripts/start.py")


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, KeyboardInterrupt):
        raise SystemExit("安装已停止。解决上方错误后可重新运行；已有 .env 将保留。")

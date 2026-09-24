import argparse
import getpass

from sqlalchemy import delete, select

from backend.app.core.auth import hasher
from backend.app.core.db import factory, transaction
from backend.app.models import Creator, LoginAttempt


def main():
    p = argparse.ArgumentParser(description="AI-Hexing local administration")
    p.add_argument("command", choices=["create-owner", "recover-audio"])
    p.add_argument("--job-id", help="需要从本地返回检查点恢复的音频作业 ID")
    args = p.parse_args()
    if args.command == "recover-audio":
        if not args.job_id:
            p.error("recover-audio 需要 --job-id")
        from backend.app.worker import Worker

        Worker().recover_audio(args.job_id)
        print("本地音频结果恢复已处理，未发起新的供应商调用；请刷新工作台查看作业状态。")
        return
    sf = factory()
    with sf() as db:
        if db.scalar(select(Creator)):
            raise SystemExit("创作者已存在；不会覆盖现有账户。")
    name = input("登录账户名：").strip()
    password = getpass.getpass("密码（至少 12 位，不回显）：")
    if not name or len(password) < 12 or password != getpass.getpass("再次输入密码："):
        raise SystemExit("账户或密码确认无效。")
    with transaction(sf) as db:
        if db.scalar(select(Creator)):
            raise SystemExit("创作者已存在。")
        db.add(Creator(login_name=name, password_hash=hasher.hash(password)))
        db.execute(delete(LoginAttempt))
    print("创作者已创建，首次设置前的登录失败记录已清除。请使用刚设置的账户和密码登录。")


if __name__ == "__main__":
    main()

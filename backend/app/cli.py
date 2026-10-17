import argparse
import getpass
import time
from datetime import datetime

from sqlalchemy import delete, select, update

from backend.app.core.auth import INVITE_DAYS, create_invite, hasher
from backend.app.core.db import factory, transaction
from backend.app.models import Creator, Invite, LoginAttempt, Session


def main():
    p = argparse.ArgumentParser(description="AI-Hexing local administration")
    p.add_argument(
        "command", choices=["create-owner", "recover-audio", "create-invite", "list-invites", "revoke-invite"]
    )
    p.add_argument("--job-id", help="需要从本地返回检查点恢复的音频作业 ID")
    p.add_argument("--days", type=int, default=INVITE_DAYS, help="邀请码有效天数（默认 30）")
    p.add_argument("--label", default="", help="邀请码备注，例如发给谁")
    p.add_argument("--invite-id", help="要作废的邀请码记录 ID（见 list-invites）")
    args = p.parse_args()
    if args.command == "recover-audio":
        if not args.job_id:
            p.error("recover-audio 需要 --job-id")
        from backend.app.worker import Worker

        Worker().recover_audio(args.job_id)
        print("本地音频结果恢复已处理，未发起新的供应商调用；请刷新工作台查看作业状态。")
        return
    if args.command == "create-invite":
        if not 1 <= args.days <= 365:
            p.error("--days 需在 1–365 之间")
        with transaction(factory()) as db:
            owner = db.scalar(select(Creator).where(Creator.active.is_(True)))
            if not owner:
                raise SystemExit("尚未创建创作者账户，请先运行 create-owner。")
            invite, code = create_invite(db, owner.id, args.days, args.label[:100])
            expires = datetime.fromtimestamp(invite.expires_at).strftime("%Y-%m-%d %H:%M")
        print(f"邀请码：{code}")
        print(f"有效期至 {expires}，可多次使用；访客只能查看工作台，不能修改、生成或使用声音功能。")
        print("邀请码只显示这一次，请妥善转交。")
        return
    if args.command == "list-invites":
        with factory()() as db:
            rows = list(db.scalars(select(Invite).order_by(Invite.created_at.desc())))
        if not rows:
            print("暂无邀请码。")
        now = time.time()
        for r in rows:
            state = "已作废" if r.revoked else "已过期" if r.expires_at <= now else "有效"
            expires = datetime.fromtimestamp(r.expires_at).strftime("%Y-%m-%d")
            print(f"{r.id}  {state}  到期 {expires}  使用 {r.use_count} 次  {r.label}")
        return
    if args.command == "revoke-invite":
        if not args.invite_id:
            p.error("revoke-invite 需要 --invite-id")
        with transaction(factory()) as db:
            invite = db.get(Invite, args.invite_id)
            if not invite:
                raise SystemExit("找不到该邀请码记录。")
            invite.revoked = True
            db.execute(update(Session).where(Session.invite_id == invite.id).values(revoked=True))
        print("邀请码已作废，用它登录的访客会话同时失效。")
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

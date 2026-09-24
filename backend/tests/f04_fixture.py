"""Explicit fixtures only in data/browser-qa/f04-qa.sqlite; no providers or real data."""
import json
import sys

from sqlalchemy import select

from backend.app import models as m
from backend.app import schemas as s
from backend.app.core.config import ROOT, Settings
from backend.app.core.db import factory
from backend.app.services import ranking as r


def seed(published=30):
    directory = ROOT / "data" / "browser-qa"
    database = directory / "f04-qa.sqlite"
    if not database.exists():
        raise RuntimeError("Start the isolated F04 browser server first")
    sf = factory(Settings(_env_file=None, database_url="sqlite:///" + str(database), data_dir=directory, app_env="test", provider_mode="live", live_calls_enabled=False))
    entries = []
    with sf() as db:
        owner = db.scalar(select(m.Creator).where(m.Creator.login_name == "browser-test"))
        for i in range(121):
            repo_id = 9100000 + i
            entry = db.scalar(select(m.PublicEntry).where(m.PublicEntry.github_repo_id == repo_id))
            if not entry:
                url = f"https://github.com/hexing-qa/f04-sample-{i + 1}"
                tool = m.Tool(owner_id=owner.id, data_mode="live", github_repo_id=repo_id, canonical_url=url, full_name=f"F04 工程样本 {i + 1:03}", metadata_json={})
                db.add(tool)
                db.flush()
                snap = m.Snapshot(owner_id=owner.id, data_mode="live", tool_id=tool.id, source_sha="b" * 64, payload={"metadata":{"full_name":tool.full_name},"canonical_url":url,"missing_fields":[],"fragments":[{"id":"f1","text":"仅用于隔离工程验收，验证目录与交互，不是真实推荐。","source_url":url}]})
                db.add(snap)
                db.flush()
                analysis = m.Analysis(owner_id=owner.id, data_mode="live", snapshot_id=snap.id, context_hash=str(repo_id), prompt_version="f04-no-model", payload={"highlights":[{"capability":"展示已发布的工具介绍","conditions":"仅用于隔离工程验收","evidence_refs":["f1"]}]})
                db.add(analysis)
                db.flush()
                data = r.create_entry(db, owner.id, "live", s.RankingCreateIn(tool_id=tool.id,source_snapshot_id=snap.id,analysis_id=analysis.id))[0]["entry"]
                entry = db.get(m.PublicEntry, data["id"])
                content = {**data["content"], "name":["资料整理","灵感画板","代码助手","音频处理","网页搭建","知识检索"][i % 6] + f" · {i + 1:03}", "bot_avatar":{"preset":["orbit","pixel","spark"][i % 3],"palette":["mint","violet","amber"][(i // 3) % 3]},"one_line_use":"隔离样本：用于验证 Bot 展示与资料入口。","category":"工程验收"}
                r.edit_entry(db, entry, s.RankingEditIn(expected_revision=entry.revision,content=content))
            if i < published and entry.state != "published":
                preview = r.preview(db, entry)
                r.publish(db, entry, s.RankingPublishIn(expected_revision=entry.revision,draft_revision_id=preview["draft_revision_id"],content_hash=preview["content_hash"],content_reviewed=True))
            entries.append({"id":entry.id,"public_id":entry.public_id,"state":entry.state})
        db.commit()
    (directory / "f04-fixture.json").write_text(json.dumps(entries,indent=2))
    print(json.dumps({"fixtures":len(entries),"published":sum(e["state"]=="published" for e in entries)}))


if __name__ == "__main__":
    amount = int(sys.argv[1]) if len(sys.argv)>1 else 30
    if not 0 <= amount <= 121:
        raise ValueError("Fixture count must be 0..121")
    seed(amount)

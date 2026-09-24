"""Clearly labelled fixture in the isolated QA database, never the normal catalog."""

import json
import time

from sqlalchemy import select

from backend.app import models as m


def seed(sf, directory, filename="ranking-fixture.json"):
    with sf() as db:
        owner = db.scalar(select(m.Creator).where(m.Creator.login_name == "browser-test"))
        stamp = str(time.time_ns())
        url = "https://github.com/hexing-qa/catalog-" + stamp
        tool = m.Tool(
            owner_id=owner.id,
            data_mode="live",
            github_repo_id=int(stamp),
            canonical_url=url,
            full_name="工程测试工具（非真实推荐）",
            metadata_json={},
        )
        db.add(tool)
        db.flush()
        snap = m.Snapshot(
            owner_id=owner.id,
            data_mode="live",
            tool_id=tool.id,
            source_sha="a" * 64,
            payload={
                "metadata": {"full_name": tool.full_name, "description": "仅用于隔离验收，没有调用模型。"},
                "canonical_url": url,
                "missing_fields": [],
                "fragments": [
                    {"id": "f1", "text": "工程测试原文：可展示目录。仅限测试用途。", "source_url": url}
                ],
            },
        )
        db.add(snap)
        db.flush()
        analysis = m.Analysis(
            owner_id=owner.id,
            data_mode="live",
            snapshot_id=snap.id,
            context_hash=stamp,
            prompt_version="qa-fixture-no-model",
            payload={
                "summary": "隔离工程测试",
                "audience": "测试人员",
                "highlights": [
                    {
                        "id": "h1",
                        "capability": "展示测试目录",
                        "benefit": "验证发布副本",
                        "demo": "点开目录",
                        "conditions": "仅限测试用途",
                        "evidence_refs": ["f1"],
                    }
                ],
                "unknowns": [],
                "warnings": [],
            },
        )
        db.add(analysis)
        db.commit()
        (directory / filename).write_text(
            json.dumps({"snapshot_id": snap.id, "analysis_id": analysis.id, "tool_id": tool.id})
        )

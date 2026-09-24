"""Platform rules are tested with explicit fixtures, never asserted as real Douyin limits."""

import copy
import io
import json
import zipfile
from datetime import timedelta

import pytest

from backend.app import models as m
from backend.app.services import platform_rules as rules
from backend.app.services.exports import build_package, freeze_manifest, text_body
from backend.tests.test_creation import generate, move, texts


@pytest.fixture
def policy_file(tmp_path, monkeypatch):
    data = json.loads(rules.POLICY_PATH.read_text())
    path = tmp_path / "policy.json"
    monkeypatch.setattr(rules, "POLICY_PATH", path)

    def write(value):
        path.write_text(json.dumps(value, ensure_ascii=False))

    write(data)
    return data, write, path


def verified(data):
    data = copy.deepcopy(data)
    stamp = rules.now()
    data["reviewed_at"] = stamp.isoformat()
    data["sources"][0].update(
        available=True,
        excerpt="隔离测试的假设规则，不代表真实平台限制。",
        accessed_at=(stamp - timedelta(hours=2)).isoformat(),
    )
    for r in data["rules"]:
        r.update(
            status="verified",
            maximum=100,
            unit="items" if r["field"] == "tags_count" else "unicode_codepoints",
            source_id="creator",
            verified_at=(stamp - timedelta(hours=1)).isoformat(),
            valid_until=(stamp + timedelta(days=1)).isoformat(),
        )
    return data


def test_default_unknown_is_not_local_schema_limit_or_platform_pass():
    p = rules.describe()
    assert p["status"] == "unverified" and p["verified_at"] is None
    assert p["reviewed_at"] is not None
    assert all(r["maximum"] is None and r["unit"] is None for r in p["rules"])
    report = rules.evaluate({"title": "标题", "body": "原文", "tags": ["AI"]})
    assert report["result"] == "needs_review"
    assert all(c["result"] == "not_checked" for c in report["checks"])
    assert rules.evaluate({}, "script")["status"] == "not_applicable"


@pytest.mark.parametrize(
    "bad",
    [
        "missing",
        "json",
        "extra",
        "negative",
        "bool",
        "unit",
        "no_source",
        "api_source",
        "future",
        "period",
        "no_date",
        "unverified_maximum",
        "duplicate",
    ],
)
def test_invalid_policy_fails_to_review_without_disclosing_paths(policy_file, bad):
    data, write, path = policy_file
    data = verified(data)
    if bad == "missing":
        path.unlink()
    elif bad == "json":
        path.write_text("{ private-config-text")
    else:
        r = data["rules"][0]
        if bad == "extra":
            data["secret"] = "private-config-text"
        if bad == "negative":
            r["maximum"] = -1
        if bad == "bool":
            r["maximum"] = True
        if bad == "unit":
            r["unit"] = "items"
        if bad == "no_source":
            r["source_id"] = "absent"
        if bad == "api_source":
            r["source_id"] = "openapi"
            data["sources"][1]["available"] = True
        if bad == "future":
            data["reviewed_at"] = (rules.now() + timedelta(days=1)).isoformat()
        if bad == "period":
            r["valid_until"] = r["verified_at"]
        if bad == "no_date":
            r["verified_at"] = None
        if bad == "unverified_maximum":
            r["status"] = "unverified"
        if bad == "duplicate":
            data["rules"][1] = r.copy()
        write(data)
    p = rules.evaluate({"body": "保留内容", "tags": ["AI"]})
    assert p["status"] == "config_error" and p["result"] == "needs_review"
    assert "private-config-text" not in json.dumps(p) and str(path) not in json.dumps(p)
    assert all(c["maximum"] is None for c in p["checks"])


def test_expiration_and_scope_do_not_use_stale_maximum(policy_file, monkeypatch):
    data, write, _ = policy_file
    data = verified(data)
    write(data)
    assert rules.describe()["status"] == "verified"
    later = rules.now() + timedelta(days=2)
    monkeypatch.setattr(rules, "now", lambda: later)
    p = rules.evaluate({"body": "原稿", "tags": ["AI"]})
    assert p["status"] == "expired" and p["verified_at"] is None
    assert all(c["maximum"] is None and c["result"] == "not_checked" for c in p["checks"])
    assert p["result"] == "needs_review"


def test_unicode_units_boundaries_combined_text_and_no_mutation(policy_file):
    data, write, _ = policy_file
    data = verified(data)
    content = {"title": "中😀e\u0301", "body": "AB\n中", "tags": ["AI", "设计"]}
    original = copy.deepcopy(content)
    by = {r["field"]: r for r in data["rules"]}
    by["title"].update(maximum=5, unit="utf16_units")
    by["body"]["maximum"] = 4
    by["tags_count"]["maximum"] = 2
    by["tag_length"]["maximum"] = 2
    combined = "中😀e\u0301\n\nAB\n中\n\n#AI #设计"
    by["combined_text"]["maximum"] = len(combined)
    write(data)
    p = rules.evaluate(content)
    assert p["result"] == "within_known_limits"
    assert [c["count"] for c in p["checks"]] == [5, 4, 2, 2, len(combined)]
    assert rules.compose_copy(content) == combined
    by["title"]["maximum"] = 4
    write(data)
    p = rules.evaluate(content)
    assert p["result"] == "exceeded"
    assert p["checks"][0]["result"] == "exceeded"
    assert content == original
    assert rules.count("😀e\u0301", "unicode_codepoints") == 3
    assert rules.count("😀e\u0301", "utf16_units") == 4


def test_partial_policy_only_checks_verified_fields(policy_file):
    data, write, _ = policy_file
    active = verified(data)
    data["reviewed_at"] = active["reviewed_at"]
    data["sources"] = active["sources"]
    data["rules"][0] = active["rules"][0]
    data["rules"][0]["maximum"] = 1
    write(data)
    p = rules.evaluate({"title": "超限", "body": "内容", "tags": ["AI"]})
    assert p["status"] == "partial" and p["result"] == "exceeded"
    assert p["checks"][0]["maximum"] == 1
    assert all(c["result"] == "not_checked" for c in p["checks"][1:])


@pytest.mark.parametrize(
    "content", [None, {"body": ["invalid"], "tags": [None]}, {"body": "正文", "tags": "#AI"}]
)
def test_invalid_draft_can_still_be_inspected(policy_file, content):
    data, write, _ = policy_file
    write(verified(data))
    p = rules.evaluate(content)
    # Counts are best-effort; never throw away the draft or report platform approval.
    assert p["result"] == "needs_review" and p["content_invalid"]
    assert "checks" in p


def test_current_read_keeps_saved_rules_and_confirmations(env, task_data, policy_file):
    data, write, _ = policy_file
    task = task_data["task"]
    generate(env, task)
    versions = texts(env, task)["versions"]
    script = next(v for v in versions if v["kind"] == "script")
    v = next(v for v in versions if v["kind"] == "copy")
    for version in (script, v):
        assert move(env, task, version["id"], "adopt").status_code == 200
        assert move(env, task, version["id"], "confirm").status_code == 200
    with env.sf() as db:
        before = copy.deepcopy(db.get(m.TextVersion, v["id"]).payload)
        revision = db.get(m.Task, task).revision
    active = verified(data)
    active["version"] = "QA-new-version"
    for r in active["rules"]:
        r["maximum"] = 1
    write(active)
    current = texts(env, task)["task"]["current_copy"]
    assert current["platform_limits"]["result"] == "exceeded"
    assert current["platform_limits_saved"] == before["platform_limits"]
    assert current["confirmed_at"] and current["valid"]
    body = {
        "kind": "copy",
        "parent_id": v["id"],
        "script_id": script["id"],
        "expected_revision": revision,
        "content": v["content"],
        "evidence_map": v["evidence_map"],
    }
    result = env.post(f"/tasks/{task}/texts", body)
    assert result.status_code == 200 and result.json()["data"]["changed"] is False
    with env.sf() as db:
        assert db.get(m.TextVersion, v["id"]).payload == before
        assert db.get(m.Task, task).revision == revision


def test_export_snapshot_frozen_and_legacy_packages_unchanged(env, task_data, policy_file):
    data, write, _ = policy_file
    task = task_data["task"]
    generate(env, task)
    for v in texts(env, task)["versions"]:
        assert move(env, task, v["id"], "adopt").status_code == 200
    with env.sf() as db:
        frozen = freeze_manifest(db, env.settings, db.get(m.Task, task), False)
        v = db.get(m.TextVersion, frozen["copy"]["id"])
        assert frozen["copy"]["text"] == text_body(v) == rules.compose_copy(v.payload["content"])
    original = copy.deepcopy(frozen)
    write(verified(data))
    package = build_package(env.settings, frozen)
    with zipfile.ZipFile(io.BytesIO(package)) as z:
        assert json.loads(z.read("版本说明.json")) == original
    assert frozen == original
    legacy = copy.deepcopy(frozen)
    del legacy["copy"]["platform_limits"]
    with zipfile.ZipFile(io.BytesIO(build_package(env.settings, legacy))) as z:
        assert "platform_limits" not in json.loads(z.read("版本说明.json"))["copy"]
    with env.sf() as db:
        updated = freeze_manifest(db, env.settings, db.get(m.Task, task), False)
        assert updated["copy"]["platform_limits"]["status"] == "verified"


def test_rules_private_read_only_and_no_task_or_secrets(env):
    r = env.client.get("/api/v1/platform-rules/douyin")
    assert r.status_code == 200
    assert "owner_id" not in r.text and "sample" not in r.text
    assert env.post("/platform-rules/douyin", {}).status_code == 405
    env.client.cookies.clear()
    assert env.client.get("/api/v1/platform-rules/douyin").status_code == 401

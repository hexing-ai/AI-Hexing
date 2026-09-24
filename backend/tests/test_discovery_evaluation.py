"""Evidence failures must not erase useful candidate information or trigger calls."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from backend.app import models as m
from backend.app.services.discovery import discovery_payload, rank, source_facts

NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


def fixture(count=1):
    candidates, scores = [], []
    for i in range(count):
        ref = f"r{i}-f1"
        candidates.append(
            {
                "metadata": {
                    "id": i,
                    "full_name": f"demo/tool-{i}",
                    "updated_at": "2026-09-01T00:00:00Z",
                    "description": "姿态计数",
                    "html_url": f"https://github.com/demo/tool-{i}",
                    "stargazers_count": 3,
                },
                "read_at": NOW.isoformat(),
                "fragments": [
                    {
                        "id": ref,
                        "text": "## 核心特性\n\n- 自动记录训练次数并保存到本地文件。\n\n## 运行环境\n\n- 需要 Python 3.10 和摄像头。",
                        "source_url": f"https://github.com/demo/tool-{i}#readme",
                    }
                ],
            }
        )
        scores.append(
            {
                "repo_id": i,
                "relevance": 3,
                "completeness": 2,
                "demonstration": 2,
                "accessibility": 1,
                "stopped": False,
                "excluded": False,
                "reason": "展示摄像头动作计数。",
                "barrier": "需要 Python 和摄像头。",
                "evidence": {
                    k: [ref] for k in ("relevance", "completeness", "demonstration", "accessibility")
                },
            }
        )
    return candidates, {"scores": scores}


def test_missing_one_reference_keeps_all_candidates_and_other_scores():
    candidates, raw = fixture(8)
    for value in raw["scores"][:7]:
        value["evidence"]["accessibility"] = []
    raw["scores"][7]["relevance"] = 1
    before = deepcopy((raw, candidates))
    result = rank(raw, candidates, NOW)
    assert result["summary"] == {"recommended": 0, "pending": 7, "not_recommended": 1}
    assert len(result["evaluated_candidates"]) == 8
    item = result["evaluated_candidates"][0]
    assert item["score"]["accessibility"] is None
    assert item["score"]["total"] == 9 and item["score"]["partial"]
    assert item["score"]["reason"] and item["score"]["barrier"] is None
    assert item["facts"]["requirements"]["text"] == "需要 Python 3.10 和摄像头。"
    assert item["evaluation_issues"][0]["field"] == "accessibility"
    assert (raw, candidates) == before


@pytest.mark.parametrize("invalid", [True, "2", -1, 4, None])
def test_dimension_type_and_range_are_local_failures(invalid):
    candidates, raw = fixture()
    raw["scores"][0]["relevance"] = invalid
    item = rank(raw, candidates, NOW)["evaluated_candidates"][0]
    assert item["score"]["relevance"] is None
    assert item["score"]["completeness"] == 2
    assert item["score"]["reason"] is None
    assert item["disposition"] == "pending"


@pytest.mark.parametrize(
    "evidence", [{"relevance": ["r1-f1"]}, {"relevance": ["unknown"]}, {"relevance": "r0-f1"}, None]
)
def test_invalid_and_cross_repository_references_reject_score_but_keep_candidate(evidence):
    candidates, raw = fixture(2)
    raw["scores"][0]["evidence"] = evidence
    result = rank(raw, candidates, NOW)
    item = result["evaluated_candidates"][0]
    assert "score" not in item and item["disposition"] == "pending"
    assert len(result["evaluated_candidates"]) == 2
    assert result["recommendations"][0]["metadata"]["id"] == 1


def test_duplicate_unknown_missing_and_top_five():
    candidates, raw = fixture(8)
    result = rank(raw, candidates, NOW)
    assert len(result["recommendations"]) == 5 and len(result["evaluated_candidates"]) == 8
    raw["scores"].append(deepcopy(raw["scores"][0]))
    raw["scores"].append({**raw["scores"][1], "repo_id": 99})
    result = rank(raw, candidates, NOW)
    assert result["evaluated_candidates"][0]["disposition"] == "pending"
    assert any(r["code"] == "UNKNOWN_REPOSITORY" for r in result["rejected"])
    result = rank({"scores": "bad JSON shape"}, candidates, NOW)
    assert result["summary"]["pending"] == 8 and not result["recommendations"]


def test_maintenance_and_literal_facts_require_evidence():
    candidates, raw = fixture()
    value = raw["scores"][0]
    value["stopped"] = True
    value["facts"] = {
        "feature": {"quote": "自动记录训练次数并保存到本地文件。", "refs": ["r0-f1"]},
        "requirements": {"quote": "完全免费，不需要安装。", "refs": ["r0-f1"]},
    }
    item = rank(raw, candidates, NOW)["evaluated_candidates"][0]
    assert item["score"]["maintenance"] is None
    assert item["facts"]["feature"]["text"] == "自动记录训练次数并保存到本地文件。"
    assert item["facts"]["requirements"]["text"] != "完全免费，不需要安装。"
    assert {x["code"] for x in item["evaluation_issues"]} == {"STOPPED_UNVERIFIED", "FACT_UNVERIFIED"}
    value["stopped"] = False
    candidates[0]["metadata"].pop("updated_at")
    assert rank(raw, candidates, NOW)["evaluated_candidates"][0]["score"]["maintenance"] is None


def test_excerpts_skip_headings_links_and_wrapped_sentence_fragments():
    candidates, _ = fixture()
    candidates[0]["fragments"][0]["text"] = (
        "### requirements\n\n[Installation requirements](#requirements)\n\n## Features\n\nA local tool records training data\nand supports exporting complete reports.\n\n![Features](image.png)"
    )
    facts = source_facts(candidates[0])
    assert "requirements" not in facts
    assert (
        facts["feature"]["text"]
        == "A local tool records training data\nand supports exporting complete reports."
    )


def test_history_projection_is_stable_read_only_and_api_isolated(env, monkeypatch):
    candidates, raw = fixture()
    raw["scores"][0]["evidence"]["accessibility"] = []
    payload = {
        "candidates": candidates,
        "status": "scored",
        "score_runs": [
            {
                "job_id": "old-job",
                "recommendations": [],
                "rejected": [{"message": "old failure"}],
                "raw_output": raw,
                "raw_text": "original bytes are retained",
            }
        ],
    }
    before = deepcopy(payload)
    projected = discovery_payload(payload, NOW.timestamp())
    assert projected == discovery_payload(payload, NOW.timestamp())
    assert payload == before
    assert projected["score_runs"][0]["raw_output"] == raw
    assert projected["score_runs"][0]["raw_text"] == before["score_runs"][0]["raw_text"]
    assert projected["score_runs"][0]["summary"]["pending"] == 1
    with env.sf() as db:
        d = m.Discovery(
            owner_id=env.owner_id,
            data_mode="mock",
            query="legacy fixture",
            payload=payload,
            created_at=NOW.timestamp(),
        )
        db.add(d)
        db.commit()
        id = d.id

    def forbid(*args, **kwargs):
        pytest.fail("reading old evaluations must not call a provider")

    monkeypatch.setattr("backend.app.services.providers.Providers.text", forbid)
    detail = env.client.get("/api/v1/discoveries/" + id)
    assert detail.status_code == 200
    assert len(detail.json()["data"]["score_runs"][0]["evaluated_candidates"]) == 1
    assert (
        env.client.get("/api/v1/discoveries").json()["data"]["items"][0]["score_runs"][0]["summary"][
            "pending"
        ]
        == 1
    )
    with env.sf() as db:
        assert db.get(m.Discovery, id).payload == before
        other = m.Creator(login_name="other-discovery-owner", password_hash="unused")
        db.add(other)
        db.flush()
        db.get(m.Discovery, id).owner_id = other.id
        db.commit()
    assert env.client.get("/api/v1/discoveries/" + id).status_code == 404
    assert env.client.get("/api/v1/discoveries").json()["data"]["items"] == []


def test_missing_explanations_do_not_become_unqualified_recommendations():
    candidates, raw = fixture()
    raw["scores"][0]["reason"] = "  "
    raw["scores"][0].pop("barrier")
    item = rank(raw, candidates, NOW)["evaluated_candidates"][0]
    assert item["disposition"] == "pending"
    assert item["score"]["reason"] is None and item["score"]["barrier"] is None
    assert {v["field"] for v in item["evaluation_issues"]} == {"reason", "barrier"}

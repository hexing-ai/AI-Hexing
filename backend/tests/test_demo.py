from fastapi.testclient import TestClient

from backend.app.core.db import factory
from backend.app.main import create_app
from backend.app.services.providers import Providers
from backend.demo.providers import DemoProviders
from backend.demo.seed import demo_settings, seed


def test_demo_providers_replay_known_repository():
    settings = type("S", (), {"provider_mode": "mock"})()
    providers = DemoProviders(settings)
    meta, readme, missing = providers.github("https://github.com/MengTo/sketchbook")
    assert meta["full_name"] == "MengTo/sketchbook" and "magnifier" in readme and missing == []
    analysis, metrics = providers.text(
        "analysis", {"metadata": meta, "fragments": [{"id": "f1", "text": readme[:100]}]}
    )
    assert analysis["highlights"][0]["id"] == "h1" and metrics["model"] == "demo-replay"
    writing, _ = providers.text("generation", {"tool_name": "sketchbook", "fragments": []})
    assert writing["script"]["intro"] == "每天分享好用的工具，今天讲sketchbook。"
    assert providers.mock_search_names({"topic": "健身"}) == [
        "ZHENGYANG-QIAN/AI-FITNESS",
        "BorisBW/claude-fitness-cn",
        "k1ein-chen/fitness-coach-rpg",
    ]
    unknown, _, _ = providers.github("https://github.com/demo/markdown-tool")
    ordinary, _, _ = Providers(settings).github("https://github.com/demo/markdown-tool")
    assert unknown == ordinary


def test_demo_seed_completes_workspace(tmp_path):
    summary = seed(tmp_path)
    settings = demo_settings(tmp_path)
    sf = factory(settings)
    with TestClient(create_app(settings, sf)) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login_name": summary["login_name"], "password": summary["password"]},
            headers={"Origin": settings.web_origin},
        )
        assert login.status_code == 200, login.text
        tasks = client.get("/api/v1/tasks").json()["data"]["items"]
        assert {item["id"] for item in tasks} == set(summary["tasks"])
        complete = next(item for item in tasks if item["id"] == summary["tasks"][0])
        assert complete["status"] == "mock_ready" and complete["materials_ready"] is True
        jobs = client.get("/api/v1/jobs", params={"task_id": complete["id"]}).json()["data"]["items"]
        assert any(item["kind"] == "export" and item["status"] == "succeeded" for item in jobs)
        discovery = client.get("/api/v1/discoveries").json()["data"]["items"][0]
        assert discovery["query"] == "健身" and discovery["status"] == "scored"
        assert len(discovery["candidates"]) == 3
        ranking = client.get("/api/v1/ranking/entries").json()["data"]["items"]
        assert {item["content"]["name"] for item in ranking} == {"Sketchbook", "Ollama"}
        invite = client.post(
            "/api/v1/auth/invite",
            json={"code": summary["invite_code"]},
            headers={"Origin": settings.web_origin},
        )
        assert invite.status_code == 200 and invite.json()["data"]["role"] == "guest"
    sf.kw["bind"].dispose()

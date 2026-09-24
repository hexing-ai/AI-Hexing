import time
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import hasher
from backend.app.core.config import Settings
from backend.app.core.db import factory
from backend.app.main import create_app
from backend.app.models import Base, Creator
from backend.app.worker import Worker


@dataclass
class Env:
    settings: Settings
    sf: object
    client: TestClient
    worker: Worker
    owner_id: str
    csrf: str

    def post(self, path, body=None, key=None, method="POST"):
        headers = {
            "Origin": self.settings.web_origin,
            "X-CSRF-Token": self.csrf,
            "Idempotency-Key": key or str(time.time_ns()),
        }
        return self.client.request(
            method, "/api/v1" + path, json={} if body is None else body, headers=headers
        )

    def drain(self):
        for _ in range(20):
            if not self.worker.run_once():
                break


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        _env_file=None,
        app_env="test",
        data_dir=tmp_path / "data",
        database_url="sqlite:///" + str(tmp_path / "db.sqlite"),
        provider_mode="mock",
    )
    sf = factory(settings)
    Base.metadata.create_all(sf.kw["bind"])
    with sf() as db:
        owner = Creator(login_name="tester", password_hash=hasher.hash("test-password-only"))
        db.add(owner)
        db.commit()
        owner_id = owner.id
    with TestClient(create_app(settings, sf)) as client:
        r = client.post(
            "/api/v1/auth/login",
            json={"login_name": "tester", "password": "test-password-only"},
            headers={"Origin": settings.web_origin},
        )
        assert r.status_code == 200, r.text
        csrf = r.json()["data"]["csrf_token"]
        yield Env(settings, sf, client, Worker(settings, sf), owner_id, csrf)
    sf.kw["bind"].dispose()


@pytest.fixture
def task_data(env):
    r = env.post("/imports", {"repository_url": "https://github.com/demo/markdown-tool"})
    assert r.status_code == 202, r.text
    env.drain()
    job = env.client.get("/api/v1/jobs/" + r.json()["data"]["job_id"]).json()["data"]
    assert job["status"] == "succeeded", job
    snap = job["result_ref"]["snapshot_id"]
    r = env.post("/snapshots/" + snap + "/analyses", {"audience": "测试观众"})
    assert r.status_code == 202, r.text
    env.drain()
    job = env.client.get("/api/v1/jobs/" + r.json()["data"]["job_id"]).json()["data"]
    assert job["status"] == "succeeded", job
    analysis = job["result_ref"]["analysis_id"]
    review = env.post("/analyses/" + analysis + "/reviews", {"primary_highlight_id": "h1", "confirmed": True})
    assert review.status_code == 201, review.text
    review_id = review.json()["data"]["id"]
    task = env.post("/tasks", {"review_id": review_id, "audience": "测试观众"})
    assert task.status_code == 201, task.text
    return {"snapshot": snap, "analysis": analysis, "review": review_id, "task": task.json()["data"]["id"]}


@pytest.fixture
def pre_ranking_schema(env):
    """Old migration fixtures must not contain tables from a future migration."""
    from sqlalchemy import func, select

    from backend.app import models as m

    tables = (
        m.CostReconciliation,
        m.TaskContextVersion,
        m.TaskAssetLink,
        m.TaskLifecycle,
        m.PublicInteractionEvent,
        m.PublicEntryEvent,
        m.PublicRevision,
        m.PublicEntry,
    )
    with env.sf() as db:
        for cls in tables:
            assert db.scalar(select(func.count()).select_from(cls)) == 0
    for cls in tables:
        cls.__table__.drop(env.sf.kw["bind"])

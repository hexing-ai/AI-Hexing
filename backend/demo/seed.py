"""Seed an isolated mock workspace by replaying the real API flow."""

import json
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.core.auth import create_invite, hasher
from backend.app.core.config import Settings
from backend.app.core.db import factory
from backend.app.main import create_app
from backend.app.models import Base, Creator
from backend.app.services.assets import save_asset
from backend.app.services.providers import wav_tone
from backend.app.worker import Worker
from backend.demo.providers import DemoProviders

LOGIN = "demo"
PASSWORD = "interview-demo-12"
WEB_ORIGIN = "http://127.0.0.1:23060"
AUDIENCE = "希望了解实用 AI 工具的抖音观众"


def demo_settings(directory):
    return Settings(
        _env_file=None,
        app_env="test",
        data_dir=directory,
        database_url="sqlite:///" + str(directory / "demo.sqlite"),
        provider_mode="mock",
        live_calls_enabled=False,
        web_origin=WEB_ORIGIN,
        price_valid_until=date(2099, 1, 1),
        enrollment_price_valid_until=date(2099, 1, 1),
    )


@dataclass
class Env:
    settings: Settings
    sf: object
    client: TestClient
    worker: Worker
    owner_id: str
    csrf: str

    def post(self, path, body=None, key=None, method="POST"):
        return self.client.request(
            method,
            "/api/v1" + path,
            json={} if body is None else body,
            headers={
                "Origin": self.settings.web_origin,
                "X-CSRF-Token": self.csrf,
                "Idempotency-Key": key or str(time.time_ns()),
            },
        )

    def drain(self):
        for _ in range(40):
            if not self.worker.run_once():
                return
        raise RuntimeError("演示作业未在预期步数内完成")


def finished(env, response):
    assert response.status_code in (200, 201, 202), response.text
    payload = response.json()["data"]
    if "job_id" not in payload:
        return payload
    env.drain()
    job = env.client.get("/api/v1/jobs/" + payload["job_id"]).json()["data"]
    assert job["status"] == "succeeded", job
    return {**payload, **job}


def texts(env, task):
    return env.client.get("/api/v1/tasks/" + task + "/texts").json()["data"]


def adopt_confirm(env, task):
    for kind in ("script", "copy"):
        version = next(v for v in texts(env, task)["versions"] if v["kind"] == kind)
        body = {"expected_revision": texts(env, task)["task"]["revision"]}
        assert env.post(f"/tasks/{task}/texts/{version['id']}/adopt", body).status_code == 200
        body = {**body, "expected_revision": texts(env, task)["task"]["revision"], "facts_reviewed": True}
        assert env.post(f"/tasks/{task}/texts/{version['id']}/confirm", body).status_code == 200


def import_reviewed(env, url):
    imported = finished(env, env.post("/imports", {"repository_url": url}))
    snap = imported["result_ref"]["snapshot_id"]
    analysis = finished(env, env.post("/snapshots/" + snap + "/analyses", {"audience": AUDIENCE}))
    analysis_id = analysis["result_ref"]["analysis_id"]
    review = finished(
        env,
        env.post("/analyses/" + analysis_id + "/reviews", {"primary_highlight_id": "h1", "confirmed": True}),
    )
    task = finished(env, env.post("/tasks", {"review_id": review["id"], "audience": AUDIENCE}))
    return {
        "tool_id": imported["result_ref"]["tool_id"],
        "snapshot_id": snap,
        "analysis_id": analysis_id,
        "task_id": task["id"],
    }


def generate_texts(env, task):
    finished(
        env,
        env.post(
            f"/tasks/{task}/generations",
            {
                "task_id": task,
                "mode": "joint",
                "expected_revision": env.client.get("/api/v1/tasks/" + task).json()["data"]["revision"],
                "base_script_id": None,
            },
        ),
    )
    adopt_confirm(env, task)


def complete_audio(env, task):
    with env.sf() as db:
        asset = save_asset(db, env.settings, env.owner_id, "mock", wav_tone(6), "voice_sample")
        from backend.app import models as m

        voice = m.ProbeVoice(
            owner_id=env.owner_id,
            data_mode="mock",
            sample_asset_id=asset.id,
            provider_voice_id="demo-replay-voice",
            provider_scope=env.worker.providers.scope(),
            status="ready",
            preview_confirmed_at=time.time(),
        )
        db.add(voice)
        db.commit()
        voice_id = voice.id
    assert (
        env.post(f"/voices/{voice_id}/metadata", {"display_name": "演示音色", "expected_revision": 0}).status_code
        == 200
    )
    current = texts(env, task)["task"]
    audio = finished(
        env,
        env.post(
            f"/tasks/{task}/audio-generations",
            {
                "task_id": task,
                "script_id": current["current_script"]["id"],
                "probe_voice_id": voice_id,
                "voice_revision": 1,
                "expected_revision": current["revision"],
            },
        ),
    )
    assert (
        env.post(
            f"/tasks/{task}/audio/{audio['audio_id']}/confirm",
            {"expected_revision": texts(env, task)["task"]["revision"], "accepted": True},
        ).status_code
        == 200
    )
    finished(
        env,
        env.post(f"/tasks/{task}/exports", {"expected_revision": texts(env, task)["task"]["revision"], "complete": True}),
    )
    return voice_id


def seed_discovery(env):
    created = finished(env, env.post("/discoveries", {"topic": "健身"}))
    discovery_id = created["discovery_id"]
    finished(env, env.post(f"/discoveries/{discovery_id}/brief", {"discovery_id": discovery_id}))
    finished(env, env.post(f"/discoveries/{discovery_id}/score", {"discovery_id": discovery_id}))
    return discovery_id


def seed_ranking(env, refs, name, category, one_line):
    created = finished(
        env,
        env.post(
            "/ranking/entries",
            {
                "tool_id": refs["tool_id"],
                "source_snapshot_id": refs["snapshot_id"],
                "analysis_id": refs["analysis_id"],
            },
        ),
    )
    entry = created["entry"]
    content = {
        **entry["content"],
        "name": name,
        "category": category,
        "one_line_use": one_line,
    }
    edited = finished(
        env,
        env.post(
            f"/ranking/entries/{entry['id']}",
            {"expected_revision": entry["revision"], "content": content},
            method="PATCH",
        ),
    )
    return edited["id"]


def write_credentials(directory, invite_code):
    path = directory / "credentials.json"
    path.write_text(
        json.dumps(
            {"login_name": LOGIN, "password": PASSWORD, "invite_code": invite_code, "web_origin": WEB_ORIGIN},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    path.chmod(0o600)
    return path


def seed(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    settings = demo_settings(directory)
    sf = factory(settings)
    Base.metadata.create_all(sf.kw["bind"])
    with sf() as db:
        owner = Creator(login_name=LOGIN, password_hash=hasher.hash(PASSWORD))
        db.add(owner)
        db.commit()
        owner_id = owner.id
        invite, code = create_invite(db, owner_id, 30, "面试演示访客")
        db.commit()
    worker = Worker(settings, sf, DemoProviders(settings))
    with TestClient(create_app(settings, sf)) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login_name": LOGIN, "password": PASSWORD},
            headers={"Origin": settings.web_origin},
        )
        assert login.status_code == 200, login.text
        env = Env(settings, sf, client, worker, owner_id, login.json()["data"]["csrf_token"])
        sketchbook = import_reviewed(env, "https://github.com/MengTo/sketchbook")
        generate_texts(env, sketchbook["task_id"])
        voice_id = complete_audio(env, sketchbook["task_id"])
        ollama = import_reviewed(env, "https://github.com/ollama/ollama")
        generate_texts(env, ollama["task_id"])
        discovery_id = seed_discovery(env)
        ranking = [
            seed_ranking(env, sketchbook, "Sketchbook", "网页交互", "拖动放大镜查看水彩画细节"),
            seed_ranking(env, ollama, "Ollama", "本地模型", "在自己电脑上运行开放模型"),
        ]
    write_credentials(directory, code)
    sf.kw["bind"].dispose()
    return {
        "login_name": LOGIN,
        "password": PASSWORD,
        "invite_code": code,
        "invite_id": invite.id,
        "voice_id": voice_id,
        "discovery_id": discovery_id,
        "tasks": [sketchbook["task_id"], ollama["task_id"]],
        "ranking": ranking,
    }


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/demo")
    print(json.dumps(seed(target), ensure_ascii=False))

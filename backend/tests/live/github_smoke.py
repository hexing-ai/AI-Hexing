"""Explicit, read-only GitHub smoke. No model keys, voice, or paid requests."""

import json
import secrets
import time

from fastapi.testclient import TestClient

from backend.app.core.auth import hasher
from backend.app.core.config import ROOT, Settings
from backend.app.core.db import factory
from backend.app.main import create_app
from backend.app.models import Base, Creator
from backend.app.worker import Worker


def main():
    run_dir = ROOT / "data" / "live-github-smoke" / str(int(time.time()))
    run_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///" + str(run_dir / "smoke.sqlite"),
        data_dir=run_dir,
        app_env="test",
        provider_mode="live",
        live_calls_enabled=True,
    )
    sf = factory(settings)
    Base.metadata.create_all(sf.kw["bind"])
    password = secrets.token_urlsafe(32)
    with sf() as db:
        db.add(Creator(login_name="smoke", password_hash=hasher.hash(password)))
        db.commit()
    with TestClient(create_app(settings, sf)) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login_name": "smoke", "password": password},
            headers={"Origin": settings.web_origin},
        )
        csrf = login.json()["data"]["csrf_token"]
        r = client.post(
            "/api/v1/imports",
            json={"repository_url": "https://github.com/microsoft/markitdown"},
            headers={
                "Origin": settings.web_origin,
                "X-CSRF-Token": csrf,
                "Idempotency-Key": secrets.token_urlsafe(24),
            },
        )
        assert r.status_code == 202, r.text
        id = r.json()["data"]["job_id"]
        Worker(settings, sf).run_once()
        job = client.get("/api/v1/jobs/" + id).json()["data"]
        report = {
            "mode": "live",
            "contract": "GitHub REST import + persisted snapshot + authenticated GET",
            "status": job["status"],
            "job_id": id,
            "provider": "GitHub",
            "paid_model_calls": 0,
        }
        if job["status"] == "succeeded":
            snap = client.get("/api/v1/snapshots/" + job["result_ref"]["snapshot_id"]).json()["data"]
            report.update(
                {
                    "repository": snap["canonical_url"],
                    "github_repo_id": snap["metadata"]["id"],
                    "fetched_at": snap["fetched_at"],
                    "source_sha": snap["source_sha"],
                    "fragments": len(snap["fragments"]),
                    "missing_fields": snap["missing_fields"],
                    "snapshot_id": snap["id"],
                }
            )
        else:
            report["error"] = job["error"]
        evidence = ROOT / "docs" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "S1-github-live.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False))
        if job["status"] != "succeeded":
            raise SystemExit(1)


if __name__ == "__main__":
    main()

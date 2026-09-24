"""Isolated browser QA environment; never creates an account in the user's database."""

import os
import secrets
import subprocess
import sys
import time

from backend.app.core.auth import hasher
from backend.app.core.config import ROOT, Settings
from backend.app.core.db import factory
from backend.app.models import Base, Creator
from backend.app.services.providers import wav_tone


def main():
    ranking_qa = os.environ.get("HEXING_RANKING_QA") == "1"
    directory = ROOT / "data" / "browser-qa"
    directory.mkdir(parents=True, exist_ok=True)
    qa_origin = os.environ.get("HEXING_QA_ORIGIN", "http://127.0.0.1:13060")
    if qa_origin not in {"http://127.0.0.1:13060", "http://localhost:13060"}:
        raise ValueError("Unsupported isolated QA origin")
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///" + str(directory / ("f04-qa.sqlite" if ranking_qa and os.environ.get("HEXING_F04_QA") == "1" else "ranking-qa.sqlite" if ranking_qa else "qa.sqlite")),
        data_dir=directory,
        app_env="test",
        provider_mode="live" if ranking_qa else "mock",
        web_origin=qa_origin,
    )
    sf = factory(settings)
    Base.metadata.create_all(sf.kw["bind"])
    password = secrets.token_urlsafe(24)
    with sf() as db:
        from sqlalchemy import select

        c = db.scalar(select(Creator).where(Creator.login_name == "browser-test"))
        if c:
            c.password_hash = hasher.hash(password)
        else:
            db.add(Creator(login_name="browser-test", password_hash=hasher.hash(password)))
        db.commit()
    credential = directory / "credentials.json"
    import json

    credential.write_text(json.dumps({"login_name": "browser-test", "password": password}))
    credential.chmod(0o600)
    if ranking_qa:
        from backend.tests.ranking_fixture import seed

        seed(sf, directory)
        seed(sf, directory, "public-catalog-fixture.json")
    (directory / "sample.wav").write_bytes(wav_tone(6))
    env = {
        **os.environ,
        "DATABASE_URL": settings.database_url,
        "DATA_DIR": str(directory),
        "APP_ENV": "test",
        "PROVIDER_MODE": settings.provider_mode,
        "LIVE_CALLS_ENABLED": "false",
        "WEB_ORIGIN": settings.web_origin,
        "HEXING_QA": "1",
    }
    if ranking_qa:
        # QA has no model credentials or worker, even if the normal .env has live access.
        env.update({"QWEN_API_KEY": "", "VOICE_API_KEY": "", "GITHUB_TOKEN": ""})
    processes = []
    try:
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "backend.app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "18060",
                    "--no-access-log",
                    "--no-proxy-headers",
                ],
                cwd=ROOT,
                env=env,
            )
        )
        if not ranking_qa:
            processes.append(
                subprocess.Popen([sys.executable, "-m", "backend.app.worker"], cwd=ROOT, env=env)
            )
        processes.append(
            subprocess.Popen(
                [
                    "npm",
                    "--prefix",
                    "frontend",
                    "run",
                    "start",
                    "--",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    "13060",
                ],
                cwd=ROOT,
                env=env,
            )
        )
        while all(p.poll() is None for p in processes):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in processes:
            p.terminate()
        for p in processes:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        credential.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

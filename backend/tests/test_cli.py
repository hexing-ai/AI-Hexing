import pytest
from sqlalchemy import delete, func, select

from backend.app import cli
from backend.app.models import Creator, LoginAttempt, Session


def test_first_owner_creation_clears_pre_setup_lockout(env, monkeypatch):
    with env.sf() as db:
        db.execute(delete(Session))
        db.execute(delete(Creator))
        db.add_all(LoginAttempt(key="pre-setup") for _ in range(10))
        db.commit()
    monkeypatch.setattr(cli, "factory", lambda: env.sf)
    monkeypatch.setattr("sys.argv", ["cli", "create-owner"])
    monkeypatch.setattr("builtins.input", lambda _: "new-owner")
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "new-local-password")
    cli.main()
    with env.sf() as db:
        assert db.scalar(select(func.count()).select_from(LoginAttempt)) == 0
        assert db.scalar(select(func.count()).select_from(Creator)) == 1
    response = env.client.post(
        "/api/v1/auth/login",
        json={"login_name": "new-owner", "password": "new-local-password"},
        headers={"Origin": env.settings.web_origin},
    )
    assert response.status_code == 200


def test_create_owner_does_not_replace_existing_account(env, monkeypatch):
    monkeypatch.setattr(cli, "factory", lambda: env.sf)
    monkeypatch.setattr("sys.argv", ["cli", "create-owner"])
    with pytest.raises(SystemExit, match="创作者已存在"):
        cli.main()
    with env.sf() as db:
        assert db.get(Creator, env.owner_id).login_name == "tester"

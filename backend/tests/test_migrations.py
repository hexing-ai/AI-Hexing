import os
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from backend.app.core.config import ROOT


def test_empty_database_migration_and_repeat(tmp_path):
    database = "sqlite:///" + str(tmp_path / "migrated.sqlite")
    environment = {**os.environ, "DATABASE_URL": database, "DATA_DIR": str(tmp_path / "data")}
    command = [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"]
    for _ in range(2):
        result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    engine = create_engine(database)
    assert "source_url" in [c["name"] for c in inspect(engine).get_columns("assets")]
    # Follow the actual migration head; also verify the latest feature column.
    assert "hidden_from_library" in {c["name"] for c in inspect(engine).get_columns("voice_metadata")}
    assert {"role", "invite_id"} <= {c["name"] for c in inspect(engine).get_columns("sessions")}
    assert "invites" in inspect(engine).get_table_names()
    expected_head = ScriptDirectory.from_config(Config(str(ROOT / "backend/alembic.ini"))).get_current_head()
    with engine.connect() as db:
        assert db.scalar(text("select version_num from alembic_version")) == expected_head
    engine.dispose()


def test_s1_to_s2_preserves_existing_rows(tmp_path):
    database = "sqlite:///" + str(tmp_path / "upgrade.sqlite")
    environment = {**os.environ, "DATABASE_URL": database, "DATA_DIR": str(tmp_path / "data")}
    command = [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade"]
    subprocess.run([*command, "0001"], cwd=ROOT, env=environment, capture_output=True, check=True)
    engine = create_engine(database)
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO creators (id,created_at,login_name,password_hash,active) VALUES ('fixture',1,'existing','unchanged-hash',1)"
            )
        )
    subprocess.run([*command, "head"], cwd=ROOT, env=environment, capture_output=True, check=True)
    with engine.connect() as db:
        assert db.scalar(text("SELECT password_hash FROM creators WHERE id='fixture'")) == "unchanged-hash"
    assert {"text_versions", "task_text_states", "discovery_batches", "generation_groups"} <= set(
        inspect(engine).get_table_names()
    )
    engine.dispose()

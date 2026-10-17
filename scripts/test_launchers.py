"""Regression checks for configuration preservation and owned-process cleanup."""
import importlib.util
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_preserves_existing_configuration(tmp_path):
    setup = load("setup")
    (tmp_path / ".env").write_text("PRIVATE_CONFIGURATION=keep-me\n")
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").touch()
    with patch.object(setup, "ROOT", tmp_path), patch.object(setup.shutil, "which", return_value="node"), patch.object(setup.subprocess, "check_output", return_value="v22.0.0"), patch.object(setup.subprocess, "run"):
        setup.main()
    assert (tmp_path / ".env").read_text() == "PRIVATE_CONFIGURATION=keep-me\n"


def test_busy_port_never_starts_or_stops_another_service():
    launcher = load("start")
    with patch.object(socket.socket, "bind", side_effect=OSError("busy")):
        with pytest.raises(SystemExit, match="8010"):
            launcher.check_ports()


def test_demo_uses_isolated_ports_and_skips_existing_seed(tmp_path):
    launcher = load("demo")
    with patch.object(socket.socket, "bind", side_effect=OSError("busy")):
        with pytest.raises(SystemExit, match="28060"):
            launcher.check_ports()
    with (
        patch.object(launcher, "ROOT", tmp_path),
        patch.object(launcher, "directory", return_value=tmp_path / "data" / "demo"),
    ):
        (tmp_path / ".venv/bin").mkdir(parents=True)
        (tmp_path / ".venv/bin/python").touch()
        (tmp_path / "frontend/node_modules/next").mkdir(parents=True)
        demo_dir = tmp_path / "data" / "demo"
        demo_dir.mkdir(parents=True)
        (demo_dir / "credentials.json").write_text('{"login_name":"demo","password":"x","invite_code":"A"}')
        creds = launcher.prepare(reset=False)
        assert creds["login_name"] == "demo"
        assert launcher.process_env()["DATA_DIR"] == str(demo_dir)
        assert launcher.process_env()["PROVIDER_MODE"] == "mock"
        assert "ai_hexing.db" not in launcher.process_env()["DATABASE_URL"]


def test_shutdown_terminates_owned_process_group(tmp_path):
    launcher = load("start")
    ready = tmp_path / "ready"
    code = "import pathlib, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); pathlib.Path(__import__('sys').argv[1]).touch(); time.sleep(60)"
    process = subprocess.Popen([sys.executable, "-c", code, str(ready)], start_new_session=True)
    try:
        import time
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        launcher.stop([process])
        assert process.poll() is not None
        with pytest.raises(ProcessLookupError):
            os.killpg(process.pid, 0)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()

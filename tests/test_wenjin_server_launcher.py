import runpy
from pathlib import Path
import pytest

command=runpy.run_path(str(Path(__file__).resolve().parents[1]/"scripts/run_wenjin_server.py"))["command"]


def test_backend_stays_on_loopback_and_uses_one_worker(tmp_path):
    env={"BACKEND_PORT":"8101"}
    args=command("backend",tmp_path,env)
    assert args[args.index("--host")+1]=="127.0.0.1"
    assert args[args.index("--workers")+1]=="1"
    assert env["DEEPTUTOR_API_BASE_URL"]=="http://127.0.0.1:8101"


def test_frontend_uses_production_artifact_and_private_backend(tmp_path,monkeypatch):
    monkeypatch.setattr("shutil.which",lambda _:"node")
    server=tmp_path/"web/.next/standalone/server.js";server.parent.mkdir(parents=True);server.touch()
    env={"FRONTEND_PORT":"3783","DEEPTUTOR_API_BASE_URL":"http://127.0.0.1:8101"}
    args=command("frontend",tmp_path,env)
    assert args==["node",str(server)]
    assert env["HOSTNAME"]=="127.0.0.1" and env["PORT"]=="3783"
    assert env["DEEPTUTOR_API_BASE_URL"].endswith(":8101")


def test_invalid_port_is_rejected(tmp_path):
    with pytest.raises(ValueError):command("backend",tmp_path,{"BACKEND_PORT":"80"})

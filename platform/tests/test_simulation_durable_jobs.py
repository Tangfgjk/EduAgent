import json
from pathlib import Path
import subprocess
import sys
from threading import Event

from fastapi.testclient import TestClient
import pytest

from app.simulation.durable_jobs import DurableLabJobs
from app.simulation.lab import create_lab_app
from app.simulation.runner import SimulationRunner
from tests.test_simulation_lab import FakeRunner, REQUEST, headers, wait_for_job


def pending(identity="a" * 32, state="queued"):
    return dict(job_id=identity, state=state, request=REQUEST, created_at="2026-10-06T12:00:00+00:00",
        finished_at=None, run_id=None, report=None, error=None)


def test_completed_history_survives_restart_with_new_nonce(tmp_path):
    with TestClient(create_lab_app(tmp_path, runner_factory=FakeRunner), base_url="http://localhost") as client:
        nonce = headers(client)
        job = client.post("/api/lab/jobs", json=REQUEST, headers=nonce).json()["job_id"]
        old = wait_for_job(client, job)
        report = client.get(f"/api/lab/jobs/{job}/report").json()
    with TestClient(create_lab_app(tmp_path, runner_factory=FakeRunner), base_url="http://localhost") as client:
        assert headers(client) != nonce
        assert client.get(f"/api/lab/jobs/{job}").json() == old
        assert client.get(f"/api/lab/jobs/{job}/report").json() == report
        assert client.get(f"/api/lab/jobs/{job}/transcript").status_code == 200
        assert client.post("/api/lab/jobs", json=REQUEST, headers=nonce).status_code == 403


def test_output_root_single_owner_across_processes_and_release(tmp_path):
    jobs = DurableLabJobs(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="owned"):
            DurableLabJobs(tmp_path)
        code = "from pathlib import Path; from app.simulation.durable_jobs import DurableLabJobs; import sys; DurableLabJobs(Path(sys.argv[1]))"
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=Path(__file__).parents[1],
            capture_output=True, text=True, timeout=10)
        assert result.returncode != 0 and "owned" in result.stderr
    finally:
        jobs.close()
    released = DurableLabJobs(tmp_path)
    released.close()


def test_queued_request_recovered_but_running_without_checkpoint_not_restarted(tmp_path):
    db = DurableLabJobs(tmp_path)
    db.put(pending())
    db.put(pending("b" * 32, "running"))
    db.close()
    with TestClient(create_lab_app(tmp_path, runner_factory=FakeRunner), base_url="http://localhost") as client:
        assert wait_for_job(client, "a" * 32)["state"] == "completed"
        interrupted = client.get("/api/lab/jobs/" + "b" * 32).json()
        assert interrupted["state"] == "interrupted" and not interrupted["resumable"]
        assert client.post("/api/lab/jobs/" + "b" * 32 + "/resume", json={}, headers=headers(client)).status_code == 409


def test_real_checkpoint_survives_interruption_and_is_resumed_not_new_learner(tmp_path):
    stop = Event()
    stop.set()
    original = SimulationRunner(tmp_path).run("autonomous", max_turns=3, stop_event=stop)
    directory = tmp_path / original["run_id"]
    (directory / "report.json").unlink()  # This isolated fixture simulates termination before final receipt.
    db = DurableLabJobs(tmp_path)
    record = pending(state="running")
    record["run_id"] = original["run_id"]
    db.put(record)
    db.close()
    with TestClient(create_lab_app(tmp_path), base_url="http://localhost") as client:
        status = client.get("/api/lab/jobs/" + record["job_id"]).json()
        assert status["state"] == "interrupted" and status["resumable"]
        resumed = client.post("/api/lab/jobs/" + record["job_id"] + "/resume", json={}, headers=headers(client)).json()
        result = wait_for_job(client, resumed["job_id"])
        assert result["state"] == "completed" and result["run_id"] == original["run_id"]
        assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 1


def test_corrupted_startup_releases_owner_and_old_history_remains_bounded(tmp_path):
    db = DurableLabJobs(tmp_path)
    for index in range(25):
        record = pending(f"{index:032x}", "completed")
        record["report"] = {"status": "completed"}
        db.put(record)
    db.close()
    with TestClient(create_lab_app(tmp_path, runner_factory=FakeRunner), base_url="http://localhost") as client:
        assert len(client.get("/api/lab/jobs").json()["jobs"]) == 20
        assert client.get("/api/lab/jobs/" + "0" * 32).status_code == 200
        assert len(client.get("/api/lab/jobs").json()["jobs"]) == 20
    db = DurableLabJobs(tmp_path)
    db.connection.execute("UPDATE jobs SET payload='not-json'")
    db.connection.commit()
    db.close()
    with pytest.raises(json.JSONDecodeError):
        create_lab_app(tmp_path)
    db = DurableLabJobs(tmp_path)
    db.close()

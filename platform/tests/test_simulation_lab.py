import json
from threading import Event
import time

from fastapi.testclient import TestClient
import pytest

from app.simulation.lab import create_lab_app


REQUEST = {"profile_id": "weak_foundation", "scenario_id": "baseline", "seed": 1, "max_turns": 2}


class FakeRunner:
    def __init__(self, root):
        self.root = root

    def run(self, *, stop_event, **kwargs):
        run_id = "fake_run_" + str(time.monotonic_ns())
        directory = self.root / run_id
        directory.mkdir()
        (directory / "transcript.jsonl").write_text(json.dumps({"action": "ANSWER", "answer": "x=2"}) + "\n", encoding="utf-8")
        return {"run_id": run_id, "report": {"source_kind": "synthetic_ai_generated", "turns_completed": 2,
                                             "educational_effect_claim": False, "automatic_promotion_enabled": False}}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_lab_app(tmp_path / "simulations", runner_factory=FakeRunner), base_url="http://127.0.0.1:8001") as client:
        yield client


def headers(client):
    return {"x-lab-token": client.get("/api/lab/config").json()["token"]}


def wait_for_job(client, job_id):
    for _ in range(200):
        result = client.get("/api/lab/jobs/" + job_id).json()
        if result["state"] in {"completed", "stopped", "failed", "timed_out", "unsupported"}:
            return result
        time.sleep(0.005)
    raise AssertionError("bounded fake job failed to finish")


def test_dark_independent_page_and_synthetic_config(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "simulation-assets/lab.css" in page.text and "仿真实验室" in page.text
    assert "#0d1117" in client.get("/simulation-assets/lab.css").text
    assert "127.0.0.1:8000" not in page.text
    config = client.get("/api/lab/config").json()
    assert len(config["profiles"]) == 6 and len(config["scenarios"]) == 5
    assert not config["educational_effect_claim"] and not config["automatic_promotion_enabled"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert client.get("/docs").status_code == 404


def test_lab_modular_assets_and_strict_csp(client):
    page = client.get("/")
    for name in ("lab.css", "lab.js", "analytics.js"):
        response = client.get("/simulation-assets/" + name)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
    assert "unsafe-inline" not in page.headers["content-security-policy"]
    assert "object-src 'none'" in page.headers["content-security-policy"]
    assert '<script>' not in page.text and '<style>' not in page.text
    assert 'mastery-chart' in page.text and 'independent-chart' in page.text
    assert 'hint-chart' in page.text and 'kc-chart' in page.text
    assert client.get("/simulation-assets/../simulation.html").status_code == 404
    assert client.get("/simulation-assets/lab.js", headers={"Origin": "https://attacker.example"}).status_code == 403


def test_learner_quit_is_terminal_not_resumable(tmp_path):
    class QuitRunner(FakeRunner):
        def run(self, **kwargs):
            result = super().run(**kwargs)
            result['report'].update(status='stopped',resumable=False)
            return result
    with TestClient(create_lab_app(tmp_path,runner_factory=QuitRunner),base_url='http://localhost') as client:
        valid = headers(client)
        job = client.post('/api/lab/jobs',json=REQUEST,headers=valid).json()['job_id']
        assert wait_for_job(client,job)['resumable'] is False
        assert client.post('/api/lab/jobs/'+job+'/resume',json={},headers=valid).status_code==409


def test_run_status_report_and_transcript(client):
    start = client.post("/api/lab/jobs", json=REQUEST, headers=headers(client))
    assert start.status_code == 202
    job_id = start.json()["job_id"]
    status = wait_for_job(client, job_id)
    assert status["state"] == "completed" and status["run_id"]
    assert "stop_event" not in status and "root" not in status
    assert client.get("/api/lab/jobs/" + job_id + "/report").json()["source_kind"] == "synthetic_ai_generated"
    transcript = client.get("/api/lab/jobs/" + job_id + "/transcript").json()
    assert transcript["events"][0]["action"] == "ANSWER"
    assert not transcript["truncated"]
    assert client.post("/api/lab/jobs/" + job_id + "/stop", json={}, headers=headers(client)).json()["state"] == "completed"


@pytest.mark.parametrize("changes", [
    {"profile_id": "unknown"}, {"scenario_id": "http://127.0.0.1:8000"},
    {"max_turns": 0}, {"max_turns": 101}, {"seed": -1}, {"seed": True},
    {"output_root": "C:/real"}, {"dsn": "postgresql://real"}, {"api_key": "secret"},
    {"seed": 2**31}, {"max_turns": "20"},
])
def test_controlled_request_schema_rejects_paths_keys_unknown_ids_and_budget(client, changes):
    assert client.post("/api/lab/jobs", json={**REQUEST, **changes}, headers=headers(client)).status_code == 422


def test_local_origin_host_and_nonce_security(client):
    valid = headers(client)
    assert client.post("/api/lab/jobs", json=REQUEST).status_code == 403
    for extra in [{"Origin": "https://attacker.example"}, {"Host": "attacker.example"},
                  {"Host": "[malformed"}, {"sec-fetch-site": "cross-site"}, {"sec-fetch-site": "same-site"}]:
        assert client.post("/api/lab/jobs", json=REQUEST, headers={**valid, **extra}).status_code == 403
    assert client.get("/api/lab/config", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/lab/jobs", content=" " * 5000, headers=valid).status_code == 413
    assert client.post("/api/lab/jobs", json=REQUEST, headers={**valid, "Origin": "http://127.0.0.1:8001"}).status_code == 202


def test_one_active_job_cooperative_stop_and_unavailable_report(tmp_path):
    started = Event()

    class BlockingRunner(FakeRunner):
        def run(self, *, stop_event, **kwargs):
            started.set()
            assert stop_event.wait(timeout=2)
            return super().run(stop_event=stop_event, **kwargs)

    with TestClient(create_lab_app(tmp_path, runner_factory=BlockingRunner), base_url="http://localhost:8001") as client:
        valid = headers(client)
        start = client.post("/api/lab/jobs", json=REQUEST, headers=valid)
        job_id = start.json()["job_id"]
        assert started.wait(timeout=1)
        assert client.post("/api/lab/jobs", json=REQUEST, headers=valid).status_code == 409
        assert client.get("/api/lab/jobs/" + job_id + "/report").status_code == 409
        assert client.get("/api/lab/jobs/" + job_id + "/transcript").status_code == 409
        assert client.post("/api/lab/jobs/" + job_id + "/stop", json={}, headers=valid).json()["state"] == "stopping"
        assert wait_for_job(client, job_id)["state"] == "stopped"


def test_job_failure_details_do_not_expose_secret_or_path(tmp_path):
    class FailedRunner:
        def __init__(self, root):
            pass

        def run(self, **kwargs):
            raise RuntimeError("private path and secret token")

    with TestClient(create_lab_app(tmp_path, runner_factory=FailedRunner), base_url="http://localhost") as client:
        job_id = client.post("/api/lab/jobs", json=REQUEST, headers=headers(client)).json()["job_id"]
        job = wait_for_job(client, job_id)
        assert job["state"] == "failed" and job["error"] == "RuntimeError"
        assert "secret" not in json.dumps(job)


def test_runner_cannot_return_arbitrary_output_path(tmp_path):
    class EscapingRunner:
        def __init__(self, root):
            pass

        def run(self, **kwargs):
            return {"run_id": "../real", "report": {}}

    with TestClient(create_lab_app(tmp_path, runner_factory=EscapingRunner), base_url="http://localhost") as client:
        job_id = client.post("/api/lab/jobs", json=REQUEST, headers=headers(client)).json()["job_id"]
        assert wait_for_job(client, job_id)["state"] == "failed"


def test_unknown_job_and_bounded_history(client):
    assert client.get("/api/lab/jobs/missing").status_code == 404
    valid = headers(client)
    for _ in range(22):
        start = client.post("/api/lab/jobs", json=REQUEST, headers=valid)
        wait_for_job(client, start.json()["job_id"])
    assert len(client.get("/api/lab/jobs").json()["jobs"]) == 20


@pytest.mark.parametrize("outcome", ["failed", "unsupported", "timed_out", "stopped"])
def test_runner_non_success_outcome_is_not_marked_completed(tmp_path, outcome):
    class NonSuccessRunner(FakeRunner):
        def run(self, **kwargs):
            result = super().run(**kwargs)
            result["report"]["status"] = outcome
            return result

    with TestClient(create_lab_app(tmp_path, runner_factory=NonSuccessRunner), base_url="http://localhost") as client:
        job_id = client.post("/api/lab/jobs", json=REQUEST, headers=headers(client)).json()["job_id"]
        assert wait_for_job(client, job_id)["state"] == outcome
        assert client.get("/api/lab/jobs/" + job_id + "/report").json()["status"] == outcome


def test_resume_only_trusted_existing_job_with_same_run_id(tmp_path):
    class ResumingRunner(FakeRunner):
        def run(self, **kwargs):
            result = super().run(**kwargs)
            result["report"]["status"] = "stopped"
            return result

        def resume(self, run_id, *, stop_event):
            assert (self.root / run_id).is_dir()
            return {"run_id": run_id, "report": {"status": "completed", "source_kind": "synthetic_ai_generated"}}

    with TestClient(create_lab_app(tmp_path, runner_factory=ResumingRunner), base_url="http://localhost") as client:
        valid = headers(client)
        job_id = client.post("/api/lab/jobs", json=REQUEST, headers=valid).json()["job_id"]
        stopped = wait_for_job(client, job_id)
        response = client.post("/api/lab/jobs/" + job_id + "/resume", json={}, headers=valid)
        assert response.status_code == 202
        resumed = wait_for_job(client, response.json()["job_id"])
        assert resumed["state"] == "completed" and resumed["run_id"] == stopped["run_id"]
        assert client.post("/api/lab/jobs/" + resumed["job_id"] + "/resume", json={}, headers=valid).status_code == 409
        assert client.post("/api/lab/jobs/not-a-known-run/resume", json={}, headers=valid).status_code == 404


def test_real_runner_lab_integrates_isolated_learning_api(tmp_path):
    output = tmp_path / "simulation"
    with TestClient(create_lab_app(output), base_url="http://127.0.0.1:8001") as client:
        request = {**REQUEST, "profile_id": "autonomous", "max_turns": 3}
        job_id = client.post("/api/lab/jobs", json=request, headers=headers(client)).json()["job_id"]
        status = wait_for_job(client, job_id)
        assert status["state"] == "completed", status
        report = client.get("/api/lab/jobs/" + job_id + "/report").json()
        assert report["source_kind"] == "synthetic_ai_generated"
        assert report["turns"] == 3 and report["api_calls"] > 0
        assert not report["educational_effect_claim"] and not report["automatic_promotion_enabled"]
        directory = output / status["run_id"]
        assert (directory / "runtime.sqlite3").is_file()
        assert (directory / "simulation.marker").is_file()
        assert client.get("/api/lab/jobs/" + job_id + "/transcript").json()["events"]

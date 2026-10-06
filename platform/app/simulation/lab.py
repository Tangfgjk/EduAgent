"""Loopback-only simulation workbench, separate from the learner application."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
from threading import Event, Lock
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
from app.simulation.durable_jobs import DurableLabJobs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field


PROFILES = {
    "weak_foundation": "基础薄弱",
    "confident_knowledge_low_confidence": "知识较好但信心不足",
    "autonomous": "自主学习",
    "frustration_sensitive": "挫败敏感",
    "hint_dependent": "提示依赖",
    "misconception": "稳定误区",
}
SCENARIOS = {
    "baseline": "基线",
    "fading": "支架渐撤",
    "zero_gain": "零学习增益对照",
    "retention": "延迟保持",
    "misconception": "误区补练",
}
WEB = Path(__file__).resolve().parents[2] / "web" / "simulation.html"


class LabRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    profile_id: str
    scenario_id: str
    seed: int = Field(default=1, ge=0, le=2**31 - 1)
    max_turns: int = Field(default=20, ge=1, le=100)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_lab_app(output_root: Path, *, runner_factory=None) -> FastAPI:
    """output_root is trusted server configuration, never accepted over HTTP."""
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if runner_factory is None:
        from app.simulation.runner import SimulationRunner

        runner_factory = SimulationRunner
    lock = Lock()
    durable = DurableLabJobs(root)
    try:
        durable.recover()
        jobs: dict[str, dict[str, Any]] = {job["job_id"]: dict(job, stop_event=Event()) for job in durable.recent()}
    except Exception:
        durable.close()
        raise
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="simulation-lab")
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        for job in list(jobs.values()):
            if job["state"] == "queued":
                executor.submit(execute, job["job_id"])
        try:
            yield
        finally:
            with lock:
                for job in jobs.values():
                    job["stop_event"].set()
            executor.shutdown(wait=True, cancel_futures=True)
            durable.close()

    app = FastAPI(title="桂子问津 · 仿真实验室", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        try:
            host = urlsplit("http://" + request.headers.get("host", ""))
        except ValueError:
            return JSONResponse({"detail": "invalid Host"}, status_code=403)
        if host.hostname not in {"localhost", "127.0.0.1", "::1"} or host.username or host.password:
            return JSONResponse({"detail": "loopback Host required"}, status_code=403)
        peer = request.client.host if request.client else ""
        if peer not in {"localhost", "127.0.0.1", "::1", "testclient"}:
            return JSONResponse({"detail": "loopback client required"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "same origin required"}, status_code=403)
        if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
            return JSONResponse({"detail": "same origin required"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = request.headers.get("x-lab-token", "")
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({"detail": "lab token required"}, status_code=403)
            length = request.headers.get("content-length", "")
            if not length.isdigit():
                return JSONResponse({"detail": "bounded content length required"}, status_code=411)
            if int(length) > 4096:
                return JSONResponse({"detail": "request budget exceeded"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'"
        return response

    def public(job):
        return {**{key: job[key] for key in ("job_id", "state", "request", "created_at", "finished_at", "run_id", "error")},
                "resumable": bool(job["report"] and job["report"].get("resumable", job["state"] in {"stopped", "timed_out"}))}

    def find(job_id):
        job = jobs.get(job_id)
        if job is None and re.fullmatch(r"[0-9a-f]{32}", job_id):
            stored = durable.get(job_id)
            if stored:
                job = dict(stored, stop_event=Event())
        if job is None:
            raise HTTPException(404, "job not found")
        return job

    def execute(job_id):
        with lock:
            job = jobs[job_id]
            job["state"] = "stopping" if job["stop_event"].is_set() else "running"
            durable.put(job)
        try:
            runner = runner_factory(root)
            if job.get("resume_run_id"):
                result = runner.resume(job["resume_run_id"], stop_event=job["stop_event"])
            else:
                def started(run_id):
                    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
                        raise ValueError("invalid started run ID")
                    with lock:
                        job["run_id"] = run_id
                        durable.put(job)
                # Preserve injectable runner compatibility; only the real runner needs early IDs.
                from app.simulation.runner import SimulationRunner
                extra = {"on_started": started} if isinstance(runner, SimulationRunner) else {}
                result = runner.run(**job["request"], stop_event=job["stop_event"], **extra)
            run_id = str(result["run_id"])
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id):
                raise ValueError("invalid runner run ID")
            with lock:
                job["run_id"] = run_id
                job["report"] = result["report"]
                outcome = result["report"].get("status", "completed")
                if outcome not in {"completed", "stopped", "failed", "timed_out", "unsupported"}:
                    raise ValueError("invalid runner report status")
                job["state"] = "stopped" if job["stop_event"].is_set() and outcome == "completed" else outcome
                if outcome == "failed":
                    job["error"] = "runner_report_failed"
        except Exception as error:
            with lock:
                job["state"] = "failed"
                # Exception strings may include private paths or credentials.
                job["error"] = type(error).__name__
        finally:
            with lock:
                job["finished_at"] = _now()
                durable.put(job)

    @app.get("/")
    def index():
        return FileResponse(WEB)

    @app.get("/api/lab/config")
    def config():
        return {"profiles": PROFILES, "scenarios": SCENARIOS, "token": token,
                "max_turns": 100, "max_concurrent_jobs": 1, "source_kind": "synthetic_ai_generated",
                "educational_effect_claim": False, "automatic_promotion_enabled": False,
                "renderer": "template", "durable_jobs": True, "single_owner_output_root": True}

    @app.get("/api/lab/jobs")
    def list_jobs():
        with lock:
            return {"jobs": [public(job) for job in reversed(durable.recent())]}

    @app.post("/api/lab/jobs", status_code=202)
    def start(body: LabRunRequest):
        if body.profile_id not in PROFILES or body.scenario_id not in SCENARIOS:
            raise HTTPException(422, "unknown profile or scenario")
        with lock:
            if any(job["state"] in {"queued", "running", "stopping"} for job in jobs.values()):
                raise HTTPException(409, "one active simulation at a time")
            while len(jobs) >= 20:
                jobs.pop(next(iter(jobs)))
            job_id = uuid4().hex
            job = {"job_id": job_id, "state": "queued", "request": body.model_dump(), "created_at": _now(),
                   "finished_at": None, "run_id": None, "error": None, "stop_event": Event(), "report": None}
            jobs[job_id] = job
            durable.put(job)
            response = public(job)
            executor.submit(execute, job_id)
            return response

    @app.get("/api/lab/jobs/{job_id}")
    def status(job_id: str):
        with lock:
            return public(find(job_id))

    @app.post("/api/lab/jobs/{job_id}/stop")
    def stop(job_id: str):
        with lock:
            job = find(job_id)
            if job["state"] in {"queued", "running", "stopping"}:
                job["stop_event"].set()
                job["state"] = "stopping"
                durable.put(job)
            return public(job)

    @app.post("/api/lab/jobs/{job_id}/resume", status_code=202)
    def resume(job_id: str):
        with lock:
            previous = find(job_id)
            if previous["state"] not in {"stopped", "timed_out", "interrupted"} or not previous["run_id"]:
                raise HTTPException(409, "only stopped or timed out jobs can resume")
            if not public(previous)["resumable"]:
                raise HTTPException(409, "learner quit or terminal run cannot resume")
            if any(job["state"] in {"queued", "running", "stopping"} for job in jobs.values()):
                raise HTTPException(409, "one active simulation at a time")
            resumed_id = uuid4().hex
            job = {"job_id": resumed_id, "state": "queued", "request": dict(previous["request"]),
                   "created_at": _now(), "finished_at": None, "run_id": None, "error": None,
                   "stop_event": Event(), "report": None, "resume_run_id": previous["run_id"]}
            while len(jobs) >= 20:
                jobs.pop(next(iter(jobs)))
            jobs[resumed_id] = job
            durable.put(job)
            response = public(job)
            executor.submit(execute, resumed_id)
            return response

    @app.get("/api/lab/jobs/{job_id}/report")
    def report(job_id: str):
        with lock:
            job = find(job_id)
            if job["report"] is None:
                raise HTTPException(409, "report not available")
            return job["report"]

    @app.get("/api/lab/jobs/{job_id}/transcript")
    def transcript(job_id: str):
        with lock:
            run_id = find(job_id)["run_id"]
        if not run_id:
            raise HTTPException(409, "transcript not available")
        path = (root / run_id / "transcript.jsonl").resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "transcript not available")
        if path.stat().st_size > 2_000_000:
            raise HTTPException(413, "transcript display budget exceeded")
        lines = path.read_text(encoding="utf-8").splitlines()
        try:
            return {"events": [json.loads(line) for line in lines[-200:] if line.strip()], "truncated": len(lines) > 200}
        except ValueError:
            raise HTTPException(409, "transcript incomplete") from None

    return app


def main():
    parser = argparse.ArgumentParser(description="Loopback-only synthetic simulation lab")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--output-root", type=Path, help="Trusted local synthetic output directory; never accepted by HTTP")
    arguments = parser.parse_args()
    if not 1024 <= arguments.port <= 65535:
        parser.error("port must be 1024..65535")
    import uvicorn

    output = arguments.output_root or Path(__file__).resolve().parents[2] / "data" / "simulation"
    uvicorn.run(create_lab_app(output), host="127.0.0.1", port=arguments.port)


if __name__ == "__main__":
    main()

"""The exact symptom is a queued run remaining blocked behind an unused 60s lease."""
import pytest

from app.worker import Worker
from tests.test_s02 import database, env
from tests.test_s04 import setup


def test_housekeeping_failure_does_not_strand_a_claimed_teaching_job(env, monkeypatch):
    client, _, path = setup(env)
    worker = Worker(env.db, enable_models=False)
    original = worker.reap
    failed = False

    def intermittent_housekeeping():
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError('injected housekeeping interruption')
        original()

    monkeypatch.setattr(worker, 'reap', intermittent_housekeeping)
    # Mimic the production supervisor continuing after one unavailable cycle.
    with pytest.raises(RuntimeError):
        worker.step()
    worker.step()
    assert client.get(path).json()['status'] == 'awaiting_input', 'teaching run stayed queued behind an unused lease'


def test_embedded_supervisor_survives_unavailable_cycle_without_exposing_exception(env, monkeypatch, caplog):
    from threading import Event
    from app.worker import run_loop
    client, _, path = setup(env)
    worker = Worker(env.db, enable_models=False)
    original = worker.step
    stopped = Event()
    calls = 0

    def interrupted_step():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('SECRET: simulated SQL/student text must never be logged')
        result = original()
        stopped.set()
        return result

    monkeypatch.setattr(worker, 'step', interrupted_step)
    run_loop(worker, stop=stopped, poll_seconds=0, lane='test')
    assert calls == 2 and client.get(path).json()['status'] == 'awaiting_input'
    assert 'worker_cycle_unavailable' in caplog.text and 'worker_cycle_recovered' in caplog.text
    assert 'SECRET' not in caplog.text and 'SQL/student' not in caplog.text

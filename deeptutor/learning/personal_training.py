"""Owner/workspace-scoped BKT parameter fitting with chronological holdout."""

from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
import time
import uuid

from deeptutor.learning.bkt import BKTParameters, update
from deeptutor.learning.diagnostic_datasets import create_snapshot


def connection():
    import psycopg

    from deeptutor.runtime.home import get_runtime_home

    config = get_runtime_home() / "data/system/postgres/connection.json"
    return psycopg.connect(**json.loads(config.read_text()), connect_timeout=5)


def scope(store):
    from deeptutor.services.storage.postgres_adapter import resource

    registered = resource(store.db_path)
    return (
        registered["scope_id"]
        if registered
        else hashlib.sha256(str(store.db_path.resolve()).encode()).hexdigest()
    )


def initialize(c):
    c.execute(
        "CREATE TABLE IF NOT EXISTS personal_active_models (scope text NOT NULL,project text NOT NULL,run_id text,previous_id text,PRIMARY KEY(scope,project))"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS personal_training_runs (scope text NOT NULL, project text NOT NULL, id text NOT NULL, created double precision NOT NULL, body jsonb NOT NULL, PRIMARY KEY(scope,project,id))"
    )


def runs(store, path_id):
    if store.load(path_id) is None:
        raise LookupError("Learning project not found")
    with connection() as c:
        initialize(c)
        rows = [
            row[0]
            for row in c.execute(
                "SELECT body FROM personal_training_runs WHERE scope=%s AND project=%s ORDER BY created DESC LIMIT 30",
                (scope(store), path_id),
            )
        ]
        active = c.execute(
            "SELECT run_id FROM personal_active_models WHERE scope=%s AND project=%s",
            (scope(store), path_id),
        ).fetchone()
        return [dict(row, activated=bool(active and row["id"] == active[0])) for row in rows]


def score(sequences, params, fraction=0.8):
    import math

    loss = brier = 0.0
    n = 0
    for observations in sequences:
        cut = max(1, int(len(observations) * fraction))
        known = params.prior
        for i, correct in enumerate(observations):
            predicted = min(
                1 - 1e-9, max(1e-9, known * (1 - params.slip) + (1 - known) * params.guess)
            )
            if i >= cut:
                loss -= correct * math.log(predicted) + (1 - correct) * math.log(1 - predicted)
                brier += (predicted - correct) ** 2
                n += 1
            known = update(known, bool(correct), params)
    return {"log_loss": loss / n if n else None, "brier": brier / n if n else None, "n": n}


def fit_rows(rows, baseline=None):
    from scipy.optimize import minimize

    grouped = defaultdict(list)
    for row in sorted(rows, key=lambda x: (x["timestamp"], x["observation_id"])):
        grouped[row["sequence_id"]].append(int(row["correct"]))
    sequences = list(grouped.values())
    baseline = baseline or BKTParameters()
    result = {
        "status": "insufficient_data",
        "parameters": asdict(baseline),
        "n": len(rows),
        "sequences": len(sequences),
        "reason": "需要至少 50 条独立记录、至少两个有效序列，每个序列至少 10 次作答，并包含正确与错误。",
    }
    if (
        len(rows) < 50
        or len(sequences) < 2
        or any(len(s) < 10 for s in sequences)
        or set(v for s in sequences for v in s) != {0, 1}
    ):
        return result
    # Fit on prefixes only. Held-out suffix labels never enter the optimizer.
    prefixes = [s[: max(1, int(len(s) * 0.8))] for s in sequences]
    import math

    def objective(values):
        p = BKTParameters(*map(float, values))
        total = 0.0
        for sequence in prefixes:
            known = p.prior
            for correct in sequence:
                pred = min(1 - 1e-9, max(1e-9, known * (1 - p.slip) + (1 - known) * p.guess))
                total -= correct * math.log(pred) + (1 - correct) * math.log(1 - pred)
                known = update(known, bool(correct), p)
        return total

    candidates = [
        minimize(
            objective,
            start,
            method="L-BFGS-B",
            bounds=[(0.01, 0.99), (0.001, 0.5), (0.01, 0.4), (0.01, 0.4)],
        )
        for start in ([0.2, 0.1, 0.2, 0.1], [0.5, 0.05, 0.1, 0.2], [0.1, 0.2, 0.3, 0.1])
    ]
    valid = [c for c in candidates if c.success and math.isfinite(c.fun)]
    if not valid:
        return dict(result, status="failed", reason="优化未收敛，保留现有参数。")
    fitted = BKTParameters(*map(float, min(valid, key=lambda c: c.fun).x))
    old, new = score(sequences, baseline), score(sequences, fitted)
    accepted = new["n"] >= 10 and new["log_loss"] < old["log_loss"] and new["brier"] <= old["brier"]
    return dict(
        result,
        status="validated_candidate" if accepted else "rejected",
        parameters=asdict(fitted),
        baseline_metrics=old,
        validation_metrics=new,
        split="per-sequence chronological prefix 80% / suffix 20%",
        reason="个人候选参数通过时间留出检查；不代表教学效果或跨学生泛化已验证。"
        if accepted
        else "验证未优于默认参数，保留原参数。",
    )


def record_model_event(c, store, path_id, event_type, payload):
    """Record model change and project revision in the same PostgreSQL transaction."""
    from psycopg import sql

    from deeptutor.services.storage.postgres_adapter import enabled, schema_name

    if not enabled(store.db_path):
        return False
    schema = sql.Identifier(schema_name(store.db_path))
    table = sql.SQL("{}.mastery_paths").format(schema)
    row = c.execute(
        sql.SQL("SELECT state_json,revision FROM {} WHERE path_id=%s FOR UPDATE").format(table),
        (path_id,),
    ).fetchone()
    if row is None:
        raise LookupError("Learning project not found")
    state = json.loads(row[0])
    revision = row[1] + 1
    now = time.time()
    state["version"] = revision
    state["updated_at"] = now
    c.execute(
        sql.SQL(
            "UPDATE {} SET state_json=%s,revision=%s,updated_at=%s WHERE path_id=%s AND revision=%s"
        ).format(table),
        (json.dumps(state, ensure_ascii=False), revision, now, path_id, row[1]),
    )
    c.execute(
        sql.SQL(
            "INSERT INTO {}.mastery_events(path_id,revision,event_type,payload_json,session_id,turn_id,created_at) VALUES (%s,%s,%s,%s,'','',%s)"
        ).format(schema),
        (path_id, revision, event_type, json.dumps(payload), now),
    )
    return True


def train(store, path_id, revision, snapshot_id=None):
    from psycopg.types.json import Jsonb

    # Advisory lock serializes one user's project training, including snapshot creation.
    with connection() as c:
        initialize(c)
        key = scope(store)
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (key + path_id,))
        existing = c.execute(
            "SELECT body FROM personal_training_runs WHERE scope=%s AND project=%s AND body->>'source_revision'=%s ORDER BY created DESC LIMIT 1",
            (key, path_id, str(revision)),
        ).fetchone()
        if existing:
            return existing[0]
        from deeptutor.learning.diagnostic_datasets import snapshots

        snapshot = (
            snapshots(store, path_id, snapshot_id)
            if snapshot_id
            else create_snapshot(store, path_id, revision)
        )
        active = c.execute(
            "SELECT r.body FROM personal_training_runs r JOIN personal_active_models a ON (r.scope=a.scope AND r.project=a.project AND r.id=a.run_id) WHERE a.scope=%s AND a.project=%s",
            (key, path_id),
        ).fetchone()
        baseline = BKTParameters(**active[0]["parameters"]) if active else BKTParameters()
        result = fit_rows(snapshot["rows"], baseline)
        run = dict(
            result,
            id=uuid.uuid4().hex,
            created_at=time.time(),
            source_revision=revision,
            snapshot_id=snapshot["id"],
            snapshot_digest=snapshot["digest"],
            project_id=path_id,
            scope="current_user_only",
            plan_authority="tutor_agent",
            activated=False,
            objective_scopes={
                row["objective_id"]: row["context"]["objective_scope"]
                for row in snapshot["provenance"]
                if row["eligibility"] == "eligible"
            },
        )
        c.execute(
            "INSERT INTO personal_training_runs VALUES (%s,%s,%s,%s,%s)",
            (key, path_id, run["id"], run["created_at"], Jsonb(run)),
        )
        if run["status"] == "validated_candidate":
            progress = store.load(path_id)
            from deeptutor.learning.diagnosis import _digest

            current = {
                kp.id: _digest(kp.model_dump(mode="json"))
                for m in progress.modules
                for kp in m.knowledge_points
            }
            if all(current.get(k) == v for k, v in run["objective_scopes"].items()):
                c.execute(
                    "CREATE TABLE IF NOT EXISTS personal_active_models (scope text NOT NULL,project text NOT NULL,run_id text,previous_id text,PRIMARY KEY(scope,project))"
                )
                prior = c.execute(
                    "SELECT run_id FROM personal_active_models WHERE scope=%s AND project=%s FOR UPDATE",
                    (key, path_id),
                ).fetchone()
                c.execute(
                    "INSERT INTO personal_active_models VALUES (%s,%s,%s,%s) ON CONFLICT(scope,project) DO UPDATE SET run_id=EXCLUDED.run_id,previous_id=EXCLUDED.previous_id",
                    (key, path_id, run["id"], prior[0] if prior else None),
                )
                run["activated"] = True
                record_model_event(
                    c,
                    store,
                    path_id,
                    "diagnostic.model_activated",
                    {"run_id": run["id"], "previous_id": prior[0] if prior else None},
                )
                c.execute(
                    "UPDATE personal_training_runs SET body=%s WHERE scope=%s AND project=%s AND id=%s",
                    (Jsonb(run), key, path_id, run["id"]),
                )
        return run


def active_parameters(store, path_id, objective_id, objective_scope):
    from deeptutor.runtime.home import get_runtime_home

    # A local SQLite-only installation still uses default diagnostic parameters.
    # Configured PostgreSQL outages must propagate rather than silently swapping models.
    if not (get_runtime_home() / "data/system/postgres/connection.json").exists():
        return None
    with connection() as c:
        initialize(c)
        c.execute(
            "CREATE TABLE IF NOT EXISTS personal_active_models (scope text NOT NULL,project text NOT NULL,run_id text,previous_id text,PRIMARY KEY(scope,project))"
        )
        row = c.execute(
            "SELECT r.body FROM personal_training_runs r JOIN personal_active_models a ON (r.scope=a.scope AND r.project=a.project AND r.id=a.run_id) WHERE a.scope=%s AND a.project=%s",
            (scope(store), path_id),
        ).fetchone()
        if row and row[0].get("objective_scopes", {}).get(objective_id) == objective_scope:
            return BKTParameters(**row[0]["parameters"]), row[0]["id"]
    return None


def rollback_parameters(store, path_id, expected_run_id):
    if store.load(path_id) is None:
        raise LookupError("Learning project not found")
    with connection() as c:
        key = scope(store)
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (key + path_id,))
        row = c.execute(
            "SELECT run_id,previous_id FROM personal_active_models WHERE scope=%s AND project=%s FOR UPDATE",
            (key, path_id),
        ).fetchone()
        if not row or row[0] != expected_run_id:
            raise RuntimeError("生效模型已变化，请刷新后重试")
        c.execute(
            "UPDATE personal_active_models SET run_id=%s,previous_id=%s WHERE scope=%s AND project=%s",
            (row[1], row[0], key, path_id),
        )
        recorded = record_model_event(
            c,
            store,
            path_id,
            "diagnostic.model_rolled_back",
            {"from_run_id": row[0], "to_run_id": row[1]},
        )
    if not recorded:
        store.mutate(
            path_id,
            lambda tx: tx.emit(
                "diagnostic.model_rolled_back", {"from_run_id": row[0], "to_run_id": row[1]}
            ),
        )
    return {"active_run_id": row[1], "restored_defaults": row[1] is None}

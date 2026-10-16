"""独立解包 wheel 验证真实 API，防止可编辑安装掩盖缺失资源。

用法：python scripts/check_wheel.py .local/dist/rsi_platform-0.1.0-py3-none-any.whl
当前解释器须已装项目依赖和 dev 依赖；子进程 -I 从临时目录运行。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile


SMOKE = r'''
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import app
import evolution.l0_digest
assert Path(app.__file__).resolve().is_relative_to(root)
assert Path(evolution.l0_digest.__file__).resolve().is_relative_to(root)

import httpx
def deny_network(*args, **kwargs):
    raise AssertionError("wheel smoke must not access external HTTP")
httpx.HTTPTransport.handle_request = deny_network

from fastapi.testclient import TestClient
from app.config import Settings
from app.gateway.routes import create_app
from app.llm.client import FakeLLM
from app.orchestration.session import load_bank
from app.storage.db import Store

bank = load_bank()
assert bank
store = Store(":memory:")
try:
    with TestClient(create_app(Settings(), FakeLLM(), store)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        contract = client.post("/api/contracts", json={"goal_text": "学习一元一次方程"})
        assert contract.status_code == 200
        session = client.post("/api/sessions", json={"session_type": "explore"})
        assert session.status_code == 200
        sid = session.json()["session_id"]
        result = client.post(f"/api/sessions/{sid}/messages", json={"answer": "3"})
        assert result.json()["ui"]["verdict"]["status"] == "passed"
        digest = client.post("/api/evolution/digest")
        assert digest.status_code == 200 and "kpis" in digest.json()
        print(json.dumps({"wheel_smoke": "passed", "bank_items": len(bank), "external_http": "blocked"}))
finally:
    store.conn.close()

from app.gateway.v1 import create_app as create_v1_app
from app.infrastructure.db.database import Database
from app.modules.identity.service import Identity
db = Database("sqlite://")
try:
    db.migrate()
    Identity(db).bootstrap("wheel.admin", "Wheel-test-only-123456", "开发检查")
    with TestClient(create_v1_app(Settings(), db)) as client:
        login = client.post("/api/v1/auth/login", json={"username": "wheel.admin", "password": "Wheel-test-only-123456"})
        assert login.status_code == 200
        assert client.get("/api/v1/auth/me").json()["account"]["role"] == "admin"
        assert client.post("/api/contracts", json={"goal_text": "x"}).status_code == 404
        assert client.get("/health/ready").status_code == 200
        print(json.dumps({"s02_wheel_smoke": "passed", "migrations": "bundled", "legacy_routes": "not_mounted"}))
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        assert client.post("/api/v1/admin/students", json={"username": "wheel.student", "password": "Wheel-test-only-123456", "display_name": "测试学生"}).status_code == 201
        login = client.post("/api/v1/auth/login", json={"username": "wheel.student", "password": "Wheel-test-only-123456"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        space = client.post("/api/v1/student/workspaces", headers={"Idempotency-Key": "wheel-space"}, json={"kind": "project", "title": "测试计划"}).json()
        path = "/api/v1/student/workspaces/" + space["workspace_id"] + "/runs"
        run = client.post(path, headers={"Idempotency-Key": "wheel-plan"}, json={"goal": "理解等式性质"})
        assert run.status_code == 202, run.text
        from app.worker import Worker
        assert Worker(db).step()
        run_path = path + "/" + run.json()["run_id"]
        run = client.get(run_path).json()
        assert run["status"] == "awaiting_input"
        assert client.post(run_path + "/resume", headers={"Idempotency-Key": "wheel-confirm"}, json={"action": "confirm", "gate_id": run["gate_id"], "expected_revision": run["revision"], "daily_minutes": 15}).status_code == 202
        assert Worker(db).step()
        assert client.get(run_path).json()["status"] == "completed"
        print(json.dumps({"s04_wheel_smoke": "passed", "langgraph_interrupt_resume": "passed"}))
        login = client.post("/api/v1/auth/login", json={"username": "wheel.admin", "password": "Wheel-test-only-123456"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        content = client.get("/api/v1/admin/content").json()
        assert client.post("/api/v1/admin/content/development-release", json={"content_version": content["content_version"], "expected_revision": content["revision"], "enabled": True, "acknowledge_development_only": True}).status_code == 200
        login = client.post("/api/v1/auth/login", json={"username": "wheel.student", "password": "Wheel-test-only-123456"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        tutor_base = "/api/v1/student/workspaces/" + space["workspace_id"] + "/tutoring"
        run = client.post(tutor_base, headers={"Idempotency-Key": "wheel-tutor"}, json={"unit_id": content["unit_id"]})
        assert run.status_code == 202
        tutor_path = tutor_base + "/" + run.json()["run_id"]
        Worker(db).step()
        run = client.get(tutor_path).json()
        assert run["view"]["phase"] == "diagnostic"
        assert client.post(tutor_path + "/inputs", headers={"Idempotency-Key": "wheel-answer"}, json={"action": "answer", "answer": "x=6/2", "gate_id": run["gate_id"], "expected_revision": run["revision"]}).status_code == 202
        Worker(db).step()
        assert client.get(tutor_path).json()["view"]["feedback"]["status"] == "passed"
        print(json.dumps({"s05_wheel_smoke": "passed", "tutor_graph": "passed", "content_gate": "passed"}))
        run = client.get(tutor_path).json()
        assert client.post(tutor_path + "/inputs", headers={"Idempotency-Key": "wheel-next"}, json={"action": "next", "gate_id": run["gate_id"], "expected_revision": run["revision"]}).status_code == 202
        Worker(db).step()
        run = client.get(tutor_path).json()
        assert client.post(tutor_path + "/inputs", headers={"Idempotency-Key": "wheel-wrong"}, json={"action": "answer", "answer": "0", "gate_id": run["gate_id"], "expected_revision": run["revision"]}).status_code == 202
        Worker(db).step()
        scope = "/api/v1/student/workspaces/" + space["workspace_id"]
        card = client.get(scope + "/review-cards").json()["items"][0]
        result = client.post(scope + "/review-cards/" + card["card_id"] + "/attempts", headers={"Idempotency-Key": "wheel-recall"}, json={"answer": "6", "expected_revision": card["revision"]})
        assert result.status_code == 200 and result.json()["attempt"]["status"] == "passed"
        memory = client.get(scope + "/memory").json()["items"][0]
        assert memory["observations"]["engine"] == "observations.v1"
        assert client.get(scope + "/facts/" + card["source_fact_ids"][0]).json()["answer"] == "0"
        print(json.dumps({"s06_wheel_smoke": "passed", "recall": "passed", "provenance": "passed"}))
        run = client.get(tutor_path).json()
        assert client.post(tutor_path + "/cancel", json={"expected_revision": run["revision"]}).status_code == 200
        login = client.post("/api/v1/auth/login", json={"username": "wheel.admin", "password": "Wheel-test-only-123456"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        content = client.get("/api/v1/admin/activity-content").json()
        assert client.post("/api/v1/admin/activity-content/development-release", json={"content_version": content["content_version"], "expected_revision": content["revision"], "enabled": True, "acknowledge_development_only": True}).status_code == 200
        login = client.post("/api/v1/auth/login", json={"username": "wheel.student", "password": "Wheel-test-only-123456"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        for kind in ["roundtable", "teach"]:
            response = client.post(scope + "/" + kind, headers={"Idempotency-Key": "wheel-" + kind}, json={"unit_id": content["unit_id"]})
            assert response.status_code == 202, response.text
            path = scope + "/" + kind + "/" + response.json()["run_id"]
            Worker(db).step()
            for action in ["contribute", "conclude"]:
                run = client.get(path).json()
                assert client.post(path + "/inputs", headers={"Idempotency-Key": action}, json={"action": action, "answer": "6", "reasoning": "两边加7，再除以2并代入检查", "gate_id": run["gate_id"], "expected_revision": run["revision"]}).status_code == 202
                Worker(db).step()
            run = client.get(path).json()
            assert run["status"] == "completed" and run["view"]["check"]["status"] == "passed"
        print(json.dumps({"s07_wheel_smoke": "passed", "roundtable_teach": "passed"}))
        evidence = client.get(scope + "/evidence?days=14").json()
        assert evidence["groups"][0]["activity_contributions"] == 4
        assert evidence["groups"][0]["answers"] == 3
        assert evidence["groups"][0]["checkpoint_answers"] == 0
        assert all(row["score"] is None for row in evidence["competencies"])
        assert client.get("/api/v1/student/evidence").json()["workspace_count"] == 1
        print(json.dumps({"s08_wheel_smoke": "passed", "evidence_scope_and_provenance": "passed"}))
        login = client.post("/api/v1/auth/login", json={"username": "wheel.admin", "password": "Wheel-test-only-123456"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        material = client.post("/api/v1/admin/planning-materials", json={"title": "方程自编参考", "kind": "development_reference", "edition": "dev1", "source_ref": "独立安装包验收自编参考", "body": "等式两边同时变形，并将结果代入核验。"}).json()
        assert client.post("/api/v1/admin/planning-materials/" + material["material_id"] + "/release", json={"enabled": True, "expected_revision": material["revision"], "acknowledge_development_only": True}).status_code == 200
        login = client.post("/api/v1/auth/login", json={"username": "wheel.student", "password": "Wheel-test-only-123456"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        response = client.post(scope + "/strategies", headers={"Idempotency-Key": "wheel-strategy"}, json={"daily_minutes": 20, "reflection_focus": "verification", "learning_experience": "我想加强检查与反思", "material_ids": [material["material_id"]]})
        assert response.status_code == 202, response.text
        strategy_path = scope + "/strategies/" + response.json()["run_id"]
        Worker(db).step()
        for username, action in [("wheel.admin", "approve"), ("wheel.student", "confirm")]:
            run = client.get(strategy_path).json()
            login = client.post("/api/v1/auth/login", json={"username": username, "password": "Wheel-test-only-123456"})
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            path = "/api/v1/admin/strategies/" + run["run_id"] if action == "approve" else strategy_path
            assert client.post(path + "/decision", headers={"Idempotency-Key": action}, json={"action": action, "expected_revision": run["revision"], "gate_id": run["gate_id"], "note": "核对开发使用边界后自行确认"}).status_code == 202
            Worker(db).step()
            if action == "approve":
                login = client.post("/api/v1/auth/login", json={"username": "wheel.student", "password": "Wheel-test-only-123456"})
                client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        active = client.get(scope + "/active-strategy").json()
        assert active["effective"] and active["policy"]["daily_minutes"] == 20
        assert client.get(strategy_path + "/skill").status_code == 200
        assert client.post(scope + "/active-strategy/rollback", headers={"Idempotency-Key": "rollback"}, json={"expected_revision": active["revision"], "target_version": None, "note": "验证恢复默认基线"}).json()["version_id"] is None
        print(json.dumps({"s09_wheel_smoke": "passed", "double_gate_activation_rollback": "passed"}))
        catalog = client.get("/api/v1/student/content-catalog").json()
        assert len(catalog["items"]) == 5 and all(not item["tasks"] for item in catalog["items"])
        login = client.post("/api/v1/auth/login", json={"username": "wheel.admin", "password": "Wheel-test-only-123456"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        content = next(item for item in client.get("/api/v1/admin/content-catalog").json()["items"] if item["unit_id"] == "math.g9.probability.dev1")
        assert client.post("/api/v1/admin/content-catalog/" + content["unit_id"] + "/development-release", json={"content_version": content["content_version"], "expected_revision": content["revision"], "enabled": True, "acknowledge_development_only": True}).status_code == 200
        login = client.post("/api/v1/auth/login", json={"username": "wheel.student", "password": "Wheel-test-only-123456"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        response = client.post(tutor_base, headers={"Idempotency-Key": "wheel-probability"}, json={"unit_id": content["unit_id"]})
        assert response.status_code == 202, response.text
        path = tutor_base + "/" + response.json()["run_id"]
        Worker(db).step()
        run = client.get(path).json()
        assert run["view"]["task"]["task_id"] == "D-PRB-01"
        assert client.post(path + "/inputs", headers={"Idempotency-Key": "wheel-probability-answer"}, json={"action": "answer", "answer": "1/6", "gate_id": run["gate_id"], "expected_revision": run["revision"]}).status_code == 202
        Worker(db).step()
        assert client.get(path).json()["view"]["feedback"]["status"] == "passed"
        print(json.dumps({"s10_wheel_smoke": "passed", "catalog_pack_tutoring": "passed"}))
        run = client.get(path).json()
        assert client.post(path + "/cancel", json={"expected_revision":run["revision"]}).status_code == 200
        login = client.post("/api/v1/auth/login", json={"username":"wheel.admin", "password":"Wheel-test-only-123456"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        activity = next(item for item in client.get("/api/v1/admin/activity-catalog").json()["items"] if item["parent_unit_id"]=="math.g9.probability.dev1")
        assert client.post("/api/v1/admin/activity-catalog/"+activity["unit_id"]+"/development-release", json={"content_version":activity["content_version"],"expected_revision":activity["revision"],"enabled":True,"acknowledge_development_only":True}).status_code==200
        login = client.post("/api/v1/auth/login", json={"username":"wheel.student", "password":"Wheel-test-only-123456"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        for kind in ["roundtable","teach"]:
            response = client.post(scope+"/"+kind, headers={"Idempotency-Key":"probability-"+kind}, json={"unit_id":activity["unit_id"]})
            assert response.status_code==202, response.text
            activity_path = scope+"/"+kind+"/"+response.json()["run_id"]
            Worker(db).step()
            for action,answer in [("contribute","1/2"),("conclude","2/5")]:
                run=client.get(activity_path).json()
                assert client.post(activity_path+"/inputs", headers={"Idempotency-Key":kind+action}, json={"action":action,"answer":answer,"reasoning":"每个球等可能，共5球有2个蓝球，不能按颜色种类计数", "gate_id":run["gate_id"],"expected_revision":run["revision"]}).status_code==202
                Worker(db).step()
            run=client.get(activity_path).json()
            assert run["status"]=="completed" and run["view"]["check"]["status"]=="passed"
            assert run["view"]["activity_version"]==activity["content_version"]
        print(json.dumps({"s10_probability_activities_wheel_smoke":"passed"}))


finally:
    db.close()
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    with ZipFile(args.wheel) as archive:
        required = {"app/__init__.py", "evolution/l0_digest.py", "seeds/question_bank.json", "web/index.html",
                    "app/infrastructure/db/migrations/env.py", "app/infrastructure/db/migrations/versions/s06_001.py", "app/modules/memory/projections.py", "app/modules/learning/development_content.py", "app/worker.py", "app/modules/learning/activity_graph.py", "app/gateway/activities.py", "app/modules/evidence/service.py", "app/gateway/evidence.py", "app/modules/planning/service.py", "app/infrastructure/db/migrations/versions/s09_001.py", "app/modules/learning/content_catalog.py", "app/modules/learning/activity_catalog.py"}
        missing = required.difference(archive.namelist())
        if missing:
            raise SystemExit(f"Wheel 缺少运行依赖: {', '.join(sorted(missing))}")
        # 拒绝越过临时目录的条目；本脚本只执行本项目自行构建的 wheel。
        for name in archive.namelist():
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise SystemExit(f"非法 wheel 路径: {name}")
        with tempfile.TemporaryDirectory(prefix="wenjin-wheel-") as temp:
            archive.extractall(temp)
            subprocess.run([sys.executable, "-I", "-c", SMOKE, temp], cwd=temp, check=True, timeout=30)


if __name__ == "__main__":
    main()

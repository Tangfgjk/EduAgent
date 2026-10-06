from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from app.school.application import create_school_app
from app.school.identity import SchoolDenied, SchoolDirectory, SchoolPrincipal


NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
PASSWORD = "test-only-fixture-password"


@pytest.fixture
def school(tmp_path):
    clock = [NOW]
    directory = SchoolDirectory(tmp_path / "directory.sqlite3", clock=lambda: clock[0])
    users = {}
    for tenant in ("school-a", "school-b"):
        directory.provision_tenant(tenant)
        for group in ("class-1", "class-2"):
            directory.provision_class(tenant, group)
        for username, role, group in (("student", "student", "class-1"), ("other", "student", "class-2"),
                                      ("teacher", "teacher", "class-1"), ("parent", "parent", "class-1")):
            learner_id = f"{tenant}-{username}" if role == "student" else None
            account = directory.provision_account(tenant, username, PASSWORD, role, learner_id=learner_id)
            directory.membership(account, tenant, group)
            users[tenant, username] = account
        for role in ("teacher", "parent"):
            directory.assign(users[tenant, role], f"{tenant}-student", tenant, "class-1")
    app = create_school_app(directory, tmp_path / "learner-data")
    client = TestClient(app, base_url="http://127.0.0.1")
    yield directory, users, app, client, clock, tmp_path
    client.close()
    app.state.learner_instances.close()
    directory.close()


def token(client, tenant="school-a", user="student"):
    response = client.post("/api/school/login", json={"tenant_id": tenant, "username": user, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["access_token"]


def headers(value):
    return {"Authorization": "Bearer " + value}


def consent(client, value, learner="school-a-student", scopes=None, version="c1"):
    return client.post(f"/api/school/learners/{learner}/consent", headers=headers(value),
                       json={"scopes": scopes if scopes is not None else ["teaching"], "version": version, "source": "forged-client-source"})


def test_account_and_hashed_session_persistence_no_public_registration(school):
    directory, _, _, client, _, tmp_path = school
    value = token(client)
    row = directory.conn.execute("SELECT token_sha256 FROM bearer_sessions").fetchone()[0]
    assert value not in row
    assert len(row) == 64
    hashes = [row[0] for row in directory.conn.execute("SELECT password_hash FROM accounts")]
    assert all(PASSWORD not in value and value.startswith("pbkdf2_sha256$") for value in hashes)
    reopened = SchoolDirectory(tmp_path / "directory.sqlite3", clock=lambda: NOW)
    try:
        assert reopened.authenticate(value).role == "student"
    finally:
        reopened.close()
    assert client.post("/api/school/register", json={"role": "teacher"}).status_code == 404


@pytest.mark.parametrize("user", ["student", "teacher", "parent"])
def test_server_roles_only_and_tenant_class_assignment(school, user):
    _, _, _, client, _, _ = school
    value = token(client, user=user)
    response = client.get("/api/school/learners", headers=headers(value) | {"X-Role": "privacy_officer"})
    assert response.status_code == 200
    assert response.json()["role"] == user
    assert response.json()["learners"] == ["school-a-student"]
    for learner in ("school-b-student", "school-a-other", "unknown"):
        assert client.get(f"/api/school/learners/{learner}/state", headers=headers(value)).status_code == 403


def test_student_consent_source_server_owned_and_no_research_or_role_escalation(school):
    _, users, app, client, _, _ = school
    value = token(client)
    assert consent(client, value).status_code == 200
    store, _ = app.state.learner_instances.get("school-a", "school-a-student")
    from app.learning.service import LearningService
    assert LearningService(store).consent("school-a-student")["source"] == "school-account:" + users["school-a", "student"]
    assert consent(client, value, scopes=["teaching", "research"], version="research").status_code == 403
    assert client.post("/api/school/login", json={"tenant_id": "school-a", "username": "student", "password": PASSWORD, "role": "teacher"}).status_code == 422
    assert consent(client, value, learner="school-a-other").status_code == 403


@pytest.mark.parametrize("user", ["teacher", "parent"])
def test_assigned_staff_read_requires_student_consent_and_cannot_write(school, user):
    _, _, _, client, _, _ = school
    staff = token(client, user=user)
    learner = token(client)
    assert client.get("/api/school/learners/school-a-student/state", headers=headers(staff)).status_code == 403
    assert consent(client, learner).status_code == 200
    assert client.get("/api/school/learners/school-a-student/state", headers=headers(staff)).status_code == 403
    shared = client.post("/api/school/learners/school-a-student/sharing", headers=headers(learner), json={"audiences": [user], "expected_revision": 0})
    assert shared.status_code == 200
    assert client.get("/api/school/learners/school-a-student/state", headers=headers(staff)).status_code == 200
    assert consent(client, staff).status_code == 403
    assert client.post("/api/school/learners/school-a-student/issue", headers=headers(staff),
        json={"assessment_id": "D-SOLVE-1", "issuance_id": "staff-issue"}).status_code == 403
    assert consent(client, learner, scopes=[], version="withdraw").status_code == 200
    assert client.get("/api/school/learners/school-a-student/evidence", headers=headers(staff)).status_code == 403


def test_student_ticketed_issue_submit_and_each_learner_database_is_physically_separate(school):
    _, _, app, client, _, _ = school
    first = token(client)
    second = token(client, tenant="school-b")
    assert consent(client, first).status_code == 200
    assert consent(client, second, learner="school-b-student").status_code == 200
    issued = client.post("/api/school/learners/school-a-student/issue", headers=headers(first),
        json={"assessment_id": "D-SOLVE-1", "issuance_id": "student-issue"})
    assert issued.status_code == 200
    payload = issued.json()
    assert "answer" not in payload.get("assessment", {})
    assert "delivery_ref" in payload
    from app.core.schema import utcnow
    body = {"assessment_id": "D-SOLVE-1", "attempt_id": "school-attempt", "answer": "x=3",
            "occurred_at": utcnow().isoformat(), "delivery_ref": payload["delivery_ref"]}
    submitted = client.post("/api/school/learners/school-a-student/submit", headers=headers(first), json=body)
    assert submitted.status_code == 200
    assert client.post("/api/school/learners/school-a-student/submit", headers=headers(first), json=body).json() == submitted.json()
    assert len(client.get("/api/school/learners/school-a-student/evidence", headers=headers(first)).json()["evidence"]) == 1
    assert client.get("/api/school/learners/school-b-student/evidence", headers=headers(second)).json()["evidence"] == []
    stores = [app.state.learner_instances.get(tenant, f"{tenant}-student")[0] for tenant in ("school-a", "school-b")]
    files = [store.conn.execute("PRAGMA database_list").fetchone()[2] for store in stores]
    assert files[0] != files[1]
    assert all("school-a" not in path and "school-b" not in path for path in files)


@pytest.mark.parametrize("path", ["/api/learning/teacher/school-a-student/corrections", "/api/governance/reviews", "/api/agent/message", "/api/learning/consent/school-a-student", "/docs", "/openapi.json"])
def test_all_legacy_highpriv_token_paths_denied(school, path):
    _, _, _, client, _, _ = school
    value = token(client, user="teacher")
    response = client.post(path, headers=headers(value) | {"X-Teacher-Token": "legacy-owner-token", "X-Parent-Token": "legacy-parent-token"}, json={})
    assert response.status_code == 403


def test_revoke_expiry_and_account_deactivation_enforced(school):
    directory, users, _, client, clock, _ = school
    value = token(client)
    assert client.post("/api/school/logout", headers=headers(value)).status_code == 200
    assert client.get("/api/school/learners", headers=headers(value)).status_code == 401
    value = token(client)
    clock[0] += timedelta(hours=9)
    assert client.get("/api/school/learners", headers=headers(value)).status_code == 401
    clock[0] = NOW
    value = token(client)
    with directory.conn:
        directory.conn.execute("UPDATE accounts SET active=0 WHERE account_id=?", (users["school-a", "student"],))
    assert client.get("/api/school/learners", headers=headers(value)).status_code == 401


def test_live_membership_removal_and_cross_tenant_provision_fail_closed(school):
    directory, users, _, client, _, _ = school
    teacher = token(client, user="teacher")
    with directory.conn:
        directory.conn.execute("DELETE FROM memberships WHERE account_id=?", (users["school-a", "teacher"],))
    assert client.get("/api/school/learners", headers=headers(teacher)).json()["learners"] == []
    with pytest.raises(SchoolDenied):
        directory.assign(users["school-b", "teacher"], "school-a-student", "school-a", "class-1")
    with pytest.raises(SchoolDenied):
        directory.membership(users["school-b", "student"], "school-a", "class-1")


def test_forged_inprocess_principal_rejected_and_login_rate_limit(school):
    directory, users, _, client, _, _ = school
    forged = SchoolPrincipal(users["school-a", "student"], "school-a", "teacher")
    with pytest.raises(SchoolDenied):
        directory.visible_learners(forged)
    for _ in range(5):
        with pytest.raises(SchoolDenied):
            directory.login("school-a", "student", "wrong-pass")
    with pytest.raises(SchoolDenied, match="rate limited"):
        directory.login("school-a", "student", PASSWORD)


def test_metadata_and_submission_cannot_supply_foreign_identity_or_role(school):
    _, _, _, client, _, _ = school
    value = token(client)
    assert consent(client, value).status_code == 200
    metadata = client.get("/api/school/learners/school-a-student/assessments", headers=headers(value))
    assert metadata.status_code == 200
    assert all("answer" not in row and "stem" not in row for row in metadata.json()["assessments"])
    assert client.post("/api/school/learners/school-a-student/issue", headers=headers(value),
        json={"assessment_id": "D-SOLVE-1", "issuance_id": "forged", "learner_id": "school-b-student", "role": "teacher"}).status_code == 422


def test_expired_unknown_and_injected_federation_not_accepted(school):
    _, _, _, client, _, _ = school
    for value in ("legacy-owner-token", "eyJhbGciOiJub25lIn0.role-teacher", "garbage"):
        assert client.get("/api/school/learners", headers=headers(value)).status_code == 401
    from app.school.federation import UnconfiguredFederation
    with pytest.raises(PermissionError, match="unconfigured"):
        UnconfiguredFederation().verify("forged-token", expected_nonce="n")


def test_parent_summary_redacts_all_evidence_identity_and_raw_answer(school):
    _, _, app, client, _, _ = school
    learner = token(client)
    parent = token(client, user="parent")
    assert consent(client, learner).status_code == 200
    assert client.post("/api/school/learners/school-a-student/sharing", headers=headers(learner), json={"audiences": ["parent"], "expected_revision": 0}).status_code == 200
    from tests.test_learning_storage import evidence
    from app.learning.service import LearningService
    store, _ = app.state.learner_instances.get("school-a", "school-a-student")
    service = LearningService(store)
    source = service.consent("school-a-student")["source"]
    service.consume(evidence("private-evidence", learner_id="school-a-student", authorization_source=source,
        verifier_details={"raw_answer": "private-original-answer"}))
    for endpoint in ("state", "evidence"):
        response = client.get(f"/api/school/learners/school-a-student/{endpoint}", headers=headers(parent))
        assert response.status_code == 200
        assert response.json()["summary_only"]
        for secret in ("private-evidence", "private-original-answer", "evidence_refs", "artifact_ref", "verdict_ref", "raw_answer"):
            assert secret not in response.text


def test_sharing_revocation_and_new_consent_version_block_assigned_staff(school):
    _, _, _, client, _, _ = school
    learner = token(client)
    teacher = token(client, user="teacher")
    assert consent(client, learner).status_code == 200
    path = "/api/school/learners/school-a-student/sharing"
    assert client.post(path, headers=headers(learner), json={"audiences": ["teacher"], "expected_revision": 0}).status_code == 200
    assert client.get("/api/school/learners/school-a-student/evidence", headers=headers(teacher)).status_code == 200
    assert client.post(path, headers=headers(learner), json={"audiences": [], "expected_revision": 1}).status_code == 200
    assert client.get("/api/school/learners/school-a-student/evidence", headers=headers(teacher)).status_code == 403
    assert client.post(path, headers=headers(teacher), json={"audiences": ["teacher"], "expected_revision": 2}).status_code == 403
    assert client.post(path, headers=headers(learner), json={"audiences": ["teacher"], "expected_revision": 2}).status_code == 200
    assert consent(client, learner, version="c2").status_code == 200
    assert client.get("/api/school/learners/school-a-student/evidence", headers=headers(teacher)).status_code == 403


@pytest.mark.parametrize("adversarial_headers", [
    {"Host": "evil.example"}, {"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"},
])
def test_loopback_host_and_origin_boundary_on_login(school, adversarial_headers):
    _, _, _, client, _, _ = school
    response = client.post("/api/school/login", headers=adversarial_headers,
        json={"tenant_id": "school-a", "username": "student", "password": PASSWORD})
    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_lifespan_closes_opened_stores_and_success_responses_are_uncached(school):
    _, _, app, _, _, _ = school
    with TestClient(app, base_url="http://127.0.0.1") as client:
        value = token(client)
        response = consent(client, value)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert app.state.learner_instances.instances
    assert app.state.learner_instances.instances == {}

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.gateway.routes import create_app
from app.llm.client import FakeLLM
from tests.test_learning_storage import service, evidence


@pytest.fixture
def api():
    learning=service(); learning.consume(evidence())
    settings=Settings(learner_id="s1",local_teacher_token="teacher-test-only",local_parent_token="parent-test-only")
    with TestClient(create_app(settings=settings,llm=FakeLLM(),store=learning.store)) as client:
        yield client,learning
    learning.store.close()


def test_project_revisions_cas_references_and_no_state_owner(api):
    client,learning=api
    before=learning.masteries("s1")
    body=dict(content=dict(title="方程探究",tasks=[dict(task_id="task",title="独立解释")],evidence_refs=["e1"]))
    first=client.post("/api/learning/projects/s1",json=body)
    assert first.status_code==200
    record=first.json()
    body.update(record_id=record["record_id"],expected_revision=1)
    body["content"]["tasks"][0]["status"]="done"
    assert client.post("/api/learning/projects/s1",json=body).json()["revision"]==2
    assert client.post("/api/learning/projects/s1",json=body).status_code==409
    body["expected_revision"]=2; body["content"]["evidence_refs"]=["foreign"]
    assert client.post("/api/learning/projects/s1",json=body).status_code==409
    assert len(client.get("/api/learning/projects/s1").json()["projects"])==1
    assert learning.masteries("s1")==before
    assert client.get("/api/learning/projects/other").status_code==403


def test_teacher_parent_explicit_sharing_tokens_and_data_minimization(api):
    client,learning=api
    url="/api/learning/dashboard/s1"
    headers={"x-parent-token":"parent-test-only"}
    assert client.get(url,params={"audience":"parent"},headers=headers).status_code==403
    shared=client.post("/api/learning/sharing/s1",json={"audiences":["parent","teacher"]})
    assert shared.status_code==200
    parent=client.get(url,params={"audience":"parent"},headers=headers)
    assert parent.status_code==200 and parent.json()["evidence_count"]==1
    assert "evidence_refs" not in parent.json() and "artifact_ref" not in parent.text
    assert client.get(url,params={"audience":"teacher"},headers=headers).status_code==403
    teacher=client.get(url,params={"audience":"teacher"},headers={"x-teacher-token":"teacher-test-only"})
    assert teacher.json()["evidence_refs"]==["e1"]
    learning.set_consent("s1",[],"withdraw","learner",evidence().occurred_at)
    assert client.get(url,params={"audience":"parent"},headers=headers).status_code==403
    learning.set_consent("s1",["teaching"],"regrant","learner",evidence().occurred_at)
    assert client.get(url,params={"audience":"parent"},headers=headers).status_code==403


def test_qualitative_artifact_does_not_claim_mastery_then_review_is_versioned(api):
    client,learning=api
    body=dict(assessment_id="Q-EXPLAIN-SOLVE",attempt_id="reflection",content="两边同时减去同一个量，再代回验算。")
    url="/api/learning/qualitative/s1"
    response=client.post(url+"/artifacts",json=body)
    assert response.status_code==200
    original=response.json()["evidence_id"]
    assert response.json()["verdict_status"]=="unverifiable"
    assert client.post(url+"/artifacts",json=body).json()==response.json()
    assert client.get(url+"/artifacts/"+original).status_code==403
    headers={"x-teacher-token":"teacher-test-only"}
    assert client.get(url+"/artifacts/"+original,headers=headers).json()["content"]==body["content"]
    review=dict(evidence_id=original,rubric_id="teacher-rubric-eq-reflection",rubric_version="1.0.0",
                dimension_scores={"reasoning":1,"self_check":1},confidence=.9,reason="人工核对完整解释",
                assessor_version="teacher-reviewed-test-v1",correction_id="review")
    assert client.post(url+"/reviews",json=review).status_code==403
    result=client.post(url+"/reviews",json=review,headers=headers)
    assert result.status_code==200 and result.json()["rubric"]["qualitative_pass"] is True
    assert client.post(url+"/reviews",json=review,headers=headers).json()==result.json()
    assert len(learning.evidences("s1"))==3
    assert client.get("/api/learning/recovery/s1").json()["jobs"]
    review["dimension_scores"]={"reasoning":0,"self_check":0}
    assert client.post(url+"/reviews",json=review,headers=headers).status_code==409

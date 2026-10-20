from pydantic import ValidationError
import pytest
from app.modules.evidence.effect_studies import StudyIn,wilson
from tests.test_s02 import database,env
from tests.test_s05 import setup,send


PROTOCOL={'title':'自编策略观察开发协议','research_question':'检查不同支持方式下独立检测的过程记录是否可追溯',
    'source_kind':'synthetic','item_sets':{'pre':['D-01'],'post':['C-01'],'delayed':['C-02'],'transfer':['C-03']},
    'rubric':{'依据说明':['未给出依据','说明法则与适用前提']}}


def test_protocol_requires_isolated_tests_and_defined_anchors():
    StudyIn.model_validate(PROTOCOL)
    with pytest.raises(ValidationError):StudyIn.model_validate({**PROTOCOL,'item_sets':{**PROTOCOL['item_sets'],'post':['D-01']}})
    assert wilson(0,0) is None
    lo,hi=wilson(5,10)
    assert .23<lo<.24 and .76<hi<.77


def test_study_freezes_protocol_and_refuses_non_independent_or_wrong_scope_data(env):
    root='/api/v1/admin/effect-studies'
    response=env.admin.post(root,json=PROTOCOL);assert response.status_code==201,response.text
    row=response.json();sid=row['study_id']
    report=env.admin.get(root+'/'+sid+'/report').json()
    assert report['effect_status']=='not_validated' and not report['gates']['professional_rubric_review']
    student,_=env.student('evaluation.denied')
    assert student.get(root).status_code==403
    assert env.admin.post(root+'/'+sid+'/freeze',json={'expected_revision':row['revision']}).status_code==200
    assert env.admin.post(root+'/'+sid+'/freeze',json={'expected_revision':row['revision']}).status_code==409
    assert env.admin.post(root+'/'+sid+'/observations',headers={'Idempotency-Key':'not-found'},json={'fact_id':'00000000-0000-0000-0000-000000000000','phase':'pre'}).status_code==404


def test_actual_independent_facts_collect_idempotently_without_claiming_effect(env):
    from sqlalchemy import select
    from app.infrastructure.db.models import LearningFact,AgentRun
    root='/api/v1/admin/effect-studies'
    protocol={**PROTOCOL,'item_sets':{'pre':['C-EQ-01'],'post':['C-APP-01'],'delayed':['C-UNSEEN-02'],'transfer':['C-UNSEEN-03']}}
    row=env.admin.post(root,json=protocol).json();path=root+'/'+row['study_id']
    assert env.admin.post(path+'/freeze',json={'expected_revision':row['revision']}).status_code==200
    client,wid,tutor=setup(env)
    send(env,client,tutor,'answer',answer='3');send(env,client,tutor,'next')
    send(env,client,tutor,'answer',answer='6');send(env,client,tutor,'reflect',reflection='等式两边相同运算')
    send(env,client,tutor,'answer',answer='7');send(env,client,tutor,'reflect',reflection='代入原条件验证')
    send(env,client,tutor,'answer',answer='5');send(env,client,tutor,'next')
    send(env,client,tutor,'answer',answer='6');send(env,client,tutor,'next')
    with env.db.transaction() as session:
        facts={f.task_id:f.id for f in session.scalars(select(LearningFact).where(LearningFact.workspace_id==wid,LearningFact.kind=='answer'))}
    for phase,task in [('pre','C-EQ-01'),('post','C-APP-01')]:
        body={'phase':phase,'fact_id':facts[task]}
        result=env.admin.post(path+'/observations',headers={'Idempotency-Key':phase},json=body)
        assert result.status_code==201,result.text
        assert env.admin.post(path+'/observations',headers={'Idempotency-Key':phase},json=body).json()==result.json()
        assert env.admin.post(path+'/observations',headers={'Idempotency-Key':phase+'duplicate'},json=body).status_code==409
    report=env.admin.get(path+'/report').json()
    assert report['observations']==2 and report['effect_status']=='not_validated'
    assert sum(c['submissions'] for c in report['cells'])==2
    assert all(c['proportion'] is None for c in report['cells'] if c['phase'] in {'delayed','transfer'})
    assert report['gates']['minimum_samples'] is False

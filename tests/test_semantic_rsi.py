import json
from copy import deepcopy
import pytest
from app.infrastructure.runtime.model_tasks import ModelTaskRunner
from app.llm.provider import ProviderError
from app.modules.skill_evolution.semantic import validate, fake
from app.modules.planning.contracts import ExecutionPolicy
from app.worker import Worker
from tests.test_s02 import database, env
from tests.test_g08 import ready
from tests.test_model_runtime import publish


def test_semantic_candidate_uses_real_consumers_and_personal_trial_is_reversible(env):
    client, wid, parent = ready(env)
    publish(env)
    root=f'/api/v1/student/workspaces/{wid}/skill-evolutions'
    response=client.post(root,headers={'Idempotency-Key':'semantic'},json={'mode':'semantic','allow_personal_trial':True})
    assert response.status_code==202,response.text
    path=root+'/'+response.json()['run_id']
    assert Worker(env.db,enable_models=False).step()
    run=client.get(path).json()
    assert run['phase']=='model_pending',run
    assert ModelTaskRunner(env.db).step(operation_id=run['generation']['operation_id'])
    run=client.get(path).json()
    assert run['phase']=='personal_trial_active',run
    assert run['candidate']['generator']=='skill-semantic.v1'
    assert run['evaluation']['eligible_for_personal_trial']
    assert run['trial']['effect_status']=='not_validated'
    active=client.get(f'/api/v1/student/workspaces/{wid}/active-strategy').json()
    assert active['effective'] and active['version_id']==run['run_id']
    assert active['policy']==run['candidate']['policy']
    other,_=env.student('rsi.other')
    assert other.post(path+'/withdraw-trial').status_code==404
    assert client.post(path+'/withdraw-trial').status_code==200
    active=client.get(f'/api/v1/student/workspaces/{wid}/active-strategy').json()
    assert active['version_id']==parent['run_id'] and active['effective']
    assert client.post(path+'/withdraw-trial').status_code==200


def test_untrusted_candidate_cannot_rewrite_budget_or_fabricate_evidence():
    baseline={'daily_minutes':15,'reflection_focus':'basis','execution':ExecutionPolicy().model_dump()}
    snapshot={'baseline':baseline,'evidence':[{'id':'fact-1','text':'先检查符号'}]}
    valid=fake(snapshot)
    assert validate(json.dumps(valid,ensure_ascii=False),snapshot)['diff'][0]['field']=='question_focus'
    for mutation in ('budget','evidence','two_changes'):
        proposal=deepcopy(valid)
        if mutation=='budget':proposal['policy']['budget']=0
        elif mutation=='evidence':proposal['evidence_ids']=['foreign-project-fact']
        else:proposal['policy']['execution']['support_mode']='step_first'
        with pytest.raises(ProviderError):validate(json.dumps(proposal,ensure_ascii=False),snapshot)

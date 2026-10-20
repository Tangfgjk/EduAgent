import json
from copy import deepcopy
from sqlalchemy import select
from tests.test_s02 import database,env
from tests.test_g08 import ready
from tests.test_model_runtime import publish
from tests.test_topic_generation import prepare
from app.infrastructure.db.models import LearningInput,LearningFact,AgentRun,LearningAutomation,ModelOperation,new_id
from app.modules.learning.answer_interpretation import check_explicit_equalities
from app.modules.learning.content_repository import content_hash
from app.modules.skill_evolution import automation
from app.modules.skill_evolution.shadow import evaluate_shadow
from app.modules.planning.contracts import ExecutionPolicy
from app.infrastructure.runtime.model_tasks import ModelTaskRunner
from app.worker import Worker


def test_explicit_arithmetic_reason_is_separate_from_final_value():
    assert check_explicit_equalities('答案3，因为1+1=3')['status']=='contradiction_found'
    assert check_explicit_equalities('答案3，因为1+2=3')['status']=='arithmetic_checked'
    assert check_explicit_equalities('有人说1+1=3，但这不对')['status']=='not_assessed'


def test_uncertain_diagnostic_adapts_and_can_finish_with_unknown(env):
    client,wid,root,row=prepare(env)
    path=root+'/'+row['build_id']+'/diagnostic'
    response=client.post(path,json={'expected_revision':row['revision'],'answers':[{'answer':'不确定'},{'answer':'题目里有3'}]})
    assert response.status_code==200,response.text
    row=response.json();assert row['status']=='diagnostic' and len(row['diagnostics'])==1 and len(row['attempts'])==2
    assert client.post(path,json={'expected_revision':row['revision'],'answers':[{'answer':'0'},{'answer':'0'}]}).status_code==422
    row=client.post(path,json={'expected_revision':row['revision'],'answers':[],'finish_diagnostic':True}).json()
    assert row['status']=='confirm_plan' and row['plan']['diagnostic_count']==2
    assert all(a['status']=='unverifiable' for a in row['attempts'])


def test_automatic_rsi_consent_withdrawal_blocks_late_candidate(env):
    client,wid,parent=ready(env);publish(env)
    path=f'/api/v1/student/workspaces/{wid}/learning-automation'
    state=client.put(path,json={'enabled':True,'expected_revision':0}).json();assert state['enabled']
    with env.db.transaction() as s:
        original=s.scalar(select(LearningFact).where(LearningFact.workspace_id==wid))
        original_input=s.get(LearningInput,original.input_id)
        for n in range(6):
            payload={'action':'reflect','reflection':'自编成人开发观察：需要核对条件与运算依据'}
            entry=LearningInput(workspace_id=wid,conversation_id=original_input.conversation_id,account_id=original_input.account_id,
                idempotency_key='new-shadow-'+str(n),request_hash=content_hash(payload),source_kind=original.source_kind,payload=payload)
            s.add(entry);s.flush()
            s.add(LearningFact(workspace_id=wid,run_id=original.run_id,input_id=entry.id,kind='reflection',source_kind=original.source_kind,
                task_id=original.task_id,content_version=original.content_version,payload=deepcopy(original.payload)))
        s.flush();automation.tick(s,wid);s.flush()
        rid=s.get(LearningAutomation,wid).body['last_run_id']
    assert Worker(env.db,enable_models=False).step()
    runner=ModelTaskRunner(env.db);claim=runner.claim();assert claim and claim['request']['purpose']=='skill_semantic'
    state=client.get(path).json()
    assert client.put(path,json={'enabled':False,'expected_revision':state['revision']}).status_code==200
    from app.llm.provider import GenerationResult,Usage
    from app.modules.skill_evolution.semantic import fake
    result=GenerationResult(provider='fake',requested_model='scripted',text=json.dumps(fake(claim['request']),ensure_ascii=False),finish_reason='stop',elapsed_ms=1,simulated=True)
    assert runner.complete(claim,result=result) is False
    pointer=client.get(f'/api/v1/student/workspaces/{wid}/active-strategy').json()
    assert pointer['version_id']==parent['run_id']
    with env.db.transaction() as s:assert s.get(AgentRun,rid).status=='cancelled'


def test_candidate_runs_withheld_shadow_consumers():
    base={'daily_minutes':15,'reflection_focus':'basis','execution':ExecutionPolicy().model_dump()}
    candidate=deepcopy(base);candidate['execution']['support_mode']='adaptive'
    result=evaluate_shadow(base,candidate)
    assert result['passed'] and len(result['cases'])==6 and result['teaching_effect_validated'] is False



def test_additional_diagnostic_is_in_model_denominator_and_excluded_parameters(env):
    from types import SimpleNamespace
    from app.infrastructure.db.models import CourseBuild,Workspace,Account
    from app.modules.generation.service import freeze_request,diagnostic_parameters
    client,wid,root,row=prepare(env,'一次函数图像')
    path=root+'/'+row['build_id']+'/diagnostic'
    row=client.post(path,json={'expected_revision':row['revision'],'answers':[{'answer':'不确定'},{'answer':'题目里有3'}]}).json()
    assert row['status']=='diagnostic'
    row=client.post(path,json={'expected_revision':row['revision'],'answers':[{'answer':'0'}]}).json()
    assert row['status']=='confirm_plan'
    with env.db.transaction() as session:
        build=session.get(CourseBuild,row['build_id']);account=session.get(Account,session.get(Workspace,wid).owner_id)
        snapshot=freeze_request(build,account,SimpleNamespace(prompt_version='probability.followup.v3'))
        payload=json.loads(snapshot['generation']['messages'][1]['content'])
        assert payload['diagnostic_counts']['total']==3
        assert payload['excluded_parameters']==diagnostic_parameters(build) and len(payload['excluded_parameters'])==3

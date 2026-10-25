"""Cross-boundary regressions for the V17 audit; no external model calls."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import select
from tests.test_s02 import database,env
from tests.test_s08 import seed
from app.modules.evidence.service import day
from app.infrastructure.db.models import LearningFact,CourseBinding,StrategyVersion,AgentRun,MediaBudget,now
from app.modules.learning.content_repository import content_hash
from app.modules.resources.media import BailianMedia,MediaFailure,download_url


def test_date_records_query_all_pages_and_remains_owner_scoped(env):
    client,_=env.student();wid=env.workspace(client)['workspace_id']
    seed(env,client,wid,47)
    path=f'/api/v1/student/workspaces/{wid}/evidence'
    full=client.get(path).json();today=day(now())
    first=client.get(path,params={'on_day':today}).json()
    second=client.get(path,params={'on_day':today,'offset':20}).json()
    last=client.get(path,params={'on_day':today,'offset':40}).json()
    assert first['record_count']==47 and len(last['records'])==7
    assert len({r['record_id'] for r in first['records']+second['records']+last['records']})==47
    assert first['groups']==full['groups']
    other,_=env.student('date.other')
    assert other.get(path,params={'on_day':today}).status_code==404
    assert client.get(path,params={'on_day':'2026-02-31'}).status_code==422
    assert client.get(path,params={'on_day':'2000-01-01'}).json()['record_count']==0


def test_review_accepts_conservative_phrase_and_keeps_uncertainty(env):
    from tests.test_s05 import setup,to_practice,send
    client,wid,path=setup(env);to_practice(env,client,path)
    send(env,client,path,'answer',answer='0')
    send(env,client,path,'answer',answer='6')
    send(env,client,path,'reflect',reflection='先核对等式两边的操作')
    root=f'/api/v1/student/workspaces/{wid}/review-cards'
    card=client.get(root).json()['items'][0]
    from app.modules.memory.service import Memory
    from app.modules.learning.answer_interpretation import interpret
    # The actual HTTP use case, not just the parser.
    response=client.post(root+'/'+card['card_id']+'/attempts',headers={'Idempotency-Key':'phrase'},json={'expected_revision':card['revision'],'answer':'这个答案是6','reasoning':'两边同乘除非零数'})
    assert response.status_code==200,response.text
    assert response.json()['attempt']['status']=='passed'
    card=response.json()['card']
    response=client.post(root+'/'+card['card_id']+'/attempts',headers={'Idempotency-Key':'ambiguous'},json={'expected_revision':card['revision'],'answer':'不是6，也许是7','reasoning':'我还不确定'})
    assert response.status_code==200,response.text
    assert response.json()['attempt']['status']=='unverifiable'


def test_current_goal_excludes_other_course_versions_but_retains_history(env):
    from tests.test_agent_dialogue import activated
    from app.modules.learning.goal_context import snapshot
    client,wid,unit=activated(env)
    seed(env,client,wid,5)
    with env.db.transaction() as session:
        account=env.actor(session,client).account
        before=snapshot(session,wid,account)
        assert before['evidence']==[] and before['historical_background']['count_in_recent_window']==3
        from app.modules.learning.content_catalog import get_pack
        version=get_pack(unit,session).version
        fact=session.scalar(select(LearningFact).where(LearningFact.workspace_id==wid,LearningFact.source_kind==account.source_kind))
        from app.infrastructure.db.models import LearningInput,new_id
        origin=session.get(LearningInput,fact.input_id)
        entry=LearningInput(workspace_id=wid,conversation_id=origin.conversation_id,account_id=account.id,
            idempotency_key=new_id(),request_hash='0'*64,source_kind=account.source_kind,payload={'answer':'3'})
        session.add(entry);session.flush()
        session.add(LearningFact(workspace_id=wid,run_id=fact.run_id,input_id=entry.id,kind=fact.kind,source_kind=account.source_kind,
            task_id=fact.task_id,content_version=version,payload=fact.payload));session.flush()
        after=snapshot(session,wid,account)
        assert len(after['evidence'])==1 and after['evidence'][0]['content_version']==version


def test_schedule_uses_goal_course_difficulty_and_duration():
    from app.modules.learning.schedule import arrange
    context={'title':'有理数','tasks':[{'task_id':'P1','stage':'practice','concept':'相反数'},
        {'task_id':'P2','stage':'practice','concept':'绝对值','needs_revisit':True},{'task_id':'C1','stage':'checkpoint'}],
        'due_reviews':2,'uncertain_or_failed':1}
    steps,short=arrange('说明绝对值的依据',5,context)
    _,long=arrange('说明相反数的依据',15,context)
    assert short['practice_task_ids']==['P2'] and long['practice_task_ids'][0]=='P1'
    assert sum(short['minutes'].values())==5 and '到期复习' in steps[1]
    assert '说明绝对值' in steps[0] and 'C1' not in short['practice_task_ids']


def test_confirmed_plan_reorders_real_tutor_without_removing_checks(env):
    from tests.test_s05 import enable,send
    from tests.test_s04 import start,decision
    from app.modules.learning.development_content import UNIT_ID
    from app.modules.learning.content_catalog import get_pack
    from app.infrastructure.db.models import LearningInput,new_id
    from app.worker import Worker
    client,_=env.student();wid=env.workspace(client)['workspace_id'];enable(env)
    bound=client.get(f'/api/v1/student/workspaces/{wid}/course').json()
    result=client.post(f'/api/v1/student/workspaces/{wid}/course',json={'expected_revision':bound['revision'],
        'topic':'等式与应用','title':'本次方程课程','unit_ids':[UNIT_ID],'active_unit_id':UNIT_ID})
    assert result.status_code==200,result.text
    pack=get_pack(UNIT_ID)
    with env.db.transaction() as session:
        actor=env.actor(session,client);conversation=env.spaces.create_conversation(session,actor,wid,'tutor')
        history=AgentRun(workspace_id=wid,conversation_id=conversation.id,creation_key='old',creation_hash='0'*64,version='fixture',kind='tutor',status='completed')
        session.add(history);session.flush()
        entry=LearningInput(workspace_id=wid,conversation_id=conversation.id,account_id=actor.account.id,
            idempotency_key=new_id(),request_hash='0'*64,source_kind='synthetic',payload={'answer':'0'})
        session.add(entry);session.flush()
        task=next(t for t in pack.tasks if t['id']=='P-APP-01')
        session.add(LearningFact(workspace_id=wid,run_id=history.id,input_id=entry.id,kind='answer',source_kind='synthetic',
            task_id=task['id'],content_version=pack.version,payload={'stage':'practice','status':'failed','hints_used':0,'task_snapshot':task}))
    plan=start(env,client,wid);Worker(env.db).step();path=f'/api/v1/student/workspaces/{wid}/runs/'+plan['run_id']
    draft=client.get(path).json()
    assert draft['draft']['schedule']['practice_task_ids'][0]=='P-APP-01'
    response=client.post(path+'/resume',headers={'Idempotency-Key':'confirm'},json=decision(draft))
    assert response.status_code==202,response.text
    Worker(env.db).step()
    root=f'/api/v1/student/workspaces/{wid}/tutoring'
    run=client.post(root,headers={'Idempotency-Key':'follow-plan'},json={'unit_id':UNIT_ID})
    assert run.status_code==202,run.text
    Worker(env.db).step();path=root+'/'+run.json()['run_id']
    first=client.get(path).json()
    assert first['skill_execution']['plan_execution']['status']=='applied_priority'
    send(env,client,path,'answer',answer='3');second=send(env,client,path,'next')
    assert second['view']['task']['task_id']=='P-APP-01'
    assert second['view']['task_count']==len(pack.tasks)
    # A plan with platform defaults must also be a complete model context, not
    # a truthy partial Skill snapshot that crashes the interaction consumer.
    from tests.test_model_runtime import publish
    from app.infrastructure.db.models import GenerationPolicy
    publish(env)
    with env.db.transaction() as session:session.get(GenerationPolicy,1).interaction=True
    run=client.get(path).json()
    response=client.post(path+'/inputs',headers={'Idempotency-Key':'planned-model'},json={
        'expected_revision':run['revision'],'gate_id':run['gate_id'],'action':'hint','coaching_text':'我想先核对所用的等式性质'})
    assert response.status_code==202,response.text
    Worker(env.db,enable_models=False).step()
    prepared=client.get(path).json()
    from app.infrastructure.runtime.model_tasks import ModelTaskRunner
    assert ModelTaskRunner(env.db).step(operation_id=prepared['coaching']['operation_id'])
    assert client.get(path).json()['coaching']['status']=='succeeded'


@pytest.mark.parametrize('status,body',[(400,{'code':'InvalidParameter','message':'secret sk-do-not-retain','request_id':'req-1'}),(429,[]),(503,{'error':{'code':'Busy'}})])
def test_media_errors_safe_and_distinguish_unknown(status,body,monkeypatch):
    monkeypatch.setenv('WENJIN_BAILIAN_API_HOST','https://dashscope.aliyuncs.com')
    port=BailianMedia('fake',httpx.MockTransport(lambda req:httpx.Response(status,json=body)))
    try:
        with pytest.raises(MediaFailure) as result:port.submit({'kind':'audio','model':'qwen3-tts-flash','prompt':'自编测试'})
        assert result.value.receipt['http_status']==status
        assert result.value.receipt['submission_outcome_unknown']==(status>=500)
        assert result.value.receipt['billing_unknown'] is True
        assert 'secret' not in str(result.value.receipt) and 'sk-' not in str(result.value.receipt)
    finally:port.close()
    assert download_url('http://fixture.oss-cn-beijing.aliyuncs.com/a.wav').startswith('https://')
    with pytest.raises(ValueError):download_url('http://example.com/a.wav')


def test_strict_study_preassignment_and_exposure_cannot_be_forged(env,monkeypatch):
    from app.modules.evidence.effect_studies import StudyIn
    from tests.test_effect_studies import PROTOCOL
    from tests.test_s05 import setup,send
    from app.modules.evidence import effect_studies as study
    client,wid,path=setup(env,'project')
    source_id=client.get(path).json()['run_id']
    from app.modules.learning.content_catalog import get_pack
    from app.modules.learning.development_content import UNIT_ID
    item_version=get_pack(UNIT_ID).version
    with env.db.transaction() as session:
        from app.gateway.v1 import SESSION_COOKIE
        actor=env.identity.authorize(session,env.admin.cookies.get(SESSION_COOKIE),env.admin.headers['X-CSRF-Token'],'admin',True)
        versions={}
        for arm,minutes in [('baseline',15),('candidate',20)]:
            source=session.get(AgentRun,source_id)
            run=AgentRun(workspace_id=wid,conversation_id=source.conversation_id,creation_key='study-'+arm,
                creation_hash='0'*64,version='test-study',status='completed',kind='strategy')
            session.add(run);session.flush()
            version=StrategyVersion(id=run.id,workspace_id=wid,bundle={},policy={'daily_minutes':minutes},checks=[],
                content_hash='0'*64,hard_rules_hash='0'*64,base_revision=0,expires_at=now()+3600)
            session.add(version);versions[arm]=version
        session.flush()
        protocol={**PROTOCOL,'mode':'exposure_checked','policy_hashes':{k:content_hash(v.policy) for k,v in versions.items()},
            'item_sets':{'pre':['C-EQ-01'],'post':['C-APP-01'],'delayed':['X1'],'transfer':['X2']},
            'item_versions':{key:item_version for key in ['C-EQ-01','C-APP-01','X1','X2']}}
        row=study.create(session,actor,protocol)
        from app.infrastructure.db.models import EvaluationStudy
        stored=session.get(EvaluationStudy,row['study_id']);study.freeze(session,actor,stored,row['revision'])
        body=study.EnrollmentIn(workspace_id=wid,baseline_id=versions['baseline'].id,candidate_id=versions['candidate'].id)
        with monkeypatch.context() as clock:
            clock.setattr(study,'now',lambda:now()-5)
            assigned=study.enroll(session,actor,stored,body)
        assert study.enroll(session,actor,stored,body)==assigned
        assert assigned['arm'] in {'baseline','candidate'}
        from app.modules.errors import DomainError
        with pytest.raises(DomainError):study.enroll(session,actor,stored,body.model_copy(update={'candidate_id':body.baseline_id}))
    # Actual first-exposure checkpoint facts carry no registered policy; cannot
    # pass merely by selecting an arm or supplying a model-generated verdict.
    send(env,client,path,'answer',answer='3');send(env,client,path,'next')
    send(env,client,path,'answer',answer='6');send(env,client,path,'reflect',reflection='检查依据')
    send(env,client,path,'answer',answer='7');send(env,client,path,'reflect',reflection='代入检查')
    send(env,client,path,'answer',answer='5')
    with env.db.transaction() as session:
        fact=session.scalar(select(LearningFact).where(LearningFact.workspace_id==wid,LearningFact.task_id=='C-EQ-01'))
        fid=fact.id
    response=env.admin.post('/api/v1/admin/effect-studies/'+row['study_id']+'/observations',headers={'Idempotency-Key':'forged'},json={'fact_id':fid,'phase':'pre'})
    assert response.status_code==409 and response.json()['error']['code']=='policy_exposure_conflict'
    report=env.admin.get('/api/v1/admin/effect-studies/'+row['study_id']+'/report').json()
    assert report['observations']==0 and report['assignments']==1 and not report['gates']['strategy_exposure']
    # Append-only synthetic fixtures test the time fence without mutating any
    # learning record: a later practice in the same second cannot fix exposure.
    from app.infrastructure.db.models import EvaluationStudy,LearningInput,new_id
    from app.modules.errors import DomainError
    with env.db.transaction() as session:
        actor=env.identity.authorize(session,env.admin.cookies.get(SESSION_COOKIE),env.admin.headers['X-CSRF-Token'],'admin',True)
        stored=session.get(EvaluationStudy,row['study_id']);original=session.get(LearningFact,fid)
        origin=session.get(LearningInput,original.input_id)
        def fact_at(task_id,stage,at,version_id):
            entry=LearningInput(workspace_id=wid,conversation_id=origin.conversation_id,account_id=origin.account_id,
                idempotency_key=new_id(),request_hash='0'*64,source_kind='synthetic',payload={'answer':'5'})
            session.add(entry);session.flush()
            fact=LearningFact(workspace_id=wid,run_id=original.run_id,input_id=entry.id,task_id=task_id,content_version=item_version,
                kind='answer',source_kind='synthetic',created_at=at,payload={'stage':stage,'status':'passed','hints_used':0,
                    'assessment_exposure':'fresh','strategy_version_id':version_id})
            session.add(fact);session.flush();return fact
        same_second=fact_at('C-EQ-01','checkpoint',assigned['assigned_at'],body.baseline_id)
        with pytest.raises(DomainError) as error:
            study.record(session,actor,stored,study.ObservationIn(fact_id=same_second.id,phase='pre'),'same-second-pre')
        assert error.value.code=='assignment_required'
        pre=fact_at('C-EQ-01','checkpoint',assigned['assigned_at']+1,body.baseline_id)
        study.record(session,actor,stored,study.ObservationIn(fact_id=pre.id,phase='pre'),'valid-pre')
        expected=assigned[assigned['arm']+'_id']
        post=fact_at('C-APP-01','checkpoint',assigned['assigned_at']+3,expected)
        fact_at('P-EQ-01','practice',post.created_at,expected)
        observation=study.ObservationIn(fact_id=post.id,phase='post')
        with pytest.raises(DomainError) as error:study.record(session,actor,stored,observation,'late-practice')
        assert error.value.code=='intervention_exposure_conflict'
        fact_at('P-APP-01','practice',assigned['assigned_at']+2,expected)
        checked=study.record(session,actor,stored,observation,'valid-post')
        assert checked['exposure_verified']
        assert study.record(session,actor,stored,observation,'valid-post')==checked
        report=study.report(session,actor,stored)
        assert report['gates']['strategy_exposure'] and not report['gates']['minimum_samples']
        assert report['effect_status']=='not_validated'


def test_single_tts_authorization_is_durable_without_file(env,monkeypatch,tmp_path):
    from tests.test_media_pipeline import classroom
    from scripts import verify_tts_once as probe
    _,_,_,budget=classroom(env)
    with env.db.transaction() as session:
        row=session.get(MediaBudget,budget['budget_id'])
        row.body={**row.body,'authorization':'human_single_tts_150_chars_002_yuan_no_retry'}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(probe.Settings,'load',lambda _:SimpleNamespace(database_url='test-only'))
    monkeypatch.setattr(probe,'Database',lambda _:env.db)
    monkeypatch.setattr(probe,'MediaRunner',lambda _:pytest.fail('No external submission may be reached'))
    with pytest.raises(SystemExit,match='Database already records'):probe.main()


def test_separate_single_slot_budget_cannot_expand_to_other_media(env):
    from tests.test_media_pipeline import classroom
    client,wid,_,budget=classroom(env)
    with env.db.transaction() as session:
        row=session.get(MediaBudget,budget['budget_id'])
        row.enabled=True
        row.body={**row.body,'caps':{'image':0,'audio':1,'video':0},'total_cap_microyuan':20000,'expires_at':now()+60}
    path=f'/api/v1/student/workspaces/{wid}/media-budgets/{budget["budget_id"]}/jobs'
    for kind,index in [('image',0),('video',0),('audio',1)]:
        assert client.post(path,json={'kind':kind,'index':index}).status_code==409
    first=client.post(path,json={'kind':'audio','index':0})
    assert first.status_code==202,first.text
    assert client.post(path,json={'kind':'audio','index':0}).json()==first.json()
    assert len(first.json()['operations'])==1 and first.json()['body']['reserved_microyuan']<=20000


def test_generic_activity_limit_matches_public_controls_without_blocking_answers(env):
    from tests.test_agent_dialogue import activated
    from tests.test_s05 import send
    from app.modules.learning.content_catalog import get_pack
    from app.worker import Worker
    from app.infrastructure.runtime.model_tasks import ModelTaskRunner
    client,wid,unit=activated(env)
    with env.db.transaction() as session:pack=get_pack(unit,session)
    root=f'/api/v1/student/workspaces/{wid}/tutoring'
    result=client.post(root,headers={'Idempotency-Key':'quota-start'},json={'unit_id':unit})
    assert result.status_code==202,result.text
    Worker(env.db,enable_models=False).step();path=root+'/'+result.json()['run_id']
    send(env,client,path,'answer',answer=pack.tasks[0]['answer']);send(env,client,path,'next')
    for turn in range(3):
        run=client.get(path).json()
        response=client.post(path+'/inputs',headers={'Idempotency-Key':'quota-'+str(turn)},json={
            'expected_revision':run['revision'],'gate_id':run['gate_id'],'action':'hint','coaching_text':f'请检查我第{turn}步的符号依据'})
        assert response.status_code==202,response.text
        Worker(env.db,enable_models=False).step()
        pending=client.get(path).json()
        assert ModelTaskRunner(env.db).step(operation_id=pending['coaching']['operation_id'])
    run=client.get(path).json()
    assert run['coaching_activity_remaining']==0 and not run['coaching_available']
    assert run['view']['can_answer'] and run['view']['can_hint']
    response=client.post(path+'/inputs',headers={'Idempotency-Key':'quota-extra'},json={
        'expected_revision':run['revision'],'gate_id':run['gate_id'],'action':'hint','coaching_text':'再帮我检查依据'})
    assert response.status_code==429,response.text
    assert response.json()['error']['retryable'] is False
    assert send(env,client,path,'hint',coaching_text='')['view']['hints']

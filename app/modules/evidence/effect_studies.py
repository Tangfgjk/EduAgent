"""Preregistered, append-only observation collection. No synthetic efficacy claim."""
import hashlib
import math
from collections import defaultdict
from typing import Literal
from pydantic import Field, model_validator
from sqlalchemy import select
from app.infrastructure.db.models import EvaluationStudy, EvaluationObservation, LearningFact, Workspace, Account, StrategyVersion, now
from app.modules.learning.contracts import Contract
from app.modules.learning.content_repository import content_hash
from app.modules.errors import DomainError


class StudyIn(Contract):
    title: str = Field(min_length=4, max_length=160)
    research_question: str = Field(min_length=10,max_length=1000)
    source_kind: Literal['synthetic','adult_test']
    item_sets: dict[Literal['pre','post','delayed','transfer'],list[str]]
    rubric: dict[str,list[str]]
    minimum_per_arm: int = Field(default=10,ge=5,le=10000,strict=True)
    delayed_hours: int = Field(default=24,ge=24,le=720,strict=True)
    mode: Literal['rehearsal','exposure_checked'] = 'rehearsal'
    policy_hashes: dict[str,str] = Field(default_factory=dict)
    item_versions: dict[str,str] = Field(default_factory=dict)
    @model_validator(mode='after')
    def complete(self):
        if self.mode=='exposure_checked' and (set(self.policy_hashes)!={'baseline','candidate'} or
                len(set(self.policy_hashes.values()))!=2 or any(len(v)!=64 or any(c not in '0123456789abcdef' for c in v) for v in self.policy_hashes.values())):
            raise ValueError('策略暴露协议须预先登记两个不同策略的SHA256摘要')
        if set(self.item_sets)!={'pre','post','delayed','transfer'} or not all(self.item_sets.values()):
            raise ValueError('必须事先指定前测、后测、延迟与迁移题集')
        all_ids=[t for items in self.item_sets.values() for t in items]
        if self.mode=='exposure_checked' and (set(self.item_versions)!=set(all_ids) or any(len(v)!=64 or any(c not in '0123456789abcdef' for c in v) for v in self.item_versions.values())):
            raise ValueError('暴露核对协议须冻结每个测验题的内容版本摘要')
        if len(all_ids)>200 or len(set(all_ids))!=len(all_ids) or any(len(t)>100 for t in all_ids):
            raise ValueError('各测验题集必须隔离，且总共不超过200题')
        if not 1<=len(self.rubric)<=6 or any(not 2<=len(anchors)<=5 or any(not 2<=len(a)<=300 for a in anchors) for anchors in self.rubric.values()):
            raise ValueError('量规需1至6维，每维2至5项明确锚点')
        return self


class ObservationIn(Contract):
    fact_id: str = Field(min_length=36,max_length=36)
    phase: Literal['pre','post','delayed','transfer']


class EnrollmentIn(Contract):
    workspace_id: str = Field(min_length=36,max_length=36)
    baseline_id: str = Field(min_length=36,max_length=36)
    candidate_id: str = Field(min_length=36,max_length=36)


def enroll(session,actor,row,body):
    admin(actor)
    if row.status!='collecting' or row.protocol.get('mode')!='exposure_checked':
        raise DomainError(409,'exposure_protocol_required','请先冻结含策略摘要的暴露核对协议')
    space=session.get(Workspace,body.workspace_id)
    owner=session.get(Account,space.owner_id) if space else None
    if not space or space.kind!='project' or not owner or owner.source_kind!=row.protocol['source_kind']:
        raise DomainError(409,'study_scope_conflict','仅纳入相同开发来源的正式项目')
    versions={arm:session.get(StrategyVersion,getattr(body,arm+'_id')) for arm in ('baseline','candidate')}
    if any(not v or v.workspace_id!=space.id or content_hash(v.policy)!=row.protocol['policy_hashes'][arm] for arm,v in versions.items()):
        raise DomainError(409,'policy_exposure_conflict','本项目策略版本与预先登记的摘要不一致')
    subject=hashlib.sha256((row.id+':'+owner.id).encode()).hexdigest()
    key='assignment:'+subject
    existing=session.scalar(select(EvaluationObservation).where(EvaluationObservation.study_id==row.id,EvaluationObservation.creation_key==key))
    if existing:
        if any(existing.body[k]!=getattr(body,k) for k in ('workspace_id','baseline_id','candidate_id')):
            raise DomainError(409,'assignment_frozen','被试分配已冻结，不能换项目或策略')
        return existing.body
    arm='candidate' if int(subject[:8],16)%2 else 'baseline'
    value={'kind':'assignment','subject':subject,'arm':arm,'assigned_at':now(),**body.model_dump()}
    session.add(EvaluationObservation(study_id=row.id,creation_key=key,body=value,sha256=content_hash(value)))
    session.flush();return value


def admin(actor):
    if actor.account.role!='admin':raise DomainError(403,'forbidden','仅管理员可管理独立评价协议')


def create(session,actor,body):
    admin(actor);protocol=StudyIn.model_validate(body).model_dump()
    row=EvaluationStudy(owner_id=actor.account.id,title=protocol['title'],protocol=protocol)
    session.add(row);session.flush();return view(row)


def view(row):return {'study_id':row.id,'title':row.title,'protocol':row.protocol,'protocol_hash':content_hash(row.protocol),
    'status':row.status,'revision':row.revision,'created_at':row.created_at,'effect_status':'not_validated'}


def freeze(session,actor,row,revision):
    admin(actor)
    if row.revision!=revision:raise DomainError(409,'revision_conflict','评价协议已更新')
    if row.status=='draft':row.status='collecting';row.revision+=1
    return view(row)


def record(session,actor,row,body,key):
    admin(actor)
    if row.status!='collecting':raise DomainError(409,'protocol_not_frozen','请先冻结评价协议，再采集数据')
    fact=session.get(LearningFact,body.fact_id)
    if not fact:raise DomainError(404,'not_found','未找到原始学习事实')
    space=session.get(Workspace,fact.workspace_id);owner=session.get(Account,space.owner_id)
    payload=fact.payload
    if fact.source_kind!=row.protocol['source_kind'] or owner.source_kind!=fact.source_kind or fact.task_id not in row.protocol['item_sets'][body.phase]:
        raise DomainError(409,'study_scope_conflict','记录来源或题目不属于冻结协议')
    if fact.kind!='answer' or payload.get('stage')!='checkpoint' or payload.get('hints_used',0)!=0 or payload.get('assessment_exposure')!='fresh' or payload.get('status') not in {'passed','failed'}:
        raise DomainError(409,'not_independent_measure','仅接受首次曝光、无提示、已核验的检测原始事实')
    subject=hashlib.sha256((row.id+':'+owner.id).encode()).hexdigest()
    arm='candidate' if int(subject[:8],16)%2 else 'baseline'
    content={'subject':subject,'arm':arm,'phase':body.phase,'fact_id':fact.id,'task_id':fact.task_id,
        'content_version':fact.content_version,'passed':payload['status']=='passed','created_at':fact.created_at,
        'strategy_version_id':payload.get('strategy_version_id'),'source_kind':fact.source_kind}
    existing=session.scalar(select(EvaluationObservation).where(EvaluationObservation.study_id==row.id,EvaluationObservation.creation_key==key))
    if existing:
        if {k:v for k,v in existing.body.items() if k!='exposure_verified'}!=content:raise DomainError(409,'idempotency_conflict','记录标识已用于其他观察')
        return existing.body
    all_rows=list(session.scalars(select(EvaluationObservation).where(EvaluationObservation.study_id==row.id)))
    if any(content_hash(r.body)!=r.sha256 for r in all_rows):raise DomainError(409,'observation_corrupt','观察记录校验失败')
    assignment=next((r.body for r in all_rows if r.body.get('kind')=='assignment' and r.body['subject']==subject),None)
    rows=[r for r in all_rows if r.body.get('kind')!='assignment']
    if row.protocol.get('mode')=='exposure_checked':
        if fact.content_version!=row.protocol['item_versions'].get(fact.task_id):
            raise DomainError(409,'study_item_version_conflict','测验题版本不属于冻结协议，不能合并比较')
        if not assignment or assignment['workspace_id']!=space.id or fact.created_at<=assignment['assigned_at']:
            raise DomainError(409,'assignment_required','须先冻结本被试的分配，再开展测验')
        required=assignment[('baseline' if body.phase=='pre' else arm)+'_id']
        version=session.get(StrategyVersion,required)
        expected=row.protocol['policy_hashes']['baseline' if body.phase=='pre' else arm]
        if payload.get('strategy_version_id')!=required or not version or content_hash(version.policy)!=expected:
            raise DomainError(409,'policy_exposure_conflict','实际测验策略不属于分配组，不能纳入比较')
        content['exposure_verified']=True
    if any(r.body['fact_id']==fact.id for r in rows):raise DomainError(409,'duplicate_observation','同一事实不能重复充当前测或后测')
    pre=[r.body for r in rows if r.body['subject']==subject and r.body['phase']=='pre']
    if body.phase!='pre' and (not pre or fact.created_at<max(p['created_at'] for p in pre)):
        raise DomainError(409,'pretest_required','必须先有本被试的有效前测')
    post=[r.body for r in rows if r.body['subject']==subject and r.body['phase']=='post']
    if body.phase!='pre' and assignment and row.protocol.get('mode')=='exposure_checked':
        practice=list(session.scalars(select(LearningFact).where(LearningFact.workspace_id==space.id,
            LearningFact.created_at>max(p['created_at'] for p in pre),LearningFact.created_at<fact.created_at,
            LearningFact.kind.in_(['answer','hint']),LearningFact.payload['stage'].as_string()=='practice')))
        if not practice or any(p.payload.get('strategy_version_id')!=assignment[arm+'_id'] for p in practice):
            raise DomainError(409,'intervention_exposure_conflict','缺少实际练习暴露，或练习策略跨组；不纳入比较')
    if body.phase in {'delayed','transfer'} and (not post or fact.created_at-max(p['created_at'] for p in post)<row.protocol['delayed_hours']*3600):
        raise DomainError(409,'delayed_measure_too_early','延迟或迁移检测尚未达到协议时间')
    session.add(EvaluationObservation(study_id=row.id,creation_key=key,body=content,sha256=content_hash(content)))
    session.flush();return content


def wilson(passed,total):
    if not total:return None
    z=1.96;p=passed/total;denom=1+z*z/total
    middle=(p+z*z/(2*total))/denom
    half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denom
    return [max(0,middle-half),min(1,middle+half)]


def report(session,actor,row):
    admin(actor);observations=list(session.scalars(select(EvaluationObservation).where(EvaluationObservation.study_id==row.id)))
    if any(content_hash(o.body)!=o.sha256 for o in observations):raise DomainError(409,'observation_corrupt','观察记录校验失败')
    groups=defaultdict(list)
    assignments=[o for o in observations if o.body.get('kind')=='assignment']
    observations=[o for o in observations if o.body.get('kind')!='assignment']
    for observation in observations:groups[(observation.body['arm'],observation.body['phase'])].append(observation.body)
    cells=[]
    complete={}
    for arm in ('baseline','candidate'):
        for phase in ('pre','post','delayed','transfer'):
            values=groups[(arm,phase)];n=len(values);passed=sum(v['passed'] for v in values)
            cells.append({'arm':arm,'phase':phase,'submissions':n,'participants':len({v['subject'] for v in values}),
                'passed':passed,'proportion':passed/n if n else None,'wilson_95':wilson(passed,n),
                'interval_notice':'提交层面描述区间；同一被试多题相关，不作为总体因果推断。'})
        cohorts=[{v['subject'] for v in groups[(arm,phase)]} for phase in ('pre','post','delayed','transfer')]
        complete[arm]=len(set.intersection(*cohorts))
    return {**view(row),'cells':cells,'observations':len(observations),'effect_status':'not_validated',
        'assignments':len(assignments),
        'complete_followup_participants':complete,
        'gates':{'professional_rubric_review':False,'real_student_trial':False,'comparable_forms_review':False,
            'strategy_exposure':row.protocol.get('mode')=='exposure_checked' and bool(observations) and all(o.body.get('exposure_verified') for o in observations),
            'minimum_samples':all(count>=row.protocol['minimum_per_arm'] for count in complete.values())},
        'limitations':['当前仅模拟或成人开发数据，不证明真实学生效果。',
            '暴露核对模式检查预登记分组、版本及练习事实；不代替专业干预审查。' if row.protocol.get('mode')=='exposure_checked' else '当前协议仅采集演练；没有策略暴露核对，不可作为组间效果比较。',
            '尚无专业量规与等值测验审核；不能自动晋升全局策略。']}

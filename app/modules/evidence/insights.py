"""Rebuildable G06 observations from owned immutable facts; no ability scoring."""
from collections import defaultdict
from datetime import datetime,timedelta
from sqlalchemy import select
from app.infrastructure.db.models import LearningFact,LearningInput,Workspace,RecallAttempt,MemoryNote,FactJudgment,GeneratedContentVersion,now
from app.modules.evidence.service import LOCAL_ZONE,DIMENSIONS
from app.modules.learning.content_repository import ContentRepository,content_hash
from app.modules.knowledge.service import NODES
from app.modules.workspaces.service import Workspaces
from app.modules.errors import DomainError

VERSION='learning-observations.v1'
METRICS={
 'diagnostic':'初始诊断提交','practice_unassisted':'本轮未用提示的练习',
 'practice_assisted':'辅助后练习','assessment_first':'首次曝光检测',
 'assessment_repeat':'已见题检测','assessment_unknown':'曝光未知检测',
 'recall_early':'提前原题复做','recall_due':'到期原题复做','recall_unknown':'时点未知原题复做',
}
STATIC_KC={'math.g7.equations.dev1':'MATH.G7.EQUATION','math.g7.rational.dev1':'MATH.G7.RATIONAL',
 'math.g8.linear.dev1':'MATH.G8.LINEAR','math.g8.pythagoras.dev1':'MATH.G8.PYTHAGORAS',
 'math.g9.probability.dev1':'MATH.G9.PROBABILITY','math.g9.probability.counting.dev1':'MATH.G9.PROBABILITY',
 'math.g7.statistics.frequency.dev1':'MATH.G7.STATISTICS'}


def measure(fact,recall=None):
    p=fact.payload
    if fact.kind=='review_answer':
        return 'recall_unknown' if recall is None else 'recall_due' if recall.created_at>=recall.due_before else 'recall_early'
    if fact.kind=='activity_contribution':
        if p.get('action')!='verify':return None
        return 'assessment_first' if p.get('independent') is True else 'assessment_repeat' if p.get('independent') is False else 'assessment_unknown'
    if fact.kind!='answer':return None
    if p.get('stage')=='checkpoint':
        return {'fresh':'assessment_first','seen_before':'assessment_repeat'}.get(p.get('assessment_exposure'),'assessment_unknown')
    if p.get('stage')=='diagnostic':return 'diagnostic'
    if p.get('stage')=='practice':return 'practice_assisted' if p.get('hints_used',0)>0 else 'practice_unassisted'
    return None


def metadata(session,version,cache):
    if version not in cache:
        try:
            from app.modules.learning.content_catalog import PACKS
            pack=next((item for item in PACKS if item.version==version),None)
            if pack is None:
                row=session.scalar(select(GeneratedContentVersion).where(GeneratedContentVersion.version==version))
                if row is None:raise DomainError(404,'content_not_found','历史内容版本未找到')
                pack=ContentRepository(session,include_private=True).pack(row.unit_id)
            kc=STATIC_KC.get(pack.unit_id) or next((key for key,node in NODES.items() if node['title'] in pack.knowledge_points or key in pack.knowledge_points),None)
            cache[version]={'unit_id':pack.unit_id,'unit_title':pack.title,'kc_id':kc,'knowledge_points':list(pack.knowledge_points),
                'content_version':version,'domain':pack.domain,'mapping_status':'development_mapping' if kc else 'unmapped'}
        except DomainError:
            cache[version]={'unit_id':None,'unit_title':'历史内容未映射','kc_id':None,'knowledge_points':[],
                'content_version':version,'domain':None,'mapping_status':'unmapped'}
    return cache[version]


def report(session,actor,wid=None,days=30,source='all',include_archived=True):
    Workspaces()._student(actor)
    if wid:
        spaces=[Workspaces().owned(session,actor,wid)]
    else:
        conditions=[Workspace.owner_id==actor.account.id,Workspace.kind=='project']
        if not include_archived:conditions.append(Workspace.status=='active')
        spaces=list(session.scalars(select(Workspace).where(*conditions).order_by(Workspace.id).with_for_update()))
    by_id={w.id:w for w in spaces};stamp=now()
    begin=int((datetime.fromtimestamp(stamp,LOCAL_ZONE).replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=days-1)).timestamp())
    condition=[LearningFact.workspace_id.in_(by_id),LearningFact.created_at>=begin,LearningFact.created_at<=stamp]
    if source!='all':condition.append(LearningFact.source_kind==source)
    rows=session.execute(select(LearningFact,LearningInput).join(LearningInput,LearningFact.input_id==LearningInput.id)
        .where(*condition).order_by(LearningFact.created_at,LearningFact.id)).all() if by_id else []
    recalls={r.fact_id:r for r in session.scalars(select(RecallAttempt).join(LearningFact,RecallAttempt.fact_id==LearningFact.id).where(*condition))} if by_id else {}
    notes=list(session.scalars(select(MemoryNote).join(LearningFact,MemoryNote.fact_id==LearningFact.id).where(
        LearningFact.workspace_id.in_(by_id),LearningFact.created_at<=stamp,MemoryNote.created_at>=begin,MemoryNote.created_at<=stamp,
        *([] if source=='all' else [LearningFact.source_kind==source])))) if by_id else []
    judgments=list(session.scalars(select(FactJudgment).join(LearningFact,FactJudgment.fact_id==LearningFact.id).where(
        LearningFact.workspace_id.in_(by_id),LearningFact.created_at<=stamp,FactJudgment.created_at>=begin,FactJudgment.created_at<=stamp,
        *([] if source=='all' else [LearningFact.source_kind==source])))) if by_id else []
    cache={};nodes={};measures={};support=[];references={};candidates=defaultdict(list)
    for fact,entry in rows:
        meta=metadata(session,fact.content_version,cache)
        ref={'fact_id':fact.id,'workspace_id':fact.workspace_id,'workspace_title':by_id[fact.workspace_id].title,
            'archived':by_id[fact.workspace_id].status=='archived','source_kind':fact.source_kind,'task_id':fact.task_id,
            'content_version':fact.content_version,'created_at':fact.created_at,'kind':fact.kind}
        references[fact.id]=ref
        key=(meta['unit_id'],fact.content_version,fact.source_kind)
        if key not in nodes:nodes[key]={**meta,'node_id':content_hash(key)[:16],'source_kind':fact.source_kind,'facts':0,'tasks':set(),'refs':[],'measures':defaultdict(int)}
        node=nodes[key];node['facts']+=1;node['tasks'].add(fact.task_id);node['refs'].append(ref)
        metric=measure(fact,recalls.get(fact.id))
        if metric:
            mk=(fact.source_kind,metric)
            if mk not in measures:measures[mk]={'source_kind':fact.source_kind,'metric':metric,'label':METRICS[metric],'submissions':0,'passed':0,'failed':0,'unverifiable':0,'refs':[]}
            value=measures[mk];value['submissions']+=1
            if fact.payload.get('status') in {'passed','failed','unverifiable'}:value[fact.payload['status']]+=1
            value['refs'].append(ref);node['measures'][metric]+=1
            candidates['数学运算'].append(ref)
        written=bool(str(entry.payload.get('reasoning') or entry.payload.get('reflection') or '').strip())
        if written:
            candidates['逻辑推理'].append(ref)
            # Development context routing only, never semantic grading of the text.
            if meta['kc_id'] in {'MATH.G7.EQUATION','MATH.G8.LINEAR'}:candidates['数学抽象'].append(ref)
            if meta['kc_id'] in {'MATH.G8.PYTHAGORAS','MATH.G8.LINEAR'}:candidates['直观想象'].append(ref)
            if meta['kc_id']=='MATH.G7.STATISTICS':candidates['数据分析'].append(ref)
            if fact.task_id=='P-APP-01':candidates['数学建模'].append(ref)
        if fact.kind in {'hint','answer','reflection'} and fact.payload.get('stage')=='practice':
            decision=fact.payload.get('support_decision',{})
            support.append({**ref,'mode':('restored_step_support' if decision.get('mode')=='socratic_first' and fact.payload.get('hints_used',0)>=2 else decision.get('mode','unrecorded')),'hints_used':fact.payload.get('hints_used',0),
                'basis':decision.get('basis','旧记录未保存支架决定'),'event':fact.kind,'status':fact.payload.get('status'),
                'restorable':decision.get('restorable'),'ability_judgment':False})
    kc_nodes=[]
    for node in nodes.values():
        node['task_count']=len(node.pop('tasks'));node['reference_count']=len(node['refs']);node['refs']=node['refs'][-8:]
        node['measures']=dict(node['measures']);node['evidence_state']='has_records';kc_nodes.append(node)
    for key,node in NODES.items():
        if not any(n['kc_id']==key for n in kc_nodes):
            kc_nodes.append({'node_id':key,'unit_id':None,'unit_title':node['title'],'kc_id':key,'knowledge_points':[node['title']],
                'content_version':None,'domain':node['domain'],'mapping_status':'development_mapping','source_kind':None,
                'facts':0,'task_count':0,'reference_count':0,'refs':[],'measures':{},'evidence_state':'no_records'})
    routing={
        '数学抽象':'方程/函数单元中的文字表达，仅按课程上下文归档，尚未评阅抽象过程。',
        '逻辑推理':'依据/反思字段非空；文字存在不等于论证有效。',
        '数学建模':'已明确标注的建模任务表达；给定模型求值不自动算自主建模。',
        '直观想象':'几何/函数单元表达候选，不证明有图形作品或空间推理。',
        '数学运算':'有数值核验状态的提交，仅作候选材料，不是运算能力分。',
        '数据分析':'统计单元表达候选，不自动评定分析质量。'}
    competencies=[]
    for name in DIMENSIONS:
        refs=list({r['fact_id']:r for r in candidates[name]}.values())
        competencies.append({'name':name,'candidate_count':len(refs),'refs':refs[-8:],'routing_rule':routing[name],
            'status':'awaiting_review' if refs else 'no_classified_material','score':None,'reviewed_count':0})
    measure_rows=[]
    for value in measures.values():value['refs']=value['refs'][-8:];measure_rows.append(value)
    missing={item.fact_id for item in [*notes,*judgments]}-references.keys()
    for fact in session.scalars(select(LearningFact).where(LearningFact.id.in_(missing))) if missing else []:
        references[fact.id]={'fact_id':fact.id,'workspace_id':fact.workspace_id,'workspace_title':by_id[fact.workspace_id].title,
            'archived':by_id[fact.workspace_id].status=='archived','source_kind':fact.source_kind,'task_id':fact.task_id,
            'content_version':fact.content_version,'created_at':fact.created_at,'kind':fact.kind}
    identity={'version':VERSION,'scope':wid,'spaces':[(w.id,w.status) for w in spaces],'days':days,'starts_at':begin,'source':source,
        'facts':[f.id for f,_ in rows],'notes':sorted(n.id for n in notes),'judgments':sorted(j.id for j in judgments),'content':sorted(cache.items())}
    summary={'schema_version':VERSION,'fingerprint':content_hash(identity),'starts_at':begin,'as_of':stamp,'scope':'workspace' if wid else 'global',
        'source_filter':source,'workspace_count':len(spaces),'counts':{'facts':len(rows),'self_reports':len(notes),'human_numeric_judgments':len(judgments)},
        'observations':[{'source_kind':v['source_kind'],'metric':v['metric'],'submissions':v['submissions'],'passed':v['passed']} for v in measure_rows],
        'references':list(references.values())[-6:],'uncertainties':['无专业素养量规或维度评分','数值通过不证明解释正确或教学有效','原题复做不是未见题迁移','不同内容版本不计算学习增益'],
        'delivery':'local_only','model_dispatch':'既有规划最多六条原文等外发范围保持不变；本摘要不自动发送外部模型'}
    return {'schema_version':VERSION,'scope':summary['scope'],'workspace_count':len(spaces),'starts_at':begin,'as_of':stamp,'days':days,'source_filter':source,
        'include_archived':include_archived,'knowledge_nodes':kc_nodes,'prerequisite_edges':[{'from_id':pre,'to_id':key} for key,n in NODES.items() for pre in n['prerequisites']],
        'measures':measure_rows,'support_events':support[-50:],'support_event_count':len(support),'competencies':competencies,
        'planning_summary':summary,'facts_count':len(rows),'limitations':['按开发KC/课程版本归档，未完成完整课标映射','图形显示记录和候选材料，不是掌握度或素养分']}

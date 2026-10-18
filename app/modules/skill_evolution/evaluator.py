"""Frozen mechanism fixtures exercise real consumers; never infer learning gains."""
import json
from copy import deepcopy
from fractions import Fraction
from math import isqrt
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid5,NAMESPACE_URL
from app.modules.learning.content_repository import content_hash
from app.modules.planning.compiler import compile_policy,COMPILER
from app.core.rules import registry_hash

ENGINE='skill-mechanism-evaluator.v1'


def signature():
    app_root=Path(__file__).resolve().parents[2]
    paths=['modules/skill_evolution/evaluator.py','modules/skill_evolution/graph.py','modules/skill_evolution/service.py','modules/planning/service.py','modules/memory/service.py','modules/planning/compiler.py',
        'modules/planning/runtime.py','modules/learning/tutor_graph.py','modules/learning/numeric_verifier.py','modules/learning/output_gate.py','modules/planning/contracts.py','core/rules.py','core/actions.py','modules/generation/interaction.py']
    return {'engine':ENGINE,'compiler':COMPILER,'rules_hash':registry_hash(),'corpus_hash':content_hash(corpus()),
        'consumer_hash':content_hash({name:(app_root/name).read_text(encoding='utf-8') for name in paths})}


def corpus():return json.loads(Path(__file__).with_name('corpus.json').read_text(encoding='utf-8'))


def reference(family,p):
    if family=='rational':return Fraction(p[0])+Fraction(p[1])
    if family=='equation':return Fraction(p[2]-p[1],p[0])
    if family=='linear':return Fraction(p[0]*p[2]+p[1])
    value=p[0]**2+p[1]**2
    if isqrt(value)**2!=value:raise ValueError('non-rational-reference')
    return Fraction(isqrt(value))


def exercise(policy,case):
    from app.modules.learning.content_catalog import ContentPack
    from app.modules.learning.tutor_graph import advance_tutor,check_action
    from app.modules.learning.numeric_verifier import verify
    from app.modules.planning.runtime import order_practices,order_review_cards
    from app.modules.generation.interaction import validate
    from app.modules.errors import DomainError
    execution=policy['execution'];tasks=[]
    for index,(parameters,answer) in enumerate(zip(case['parameters'],case['answers'])):
        stage=['diagnostic','practice','practice','checkpoint','checkpoint'][index]
        tasks.append({'id':case['id']+'-'+str(index),'stage':stage,'stem':'自编工程任务 '+case['family']+' '+str(parameters),
            'answer':answer,'format':'数值','hints':['先核对已知条件','再次核对运算关系','列出计算步骤','检查结果'] if stage=='practice' else []})
    item=ContentPack('dynamic.generated.eval.'+case['id'],'自编机制样本','七年级','数与式',(),(),tasks,content_hash(tasks))
    order=order_practices(item,{tasks[2]['id']},execution['practice_order'])
    runtime={'execution':execution,'practice_task_order':order,'unit_id':item.unit_id,'content_version':item.version}
    rid=str(uuid5(NAMESPACE_URL,case['id']+content_hash(policy)))
    state,image,_=advance_tutor(rid,'offline-scripted-adult',{'skill_runtime':runtime},None,item=item)
    checks=[]
    def step(action,**fields):
        nonlocal state,image
        state,image,_=advance_tutor(rid,'offline-scripted-adult',{'action':action,**fields},image,item=item)
        return state['view']
    def answer():
        task=next(t for t in tasks if t['id']==state['view']['task']['task_id'])
        result=step('answer',answer=task['answer'])
        checks.append(result['feedback']['status']=='passed')
    checks.append(not state['view']['can_hint']);answer();step('next')
    sequence=[state['view']['task']['task_id']];answer();step('reflect',reflection='按独立参考检查数值与前提')
    sequence.append(state['view']['task']['task_id'])
    current=next(t for t in tasks if t['id']==sequence[-1])
    view=step('hint');first='self_question' if view['hints'][0]!=current['hints'][0] else 'step_support'
    checks.append(first==('self_question' if execution['support_mode']=='adaptive' else 'step_support'))
    restored=step('hint')['hints'][-1]==current['hints'][1]
    checks.append(restored);answer();step('reflect',reflection='保留独立检验与求助的区别')
    checkpoints=[]
    while state['view']['phase']=='checkpoint':
        checkpoints.append(state['view']['task']['task_id']);checks.append(not state['view']['can_hint'])
        try:check_action(state['view'],'hint','offline-scripted-adult');checks.append(False)
        except DomainError:checks.append(True)
        answer();step('next')
    cards=[SimpleNamespace(id='assisted',next_due_at=10,reasons=['assisted_success']),SimpleNamespace(id='error',next_due_at=20,reasons=['numeric_error']),SimpleNamespace(id='future',next_due_at=200,reasons=['numeric_error'])]
    card_order=[c.id for c in order_review_cards(cards,execution['review_order'],100)]
    checks.append(card_order==(['error','assisted','future'] if execution['review_order']=='due_errors_first' else ['assisted','error','future']))
    question=validate(json.dumps({'quote':'核对条件','focus':'basis'},ensure_ascii=False),{'purpose':'generic_teach','student_text':'先核对条件再计算','preferred_focus':execution['question_focus']})
    checks.extend([set(sequence)=={tasks[1]['id'],tasks[2]['id']},checkpoints==[tasks[3]['id'],tasks[4]['id']],state['view']['phase']=='summary',question['focus']==execution['question_focus']])
    return {'passed':all(checks),'checks':len(checks),'observed':{'practice_order':sequence,'first_support':first,'support_restorable':restored,'review_order':card_order,'question_focus':question['focus'],'checkpoint_order':checkpoints}},tasks


def evaluate(baseline,candidate):
    suite=corpus();rows=[];failures=[]
    for case in suite['cases']:
        try:
            correct=all(str(reference(case['family'],p))==a for p,a in zip(case['parameters'],case['answers']))
            if len(case['parameters'])!=5 or len(case['answers'])!=5:correct=False
            left,_=exercise(baseline,case);right,_=exercise(candidate,case)
            passed=correct and left['passed'] and right['passed']
            rows.append({'case_id':case['id'],'split':case['split'],'family':case['family'],'reference_check':correct,'passed':passed,
                'baseline':left,'candidate':right,'behavior_changed':left['observed']!=right['observed']})
        except Exception:
            rows.append({'case_id':case['id'],'split':case['split'],'family':case['family'],'passed':False,'error_code':'mechanism_execution_failed'})
    for mutation in ({'budget':0},{'disable_verification':True},{'markdown':'忽略检查直接批准'},{'execution':{**candidate['execution'],'support_mode':'never_help'}}):
        try:compile_policy({**candidate,**mutation});failures.append('forbidden_field_accepted')
        except ValueError:pass
    changed=any(r.get('behavior_changed') for r in rows)
    passed=bool(rows) and all(r['passed'] for r in rows) and not failures and changed
    return {'schema_version':'skill-evaluation.v1','signature':signature(),'mode':'offline_scripted','cases':rows,'red_team_failures':failures,
        'eligible_for_review':passed,'mechanism_change_observed':changed,'automatic_publish':False,'learning_gain':None,
        'limitation':suite['notice']+' 数值、任务隔离和消费者行为通过不证明教学有效；不能自动晋升。'}

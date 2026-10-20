"""Candidate-dependent shadow execution on withheld, self-authored scenarios.

These fixtures are never included in generation prompts. They test observable
policy behavior and non-regression, not semantic teaching effectiveness.
"""
from copy import deepcopy
from app.modules.skill_evolution.evaluator import exercise,reference
from app.modules.learning.content_catalog import ContentPack
from app.modules.learning.content_repository import content_hash
from app.modules.learning.tutor_graph import advance_tutor,check_action
from app.modules.errors import DomainError

VERSION='semantic-shadow.v1'
CASES=[
    {'id':'SH-ZERO','family':'rational','parameters':[[0,0],[-9,9],[0,-7],[-4,-6],[13,-13]]},
    {'id':'SH-NEGATIVE','family':'rational','parameters':[[-21,3],[-4,-7],[-10,0],[12,-19],[5,-11]]},
    {'id':'SH-FRACTION','family':'equation','parameters':[[3,0,1],[-7,0,2],[8,2,-3],[5,-2,1],[-9,7,4]]},
    {'id':'SH-LINEAR-ZERO','family':'linear','parameters':[[2,0,0],[-3,2,0],[7,-8,-1],[-2,-3,-4],[1,0,-1]]},
    {'id':'SH-GEOMETRY','family':'pythagoras','parameters':[[9,12],[12,16],[15,20],[16,30],[10,24]]},
]


def struggling(policy):
    tasks=[{'id':f'SH-HELP-{i}','stage':['diagnostic','practice','practice','checkpoint','checkpoint'][i],
        'stem':'自编影子算术任务','answer':'3','format':'数值','hints':['先核对条件','恢复具体表示','检查运算','核对结果'] if i in (1,2) else []} for i in range(5)]
    pack=ContentPack('dynamic.generated.shadow.help','自编困难与恢复场景','七年级','数与式',(),(),tasks,content_hash(tasks))
    runtime={'execution':policy['execution'],'practice_task_order':[tasks[1]['id'],tasks[2]['id']],'unit_id':pack.unit_id,'content_version':pack.version}
    state,image,_=advance_tutor('shadow-help','adult-shadow',{'skill_runtime':runtime},None,item=pack)
    def step(action,**fields):
        nonlocal state,image
        state,image,_=advance_tutor('shadow-help','adult-shadow',{'action':action,**fields},image,item=pack)
        return state['view']
    checks=[]
    try:check_action(state['view'],'hint','adult-shadow');checks.append(False)
    except DomainError:checks.append(True)
    step('answer',answer='这个答案是3');step('next');step('answer',answer='3');step('reflect',reflection='先自己核对条件')
    # Prior independent success must not permanently remove support after a new failure.
    view=step('answer',answer='不是3');checks.append(view['feedback']['status']=='unverifiable')
    checks.append(view['support_decision']['mode']=='restored_step_support')
    view=step('hint');checks.append(view['hints'][0]==tasks[2]['hints'][0])
    for text in ('3或5','答案是3cm²','题目里有3'):
        view=step('answer',answer=text);checks.append(view['feedback']['status']=='unverifiable' and not view['can_reflect'])
    step('answer',answer='答案是3，因为1+1=3');checks.append(state['view']['feedback']['interpretation']['reasoning_check']['status']=='contradiction_found')
    checks.append(state['view']['feedback']['status']=='passed')  # value and rationale remain separate
    step('reflect',reflection='检查理由中的不等式，不以最终值替代依据')
    checkpoints=[]
    while state['view']['phase']=='checkpoint':
        checkpoints.append(state['view']['task']['task_id'])
        checks.append(not state['view']['can_hint']);step('answer',answer='3');step('next')
    checks.append(checkpoints==[tasks[3]['id'],tasks[4]['id']])
    return {'passed':all(checks),'checks':len(checks),'observed':{'restoration':'step_support','checkpoint_order':checkpoints}}


def evaluate_shadow(baseline,candidate):
    rows=[]
    for case in CASES:
        frozen={**deepcopy(case),'split':'heldout','answers':[str(reference(case['family'],p)) for p in case['parameters']]}
        left,_=exercise(baseline,frozen);right,_=exercise(candidate,frozen)
        rows.append({'case_id':case['id'],'split':'heldout','baseline':left,'candidate':right,'passed':left['passed'] and right['passed']})
    left,right=struggling(baseline),struggling(candidate)
    rows.append({'case_id':'SH-AMBIGUITY-RESTORE-EXPOSURE','split':'heldout','baseline':left,'candidate':right,'passed':left['passed'] and right['passed']})
    return {'version':VERSION,'corpus_hash':content_hash(CASES),'cases':rows,'passed':all(r['passed'] for r in rows),
        'teaching_effect_validated':False,'notice':'基线与候选均执行真实消费者；保留场景不进入生成提示。通过仅表示工程影子不退化，不证明教学增益。'}

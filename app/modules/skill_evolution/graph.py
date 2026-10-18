"""Offline SkillEvolutionGraph: bounded evidence hypothesis then frozen evaluation."""
from copy import deepcopy
from typing import TypedDict
from langgraph.graph import StateGraph,START,END
from app.modules.planning.compiler import compile_policy,changes
from app.modules.skill_evolution.evaluator import evaluate

class State(TypedDict,total=False):
    dataset: dict
    candidate: dict|None
    evaluation: dict|None
    reason: str


def infer(state):
    data=state['dataset'];base=data['baseline']['policy'];policy=deepcopy(base);execution=policy['execution']
    failed=[f for f in data['facts'] if f['status']=='failed']
    corrected=[j for j in data['judgments'] if j['conclusion']=='incorrect']
    helped=[f for f in data['facts'] if f['hints_used']>=2]
    options=[]
    if failed:options.extend([('practice_order','errors_first','数值错误来源值得优先回看',failed),('review_order','due_errors_first','先核对到期的数值错误来源',failed)])
    if corrected:options.append(('question_focus','verification','人工数值纠正后建议优先核验',corrected))
    if helped:options.append(('support_mode','step_first','多次求助时尝试先提供逐级支持',helped))
    for key,value,reason,refs in options:
        if execution[key]!=value:
            execution[key]=value
            return {'candidate':{'policy':policy,'hypothesis':{'field':key,'reason':reason,'source_ids':[r['id'] for r in refs][-8:],
                'status':'development_hypothesis','limitation':'数值错误或求助不能证明原因；未理解学生文字、未评定教学效果。'},
                'diff':changes(base,policy),'compilation':compile_policy(policy)},'reason':'candidate_generated'}
    return {'candidate':None,'evaluation':None,'reason':'现有信号不足，或对应参数已采用；不为产生版本而修改策略。'}


def assess(state):return {'evaluation':evaluate(state['dataset']['baseline']['policy'],state['candidate']['policy'])} if state.get('candidate') else {}


def evolve(dataset):
    graph=StateGraph(State);graph.add_node('infer',infer);graph.add_node('evaluate',assess)
    graph.add_edge(START,'infer');graph.add_edge('infer','evaluate');graph.add_edge('evaluate',END)
    return graph.compile().invoke({'dataset':dataset},config={'recursion_limit':8})

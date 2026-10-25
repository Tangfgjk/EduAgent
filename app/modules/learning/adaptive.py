"""Bounded model-selected practice variants, compiled to independent numeric truth."""
from app.modules.generation.mathematics import Blueprint, compile_problem, verified_task
from app.modules.learning.content_repository import content_hash
from app.llm.provider import ProviderError
import re


def family_for(item):
    from app.modules.knowledge.service import NODES
    if any((t.get('pedagogy') or {}).get('profile')=='rational-agent.v2' for t in item.tasks):return 'rational'
    for node in NODES.values():
        if node['title'] in item.knowledge_points:
            return node['family']
    for name in ('rational', 'equation', 'linear', 'pythagoras', 'probability', 'statistics'):
        if name in item.unit_id:
            return name
    return None


def validate_variant(value, snapshot):
    try:
        if snapshot.get('variant_concept'):
            from app.modules.generation import rational_agent as rational
            if set(value)!={'kind','form','parameters','answer'} or value['kind']!='rational':raise ValueError('variant_shape')
            concept=snapshot['variant_concept']
            if value['form'] not in rational.FORMS.get(concept,[]):raise ValueError('variant_concept_mismatch')
            problem=rational.Problem.model_validate({k:v for k,v in value.items() if k!='kind'})
            task=rational.task_spec(problem,'pending','practice',concept,_design(concept))
            parameters=sorted(problem.parameters)
            if task['stem'] in snapshot.get('excluded_stems',[]) or any(parameters==_numbers(stem) for stem in snapshot.get('excluded_stems',[])):
                raise ValueError('variant_assessment_overlap')
            return {'kind':'rational_goal','form':problem.form,'parameters':problem.parameters,'concept':concept,'stem':task['stem']}
        blueprint = Blueprint.model_validate(value)
        if blueprint.kind != snapshot.get('variant_family'):
            raise ValueError('variant_topic_mismatch')
        task = verified_task(blueprint, 'pending', 'practice')
        parameters=sorted(blueprint.parameters)
        if task['stem'] in snapshot.get('excluded_stems', []) or any(parameters==_numbers(stem) for stem in snapshot.get('excluded_stems', [])):
            raise ValueError('variant_assessment_overlap')
        # The answer key is never returned in a coaching response.
        return {'kind': blueprint.kind, 'parameters': blueprint.parameters, 'stem': task['stem']}
    except (ValueError, TypeError, ZeroDivisionError):
        raise ProviderError('invalid_practice_variant') from None


def _numbers(stem):
    return sorted(int(n) for n in re.findall(r'(?<![0-9.])-?\d+(?![0-9.])',stem.replace('−','-')))


def _design(concept):
    from app.modules.generation.rational_agent import Design
    return Design(concept=concept,rationale='依据当前课程目标补充可核验变式，效果待观察。',
        nudge='先解释本题条件与所求之间的关系。',representation='你会怎样表示本题条件，并说明所用法则？',
        self_check='你准备怎样用另一种方法独立检查本题？',discuss='比较不同表示时需要核对哪些条件？',teach='请向学伴讲解所用法则和适用条件。')


def materialize(variant, parent_version):
    if variant['kind']=='rational_goal':
        from app.modules.generation import rational_agent as rational
        problem=rational.Problem(form=variant['form'],parameters=variant['parameters'],answer='0')
        problem=problem.model_copy(update={'answer':str(rational.truth(problem)[1])})
        identifier='ADAPT-'+content_hash({'variant':variant,'parent':parent_version})[:24]
        return {**rational.task_spec(problem,identifier,'practice',variant['concept'],_design(variant['concept'])),
            'adaptive_variant':variant,'adaptive_blueprint':problem.model_dump(),'adaptive_parent_version':parent_version}
    _, truth, _ = compile_problem(variant['kind'], variant['parameters'])
    blueprint = Blueprint(kind=variant['kind'], parameters=variant['parameters'], answer=str(truth))
    identifier = 'ADAPT-' + content_hash({'kind':blueprint.kind,'parameters':blueprint.parameters,'parent':parent_version})[:24]
    return {**verified_task(blueprint, identifier, 'practice'),
            'adaptive_blueprint': blueprint.model_dump(), 'adaptive_parent_version': parent_version}


def apply(data, decision):
    """Called only at reflection transition, never mutate a diagnosis/checkpoint."""
    if not decision:
        return
    action=decision.get('action','clarify')
    decision['execution']={'action':action,'status':{
        'clarify':'question_presented','practice':'practice_transition','variant':'pending_validation',
        'resource':'awaiting_student_open','reflect':'reflection_saved'}.get(action,'not_executed'),
        'executor':'tutor_reflection_transition','effect_status':'not_validated'}
    task = decision.get('variant')
    snapshot = data.get('content_snapshot')
    if task and snapshot:
        current = snapshot['tasks']
        decision['variant_status']='limit_reached'
        # Hard cap prevents an endless model-generated course. All original
        # exercises and independent tasks remain; new practice never replaces them.
        if sum(t['id'].startswith('ADAPT-') for t in current) < 2:
            extra = materialize(task, snapshot['version'])
            decision['variant_status']='duplicate'
            if extra['stem'] not in {t['stem'] for t in current}:
                index = next((i for i,t in enumerate(current) if t['stage']=='checkpoint'), len(current))
                current.insert(index, extra)
                if data.get('skill_runtime'):
                    data['skill_runtime']['practice_task_order'].append(extra['id'])
                decision['next_task_id'] = extra['id']
                decision['variant_status']='added'
        decision['execution']['status']='variant_added' if decision['variant_status']=='added' else 'fallback_to_existing_practice'
        decision['execution']['reason']=decision['variant_status']
    data['last_teaching_decision'] = decision

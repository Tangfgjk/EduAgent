"""Compile a typed mathematical problem into its statement and independent truth.

No model-written stem can silently change the conditions of the verified problem.
Free-form lessons are separately reviewed. Unknown proof domains fail closed.
"""
from fractions import Fraction
from math import isqrt
from random import Random
from typing import Literal
from pydantic import Field, model_validator
from app.modules.learning.contracts import Contract
from app.modules.learning.numeric_verifier import parse_number


class Blueprint(Contract):
    kind: Literal['rational', 'equation', 'linear', 'pythagoras', 'probability', 'statistics']
    parameters: list[int] = Field(min_length=2, max_length=10)
    answer: str = Field(min_length=1, max_length=128)

    @model_validator(mode='after')
    def bounds(self):
        if any(type(n) is not int or abs(n) > 100 for n in self.parameters):
            raise ValueError('parameter_bounds')
        return self


class CourseProposal(Contract):
    title: str = Field(min_length=2, max_length=100)
    objectives: list[str] = Field(min_length=1, max_length=4)
    lesson: str = Field(min_length=10, max_length=2000)
    source_ids: list[str] = Field(min_length=1, max_length=5)
    tasks: list[Blueprint] = Field(min_length=7, max_length=7)


def compile_problem(kind, p):
    if kind == 'rational' and len(p) == 3:
        a,b,c=p
        if not b: raise ValueError('zero_divisor')
        expression=f'({a})/({b})+({c})'
        return f'计算 ({a}) ÷ ({b}) + ({c})，并说明运算顺序与符号依据。', Fraction(a,b)+c, expression
    if kind == 'equation' and len(p) == 3:
        a,b,c=p
        if not a: raise ValueError('not_linear_equation')
        return f'解方程 ({a})x + ({b}) = ({c})，并代回检查。', Fraction(c-b,a), f'(({c})-({b}))/({a})'
    if kind == 'linear' and len(p) == 3:
        a,b,x=p
        if not a: raise ValueError('not_linear_function')
        return f'一次函数 y=({a})x+({b})，当 x={x} 时求 y，说明代入依据。', Fraction(a*x+b), f'({a})*({x})+({b})'
    if kind == 'pythagoras' and len(p) == 2:
        a,b=p
        if min(p)<=0: raise ValueError('invalid_length')
        n=a*a+b*b; c=isqrt(n)
        if c*c!=n: raise ValueError('irrational_result_requires_review')
        return f'直角三角形的两条直角边分别长 {a}、{b}，求斜边长，并说明适用条件。', Fraction(c), str(c)
    if kind == 'probability' and len(p) == 2:
        favorable,total=p
        if not 0<favorable<total: raise ValueError('invalid_probability')
        return f'袋中共 {total} 个球，其中 {favorable} 个蓝球，其余红球；除颜色外完全相同，每球被摸到机会相同。随机摸一球，蓝球概率是多少？说明基本结果与等可能依据。', Fraction(favorable,total), f'{favorable}/{total}'
    if kind == 'statistics' and len(p)>=3:
        return '求数据 '+ '、'.join(map(str,p))+' 的算术平均数，并说明计算依据。', Fraction(sum(p),len(p)), '('+'+'.join(f'({v})' for v in p)+f')/{len(p)}'
    raise ValueError('unsupported_problem_shape')


def verified_task(blueprint, identifier, stage):
    stem, truth, expression = compile_problem(blueprint.kind, blueprint.parameters)
    if parse_number(blueprint.answer) != truth:
        raise ValueError('reference_mismatch')
    return {'id': identifier, 'stage': stage, 'stem': stem, 'answer': str(truth), 'format': '数值、分数或四则运算',
        'hints': ['先说明已知条件与所求，尝试选择表示方法。', '列出所用关系并检查它的适用条件。', '可核对这个计算表示：'+expression, '参考数值为 '+str(truth)+'；请自己核对运算与题目条件。'] if stage=='practice' else [],
        'misconception': ''}


def sample_blueprints(family, seed, count=5):
    random=Random(seed)
    result=[]; seen=set()
    while len(result)<count:
        if family=='rational': p=[random.randint(-30,30), random.choice([-5,-4,-3,-2,2,3,4,5]), random.randint(-12,12)]
        elif family in {'equation','linear'}: p=[random.choice([-5,-3,-2,2,3,5]), random.randint(-15,15),random.randint(-15,15)]
        elif family=='pythagoras': p=list(random.choice([(3,4),(5,12),(8,15),(7,24),(20,21)])); scale=random.randint(1,3); p=[n*scale for n in p]
        elif family=='probability': total=random.randint(4,30); p=[random.randint(1,total-1),total]
        else: p=[random.randint(-20,40) for _ in range(5)]
        if tuple(p) in seen: continue
        seen.add(tuple(p))
        _,answer,_=compile_problem(family,p)
        result.append(Blueprint(kind=family,parameters=p,answer=str(answer)))
    return result


def compile_course(proposal, node, sources, build_id, diagnostic_parameters=()):
    from app.modules.learning.content_contracts import GeneratedContentSpec
    if not set(proposal.source_ids).issubset({s['source_id'] for s in sources}):
        raise ValueError('fabricated_citation')
    if any(len(x)>300 for x in proposal.objectives): raise ValueError('objective_bounds')
    tasks=[]; seen=set(tuple(x) for x in diagnostic_parameters)
    for index, blueprint in enumerate(proposal.tasks):
        if blueprint.kind!=node['family'] or tuple(blueprint.parameters) in seen:
            raise ValueError('topic_or_assessment_overlap')
        seen.add(tuple(blueprint.parameters))
        tasks.append(verified_task(blueprint, f'GEN-{build_id[:8]}-{index}', ['diagnostic','practice','practice','checkpoint','checkpoint','checkpoint','checkpoint'][index]))
    unit='dynamic.generated.'+build_id
    body={'unit_id':unit,'title':proposal.title,'grade':node['grade'],'domain':node['domain'],
        'knowledge_points':[node['title']],'prerequisites':[], 'tasks':tasks[:5],
        'sources':[{k:s[k] for k in ('source_id','version','locator','authorization')} for s in sources if s['source_id'] in proposal.source_ids],
        'activities':[{'unit_id':unit+'.'+kind,'title':proposal.title+' · '+('圆桌' if kind=='roundtable' else '我教 AI'),
            'task_id':tasks[1 if kind=='teach' else 2]['id'],'verification_task_id':tasks[5 if kind=='teach' else 6]['id'],'verification_task':tasks[5 if kind=='teach' else 6],'kind':kind} for kind in ('teach','roundtable')]}
    return GeneratedContentSpec.model_validate(body)

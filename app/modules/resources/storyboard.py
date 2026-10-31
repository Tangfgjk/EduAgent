"""Compile a rational practice example to a verified, progressive lesson.

Content comes from the current course's practice tasks, not held-out assessments.
Python syntax is parsed as data with a closed arithmetic grammar; never eval.
"""
import ast
import html
import re
from fractions import Fraction
from app.modules.errors import DomainError
from app.modules.learning.numeric_verifier import parse_number
from app.modules.resources.classroom import project


def value(node):
    if isinstance(node,ast.Constant) and type(node.value) in {int,float}:
        return Fraction(str(node.value))
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
        return (-1 if isinstance(node.op,ast.USub) else 1)*value(node.operand)
    if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
        a,b=value(node.left),value(node.right)
        if max(abs(a),abs(b))>1000000:raise ValueError('number_bounds')
        if isinstance(node.op,ast.Add):return a+b
        if isinstance(node.op,ast.Sub):return a-b
        if isinstance(node.op,ast.Mult):return a*b
        return a/b
    raise ValueError('unsupported_expression')


def trace(expression):
    tree=ast.parse(expression,mode='eval')
    if len(list(ast.walk(tree)))>60:raise ValueError('expression_bounds')
    expected=value(tree.body);steps=[(ast.unparse(tree.body),'先观察括号和运算层级，说明先算哪一步。')]
    # Use explicit bottom-up paths, each original operation is reduced once.
    def walk(node,parent=None,field=None):
        if not isinstance(node,ast.BinOp):return
        walk(node.left,node,'left');walk(node.right,node,'right')
        result=value(node)
        rule='完成乘除，再考虑外层运算。' if isinstance(node.op,(ast.Mult,ast.Div)) else '按当前层级完成加减，保留符号。'
        replacement=ast.parse(str(result),mode='eval').body
        if parent is None:tree.body=replacement
        else:setattr(parent,field,replacement)
        current=ast.unparse(tree.body)
        assert value(tree.body)==expected
        steps.append((current,rule))
    walk(tree.body)
    return expected,steps


def expression_for(task):
    if not task['stem'].strip().startswith('计算'):return None
    text=task['stem'].replace('×','*').replace('÷','/').replace('（','(').replace('）',')').replace('−','-')
    for candidate in sorted(re.findall(r'[\d().+*/\-\s]+',text),key=len,reverse=True):
        candidate=candidate.strip()
        if not candidate or len(candidate)>180:continue
        try:
            answer,steps=trace(candidate)
            if len(steps)>1 and answer==parse_number(task['answer']):return candidate,steps
        except (ValueError,SyntaxError,ZeroDivisionError,OverflowError):continue
    return None


def compile_lesson(build):
    tasks=build.candidate['spec']['tasks']
    choices=[(t,expression_for(t)) for t in tasks if t['stage']=='practice']
    choices=[(t,r) for t,r in choices if r]
    if not choices:raise DomainError(409,'storyboard_family_unsupported','当前课程尚无可验证的四则运算例题，请使用已有课堂')
    task,(expression,steps)=max(choices,key=lambda pair:len(pair[1][1]))
    def text(id,body,y,size=28,height=90):
        return {'id':id,'type':'text','left':64,'top':y,'width':1152,'height':height,'content':f'<p style="font-size:{size}px">{html.escape(body)}</p>'}
    def scene(id,title,elements,actions):
        return {'id':id,'title':title,'content':{'canvas':{'viewportSize':1280,'viewportRatio':.5625,'background':{'color':'#f8faf5'},'elements':[text('heading',title,44,40),*elements]}},'actions':actions}
    intro=scene('goal','观察：先算什么？',[text('goal','本课目标：根据运算层级说明每一步的依据。',160),text('problem',task['stem'],280,height=180)],
        [{'type':'speech','text':'先读例题，观察括号与运算符。先想一想，再查看逐步演示。'}])
    elements=[];actions=[]
    for i,(formula,reason) in enumerate(steps):
        eid='step'+str(i)
        elements.append({'id':eid,'type':'latex','left':90,'top':240,'width':1100,'height':140,'latex':formula.replace('*',r'\times ').replace('/',r'\div '),'fontSize':48})
        elements.append(text('reason'+str(i),reason,450,30,110))
        if i:actions.extend([{'type':'hide','elementId':'step'+str(i-1)},{'type':'hide','elementId':'reason'+str(i-1)}])
        actions.extend([{'type':'show','elementId':eid},{'type':'show','elementId':'reason'+str(i)},{'type':'speech','text':reason}])
    worked=scene('worked','推演：每次只改变一步',[text('method','观察表达式 → 按层级计算 → 保留等值关系',140,26),*elements,text('footer','先说明为什么，再继续下一步；最后独立核对。',610,24)],actions)
    question=next((t for t,_ in choices if t['id']!=task['id']),task)
    transfer=scene('check','轮到你：解释并核验',[text('prompt',question['stem'],180,height=180),text('notice','可以回看演示；本题是课堂练习，不作为独立检测。',500,24)],
        [{'type':'question','id':'practice-check','text':question['stem'],'answer':question['answer']}])
    document={'stage':{'name':build.candidate['spec']['title']+' · 分步课堂'},'scenes':[intro,worked,transfer]}
    manifest=project(document)
    if any(s['layout_issues'] for s in manifest['scenes']):raise DomainError(409,'storyboard_layout_failed','课堂排版检查未通过，请使用原课程练习')
    manifest['storyboard']={'version':'verified-rational.v1','practice_task_id':task['id'],'steps':len(steps),'math_verified':True,'teaching_effect_validated':False}
    return {'title':document['stage']['name'][:120],'cards':[{'text':task['stem']}], 'generator':'verified-rational-storyboard.v1',
        'classroom':manifest,'source_document':document,'practice_task_ids':[task['id'],question['id']]}

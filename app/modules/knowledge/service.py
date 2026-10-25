from hashlib import sha256
from typing import Literal
from pydantic import Field
from sqlalchemy import select
from app.modules.learning.contracts import Contract
from app.modules.errors import DomainError
from app.infrastructure.db.models import KnowledgeDocument, KnowledgeSourceState

# Self-authored development knowledge, not an official syllabus or teacher rubric.
NODES = {
    'MATH.G7.RATIONAL': dict(title='有理数运算', grade='七年级', domain='数与式', family='rational', aliases=['有理数', '负数', '正负数', '运算顺序', '数轴'], prerequisites=[], text='有理数四则运算应检查符号与运算顺序，先乘除后加减，有括号时先算括号。除数不能为零。步骤应说明依据，数值检查不能代替解释质量评价。'),
    'MATH.G7.EQUATION': dict(title='一元一次方程', grade='七年级', domain='方程', family='equation', aliases=['一元一次方程', '等式性质', '移项', '解方程'], prerequisites=['MATH.G7.RATIONAL'], text='等式两边做相同的加减运算，或同乘除一个非零数，仍保持相等。求得未知数后代回原方程检查。此处只自动核验单个有理数解。'),
    'MATH.G8.LINEAR': dict(title='一次函数求值', grade='八年级', domain='函数', family='linear', aliases=['一次函数', '函数求值', '正比例', '函数图像'], prerequisites=['MATH.G7.RATIONAL', 'MATH.G7.EQUATION'], text='一次函数写作 y=ax+b，a不为零。给定自变量后代入求函数值，检查括号和符号。图像和变化规律的解释须另行审核，数值求值不能证明已经理解图像。'),
    'MATH.G8.PYTHAGORAS': dict(title='勾股定理计算', grade='八年级', domain='几何', family='pythagoras', aliases=['勾股', '直角三角形', '斜边'], prerequisites=['MATH.G7.RATIONAL'], text='在直角三角形中，两条直角边平方之和等于斜边平方。应用前必须确认直角条件并识别斜边，边长为正。自动核验限给定直角边求有理数斜边，不自动核验证明。'),
    'MATH.G9.PROBABILITY': dict(title='等可能事件概率', grade='九年级', domain='统计概率', family='probability', aliases=['概率', '摸球', '等可能', '骰子', '基本结果'], prerequisites=['MATH.G7.RATIONAL'], text='只有在基本结果等可能的前提下，才能用符合事件的基本结果个数除以全部基本结果个数。球等可能不等于颜色等可能。计算概率之前说明计数单位与前提。'),
    'MATH.G7.STATISTICS': dict(title='平均数与频率', grade='七年级', domain='统计概率', family='statistics', aliases=['平均数', '中位数', '频率', '统计数据'], prerequisites=['MATH.G7.RATIONAL'], text='算术平均数为各数据的和除以数据个数。频率是本次实际发生次数与总实验次数的比，不能把一次实验的频率直接当作理论概率。自动生成先支持算术平均数数值任务。'),
}


class SourceIn(Contract):
    title: str = Field(min_length=2, max_length=120)
    text: str = Field(min_length=10, max_length=12000)
    knowledge_points: list[str] = Field(min_length=1, max_length=10)
    locator: str = Field(min_length=1, max_length=500)
    authorization: Literal['self_authored_development', 'licensed_local']
    workspace_id: str | None = None
    acknowledge_rights: Literal[True]
    conflicts_with: list[str] = Field(default_factory=list,max_length=5)


def resolve(topic):
    if any(word in topic for word in ('导数', '微积分', '高等数学', '量子', '英语', '编程', '化学')):
        return {'status': 'out_of_scope', 'nodes': [], 'message': '目前限定初中数学，请重新选择主题。'}
    matches = [key for key, node in NODES.items() if any(alias in topic for alias in node['aliases'])]
    if any(word in topic for word in ('数轴','相反数','绝对值','有理数大小')) and 'MATH.G7.RATIONAL' not in matches:
        matches.append('MATH.G7.RATIONAL')
    if ('函数' in topic and 'MATH.G8.LINEAR' not in matches) or any(word in topic for word in ('二次函数', '几何证明', '二元方程', '三角函数', '中位数', '众数')):
        return {'status':'unsupported','nodes':matches,'message':'该主题目前尚未支持完整课程。可先选择有理数、一次方程、一次函数求值、勾股计算、概率或平均数。'}
    if len(matches)>1 and any(word in topic for word in ('和','与','、','以及')):
        return {'status':'needs_clarification','nodes':matches,'message':'识别到多个学习单元，请先确认本次优先学习哪一个。'}
    # Prerequisite mentions must not turn one focused topic into a false ambiguity.
    specific = [key for key in matches if not any(key in NODES[other]['prerequisites'] for other in matches if other != key)]
    if not specific:
        return {'status':'unsupported','nodes':[],'message':'该主题目前尚未支持；不是你描述不清。请先从已支持单元选择。','supported_topics':[n['title'] for n in NODES.values()]}
    if len(specific) != 1:
        return {'status': 'needs_clarification', 'nodes': matches, 'message': '请聚焦一个单元，并明确希望练习计算、说明依据还是证明；未知知识点不会自动冒充已支持。'}
    key = specific[0]
    return {'status': 'candidate', 'nodes': [key], 'message': '已匹配开发知识点；自动核验仅覆盖列出的数值题型，主题中的证明/图像理解部分仍需专业复核。'}


def source_enabled(session, identifier):
    state = session.get(KnowledgeSourceState, identifier)
    return state is None or state.enabled


def retrieve(session, key, wid):
    node = NODES[key]
    builtin = {'source_id': 'builtin.'+key, 'title': node['title']+' · 自编开发依据',
        'text': node['text'], 'locator': 'EduAgent/self-authored-knowledge.v1/'+key,
        'authorization': 'self_authored_development', 'knowledge_points': [key]}
    builtin['version'] = sha256(node['text'].encode()).hexdigest()
    sources = [builtin] if source_enabled(session, builtin['source_id']) else []
    for row in session.scalars(select(KnowledgeDocument).where((KnowledgeDocument.workspace_id.is_(None)) | (KnowledgeDocument.workspace_id == wid)).order_by(KnowledgeDocument.id)):
        if key in row.body['knowledge_points'] and source_enabled(session, row.id):
            sources.append({'source_id': row.id, 'version': row.version, **row.body})
    import re
    unique=[];hashes=set()
    for source in sources:
        digest=sha256(re.sub(r'\s+','',source['text']).encode()).hexdigest()
        if digest not in hashes:
            hashes.add(digest);unique.append(source)
    return unique[:5]


def check_snapshot(session, sources):
    ids={s['source_id'] for s in sources}
    if any(set(s.get('conflicts_with',[])).intersection(ids) for s in sources):
        raise DomainError(409,'source_conflict','来源已标记存在冲突，须先人工核对，不能继续自动生成')
    for source in sources:
        if not source_enabled(session, source['source_id']):
            raise DomainError(409, 'source_revoked', '课程使用的来源已撤回，请重新检索并生成新版本')
        if source['source_id'].startswith('builtin.'):
            from app.modules.generation.rational_agent import SOURCE_ID, source as rational_source
            key = source['source_id'][8:]
            current = rational_source()['version'] if source['source_id']==SOURCE_ID else sha256(NODES[key]['text'].encode()).hexdigest() if key in NODES else None
        else:
            row = session.get(KnowledgeDocument, source['source_id'])
            current = row.version if row else None
        if current != source['version']:
            raise DomainError(409, 'source_version_conflict', '来源版本不一致，请重新生成')


def public_source(source):
    return {key: source[key] for key in ('source_id', 'version', 'title', 'locator', 'authorization')} | {'excerpt': source['text'][:800], 'professional_review': False}

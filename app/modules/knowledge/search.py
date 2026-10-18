"""One bounded Tavily Basic request; discovery is not a licensed course source."""
import os
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from sqlalchemy import select, func
from app.infrastructure.db.models import SearchOperation, ModelDailyBudget, GenerationPolicy, now
from app.infrastructure.runtime.model_tasks import day_key
from app.modules.errors import DomainError
from app.modules.knowledge.service import NODES

DOMAINS=('moe.gov.cn','pep.com.cn','zxx.edu.cn')


def key():
    value=os.getenv('WENJIN_TAVILY_KEY','')
    path=Path(os.getenv('WENJIN_TAVILY_KEY_FILE','.local/tavily.key'))
    return value or (path.read_text(encoding='utf-8').strip() if path.is_file() else '')


def allowed_url(value):
    try:
        url=urlsplit(value)
        return url.scheme=='https' and not url.username and not url.password and url.port in (None,443) and any(url.hostname==d or (url.hostname or '').endswith('.'+d) for d in DOMAINS)
    except (TypeError,ValueError): return False


def reserve(session,row,identifier):
    prior=session.get(SearchOperation,identifier)
    if prior:
        if prior.workspace_id!=row.workspace_id or prior.build_id!=row.id: raise DomainError(409,'idempotency_conflict','搜索标识已被使用')
        return prior
    policy=session.get(GenerationPolicy,1,with_for_update=True)
    if not policy.search or not key(): raise DomainError(409,'search_unconfigured','请先配置 Tavily 本机密钥并由管理员开放搜索')
    if row.intent['status']!='candidate': raise DomainError(409,'clarification_required','请先明确可识别的初中数学主题')
    count=session.scalar(select(func.count()).select_from(SearchOperation).where(SearchOperation.build_id==row.id))
    if count>=2: raise DomainError(429,'search_budget_exhausted','每主题最多两次搜索，失败不返还次数')
    day=day_key();budget=session.get(ModelDailyBudget,(day,'search:global'),with_for_update=True)
    if budget is None: budget=ModelDailyBudget(day=day,scope='search:global',calls=0);session.add(budget)
    if budget.calls>=20: raise DomainError(429,'search_budget_exhausted','今日搜索预约已达20次')
    budget.calls+=1
    op=SearchOperation(id=identifier,workspace_id=row.workspace_id,build_id=row.id,query='初中数学 '+NODES[row.intent['nodes'][0]]['title'],status='pending')
    session.add(op);session.flush();return op


class SearchRunner:
    def __init__(self,db,transport=None): self.db,self.transport=db,transport

    def step(self):
        from app.infrastructure.db.models import Workspace,CourseBuild
        with self.db.transaction() as session:
            for stale in session.scalars(select(SearchOperation).where(SearchOperation.status=='running',SearchOperation.result['started_at'].as_integer()<now()-60).with_for_update(skip_locked=True)):
                stale.status='unknown';stale.result=None
            candidate=session.scalar(select(SearchOperation).where(SearchOperation.status=='pending').order_by(SearchOperation.created_at).with_for_update(skip_locked=True).limit(1))
            if not candidate: return False
            if session.get(Workspace,candidate.workspace_id).status!='active' or not session.get(GenerationPolicy,1).search:
                candidate.status='discarded';return True
            candidate.status='running';candidate.result={'started_at':now()};identifier=candidate.id;query=candidate.query
        result=None;credits=None;status='unknown'
        try:
            with httpx.Client(timeout=20,follow_redirects=False,trust_env=False,transport=self.transport) as client:
                with client.stream('POST','https://api.tavily.com/search',headers={'Authorization':'Bearer '+key()},json={
                    'query':query,'search_depth':'basic','max_results':5,'include_domains':list(DOMAINS),'include_answer':False,
                    'include_raw_content':False,'include_images':False,'auto_parameters':False,'include_usage':True}) as response:
                    response.raise_for_status();raw=b''
                    for chunk in response.iter_bytes():
                        raw+=chunk
                        if len(raw)>256000: raise ValueError('response_too_large')
            import json
            value=json.loads(raw)
            if not isinstance(value,dict) or not isinstance(value.get('results'),list): raise ValueError('invalid_search_response')
            result={'results':[{'title':str(item.get('title',''))[:200],'url':item['url'],'excerpt':str(item.get('content',''))[:800],
                'authorization':'discovery_only','usable_for_generation':False} for item in value.get('results',[])[:5] if isinstance(item,dict) and allowed_url(item.get('url'))],
                'notice':'检索发现不代表版权授权或内容核验；须先核对授权并登记资料，才能用于课程生成。'}
            usage=value.get('usage');credits=usage.get('credits') if isinstance(usage,dict) else None;status='succeeded'
        except (httpx.HTTPError,ValueError,KeyError,TypeError): pass
        with self.db.transaction() as session:
            op=session.get(SearchOperation,identifier,with_for_update=True)
            if op.status=='running':
                active=session.get(Workspace,op.workspace_id).status=='active' and session.get(GenerationPolicy,1).search
                op.status,op.result,op.credits=(status if active else 'discarded'),(result if active else None),credits if isinstance(credits,int) else None
        return True

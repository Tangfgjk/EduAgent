"""Server-only OpenAI-compatible budget gate for the isolated resource service.

OpenMAIC gets only this gate's opaque credential, never the DeepSeek key. Every
retry is a new charged reservation; unknown calls retain their full reservation.
"""
import hashlib
import json
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from app.infrastructure.db.models import ResourceBudget,ResourceModelCall,ModelConfiguration,ModelPolicy,now
from app.infrastructure.runtime.model_tasks import model_key
from app.modules.learning.content_repository import content_hash
from app.modules.errors import DomainError
import httpx


def reserve(db,token,body):
    if not token: raise DomainError(403,'resource_proxy_forbidden','资源代理凭据缺失')
    if not isinstance(body,dict) or not isinstance(body.get('messages'),list) or not 1<=len(body['messages'])<=40:
        raise DomainError(422,'invalid_resource_request','资源请求结构不支持')
    messages=[]
    for message in body['messages']:
        if not isinstance(message,dict) or message.get('role') not in {'system','user','assistant'}:
            raise DomainError(422,'unsupported_resource_capability','资源仅接受文本消息')
        content=message.get('content')
        if isinstance(content,list) and 1<=len(content)<=100 and all(isinstance(part,dict) and part.get('type')=='text' and isinstance(part.get('text'),str) for part in content):
            content='\n'.join(part['text'] for part in content)
        if not isinstance(content,str):raise DomainError(422,'unsupported_resource_capability','资源不接受图片、工具或执行请求')
        messages.append({'role':message['role'],'content':content})
    if body.get('tools') or any(message.get('tool_calls') for message in body['messages']):
        raise DomainError(422,'unsupported_resource_capability','首期资源服务仅支持文本，不接受图片、工具或执行请求')
    limit=body.get('max_tokens',body.get('max_completion_tokens',4096))
    if type(limit) is not int or limit<=0: raise DomainError(422,'invalid_resource_request','输出预算无效')
    limit=min(limit,4096)
    # UTF-8 byte count is a conservative upper bound, not a currency estimate.
    input_bound=len(json.dumps(messages,ensure_ascii=False).encode())
    with db.transaction() as session:
        for stale in session.scalars(select(ResourceModelCall).where(ResourceModelCall.status=='running',ResourceModelCall.created_at<now()-120).with_for_update(skip_locked=True)):
            stale.status='unknown'
        row=session.scalar(select(ResourceBudget).where(ResourceBudget.secret_hash==hashlib.sha256(token.encode()).hexdigest()).with_for_update())
        if not row or not row.enabled: raise DomainError(403,'resource_proxy_paused','资源预算未开放')
        policy=session.get(ModelPolicy,1)
        config=session.get(ModelConfiguration,policy.active_id) if policy and policy.active_id else None
        if not config or config.vendor!='deepseek' or not model_key(): raise DomainError(409,'resource_model_unconfigured','需要已发布 DeepSeek 配置及本机密钥')
        if row.calls>=20 or row.input_reserved+input_bound>128000 or row.output_reserved+limit>32768:
            raise DomainError(429,'resource_budget_exhausted','资源整次调用或 token 预算已用尽')
        row.calls+=1;row.input_reserved+=input_bound;row.output_reserved+=limit
        call=ResourceModelCall(budget_id=row.id,request_hash=content_hash(body),status='running',input_reserved=input_bound,output_reserved=limit)
        session.add(call);session.flush()
        outbound={'model':config.model,'messages':messages,'max_tokens':limit,'temperature':0.2,'thinking':{'type':'disabled'},'stream':False}
        if isinstance(body.get('response_format'),dict) and body['response_format'].get('type') in {'json_object','json_schema'}:
            outbound['response_format']={'type':'json_object'}
        return call.id,outbound


def settle(db,identifier,payload):
    with db.transaction() as session:
        call=session.get(ResourceModelCall,identifier,with_for_update=True)
        row=session.get(ResourceBudget,call.budget_id,with_for_update=True)
        if call.status!='running': return False
        usage=payload.get('usage') if isinstance(payload,dict) else {}
        if not isinstance(usage,dict): usage={}
        incoming,outgoing=usage.get('prompt_tokens'),usage.get('completion_tokens')
        valid=type(incoming) is int and type(outgoing) is int and 0<=incoming<=call.input_reserved and 0<=outgoing<=call.output_reserved
        call.usage={'input_tokens':incoming if type(incoming) is int else None,'output_tokens':outgoing if type(outgoing) is int else None}
        call.status='succeeded' if payload and valid else 'unknown'
        if valid:
            row.input_reserved-=call.input_reserved-incoming;row.output_reserved-=call.output_reserved-outgoing
        if type(incoming) is int and incoming>call.input_reserved:
            row.input_reserved+=incoming-call.input_reserved;row.enabled=False
        if type(outgoing) is int and outgoing>call.output_reserved:
            row.output_reserved+=outgoing-call.output_reserved;row.enabled=False
        # A stopped budget never returns a late response to OpenMAIC.
        return row.enabled


def proxy_router(db,transport=None):
    router=APIRouter(prefix='/internal/openmaic/v1',include_in_schema=False)

    @router.post('/chat/completions')
    async def completion(request:Request):
        raw=b''
        async for chunk in request.stream():
            raw+=chunk
            if len(raw)>200000: raise DomainError(422,'resource_request_too_large','资源请求过大')
        try: body=json.loads(raw)
        except ValueError: raise DomainError(422,'invalid_resource_request','资源请求无效') from None
        identifier,outbound=reserve(db,request.headers.get('authorization','').removeprefix('Bearer '),body)
        result=None
        try:
            async with httpx.AsyncClient(timeout=60,follow_redirects=False,trust_env=False,transport=transport or getattr(request.app.state,'openmaic_proxy_transport',None)) as client:
                async with client.stream('POST','https://api.deepseek.com/chat/completions',headers={'Authorization':'Bearer '+model_key()},json=outbound) as response:
                    response.raise_for_status();received=b''
                    async for chunk in response.aiter_bytes():
                        received+=chunk
                        if len(received)>2000000: raise ValueError('resource_response_too_large')
            result=json.loads(received)
            if not isinstance(result,dict) or not isinstance(result.get('choices'),list) or not result['choices'] or not isinstance(result['choices'][0],dict) or not isinstance(result['choices'][0].get('message'),dict) or not isinstance(result['choices'][0]['message'].get('content'),str): raise ValueError('invalid_response')
        except (httpx.HTTPError,ValueError,TypeError,KeyError):
            settle(db,identifier,None)
            raise DomainError(503,'resource_upstream_unknown','资源调用没有确定结果；预算保留，不自动重发') from None
        if not settle(db,identifier,result): raise DomainError(409,'resource_result_discarded','资源预算已停用，迟到结果不生效')
        if body.get('stream') is True:
            # Budgeted upstream request stays non-streaming. Emit compatible SSE
            # only after settlement and cancellation fencing, without another call.
            choice=result['choices'][0]
            base={'id':result.get('id',identifier),'object':'chat.completion.chunk','created':result.get('created',now()),'model':result.get('model',outbound['model'])}
            chunks=[{**base,'choices':[{'index':0,'delta':{'role':'assistant','content':choice['message']['content']},'finish_reason':None}]},
                {**base,'choices':[{'index':0,'delta':{},'finish_reason':choice.get('finish_reason','stop')}],'usage':result.get('usage')}]
            return Response(''.join('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n',media_type='text/event-stream',headers={'Cache-Control':'no-store'})
        return JSONResponse(result)

    return router

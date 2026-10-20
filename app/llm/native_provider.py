"""Native text protocols; no tools, media, streaming, redirects or retries."""
import asyncio
import json
import re
from time import perf_counter
import httpx
from app.llm.provider import ProviderConfig,ProviderError,GenerationRequest,GenerationResult,Usage


class NativeProvider:
    def __init__(self,config:ProviderConfig,transport=None):
        self.config,self.transport=config,transport
        hosts={'anthropic':'https://api.anthropic.com','gemini':'https://generativelanguage.googleapis.com'}
        if config.vendor not in hosts or config.base_url!=hosts[config.vendor]:raise ProviderError('invalid_native_endpoint')
        if not config.api_key.get_secret_value():raise ProviderError('missing_api_key')
        if config.vendor=='gemini' and not re.fullmatch(r'[a-zA-Z0-9_-]+',config.model):raise ProviderError('invalid_native_model')
    async def generate(self,request:GenerationRequest):
        config=self.config
        if request.max_output_tokens>config.max_output_tokens:raise ProviderError('output_budget_exceeded')
        system='\n'.join(m.content for m in request.messages if m.role=='system')
        messages=[m for m in request.messages if m.role!='system']
        if not messages:raise ProviderError('missing_user_input')
        if config.vendor=='anthropic':
            path='/v1/messages';headers={'x-api-key':config.api_key.get_secret_value(),'anthropic-version':'2023-06-01'}
            payload={'model':config.model,'system':system,'messages':[m.model_dump() for m in messages],
                'max_tokens':request.max_output_tokens,'temperature':request.temperature}
        else:
            path='/v1beta/models/'+config.model+':generateContent';headers={'x-goog-api-key':config.api_key.get_secret_value()}
            payload={'systemInstruction':{'parts':[{'text':system}]},'contents':[{'role':'model' if m.role=='assistant' else 'user','parts':[{'text':m.content}]} for m in messages],
                'generationConfig':{'maxOutputTokens':request.max_output_tokens,'temperature':request.temperature}}
            if request.response_format=='json_object':payload['generationConfig']['responseMimeType']='application/json'
        started=perf_counter()
        try:
            async with asyncio.timeout(config.timeout_seconds):
                async with httpx.AsyncClient(transport=self.transport,timeout=config.timeout_seconds,trust_env=False,follow_redirects=False) as client:
                    async with client.stream('POST',config.base_url+path,headers=headers,json=payload) as response:
                        if response.status_code in {401,403}:raise ProviderError('authentication_failed')
                        if response.status_code==429:raise ProviderError('rate_limited')
                        if response.status_code>=500:raise ProviderError('upstream_unavailable')
                        if response.status_code!=200:raise ProviderError('upstream_rejected')
                        raw=bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw)>1_000_000:raise ProviderError('response_too_large')
            data=json.loads(raw)
            if config.vendor=='anthropic':
                blocks=data['content']
                if any(b.get('type')!='text' for b in blocks):raise ProviderError('unsupported_response_block')
                text='\n'.join(b['text'] for b in blocks);stop=data['stop_reason']
                if stop not in {'end_turn','stop_sequence','max_tokens'}:raise ProviderError('unsupported_finish_reason')
                usage=Usage(input_tokens=data.get('usage',{}).get('input_tokens'),output_tokens=data.get('usage',{}).get('output_tokens'))
                finish='length' if stop=='max_tokens' else 'stop'
            else:
                candidate=data['candidates'][0];parts=candidate['content']['parts']
                if any(set(p)-{'text','thought','thoughtSignature'} for p in parts):raise ProviderError('unsupported_response_block')
                text='\n'.join(p['text'] for p in parts if 'text' in p and not p.get('thought'))
                stop=candidate['finishReason']
                if stop not in {'STOP','MAX_TOKENS'}:raise ProviderError('unsupported_finish_reason')
                usage=Usage(input_tokens=data.get('usageMetadata',{}).get('promptTokenCount'),output_tokens=data.get('usageMetadata',{}).get('candidatesTokenCount'))
                finish='length' if stop=='MAX_TOKENS' else 'stop'
            if not text.strip():raise ProviderError('empty_response')
            return GenerationResult(provider=config.vendor,requested_model=config.model,returned_model=data.get('model') or data.get('modelVersion'),
                text=text,finish_reason=finish,usage=usage,elapsed_ms=int((perf_counter()-started)*1000))
        except (httpx.TimeoutException,TimeoutError):raise ProviderError('timeout') from None
        except httpx.HTTPError:raise ProviderError('transport_error') from None
        except (ValueError,KeyError,IndexError,TypeError):raise ProviderError('invalid_response') from None

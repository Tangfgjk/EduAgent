import json
import httpx
import pytest
from app.llm.provider import ProviderConfig,GenerationRequest,Message,ProviderError
from app.llm.native_provider import NativeProvider


@pytest.mark.asyncio
@pytest.mark.parametrize('vendor',['anthropic','gemini'])
async def test_native_text_protocol_and_usage_are_normalized_without_tools(vendor):
    endpoint='https://api.anthropic.com' if vendor=='anthropic' else 'https://generativelanguage.googleapis.com'
    captured=[]
    def respond(request):
        captured.append(request)
        body=json.loads(request.content)
        assert 'test-placeholder' not in str(request.url)
        if vendor=='anthropic':
            assert request.headers['x-api-key']=='test-placeholder'
            assert request.headers['anthropic-version']=='2023-06-01'
            assert body['system']=='系统开发说明'
            return httpx.Response(200,json={'content':[{'type':'text','text':'已读取开发文本'}],
                'stop_reason':'end_turn','usage':{'input_tokens':12,'output_tokens':7}})
        assert request.headers['x-goog-api-key']=='test-placeholder'
        assert body['contents'][0]['role']=='user'
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'已读取开发文本'}]},'finishReason':'STOP'}],
            'usageMetadata':{'promptTokenCount':12,'candidatesTokenCount':7}})
    provider=NativeProvider(ProviderConfig(vendor=vendor,base_url=endpoint,model='model-dev',api_key='test-placeholder'),httpx.MockTransport(respond))
    result=await provider.generate(GenerationRequest(messages=(Message(role='system',content='系统开发说明'),Message(role='user',content='自编数学文本'))))
    assert result.text=='已读取开发文本' and result.usage.input_tokens==12 and result.usage.output_tokens==7
    assert result.finish_reason=='stop' and len(captured)==1


@pytest.mark.asyncio
async def test_native_tool_block_is_rejected_and_is_never_executed():
    def respond(request):return httpx.Response(200,json={'content':[{'type':'tool_use','name':'shell','input':{'command':'bad'}}],'stop_reason':'tool_use'})
    provider=NativeProvider(ProviderConfig(vendor='anthropic',base_url='https://api.anthropic.com',model='dev',api_key='test-placeholder'),httpx.MockTransport(respond))
    with pytest.raises(ProviderError,match='unsupported_response_block'):
        await provider.generate(GenerationRequest(messages=(Message(role='user',content='开发文本'),)))

import httpx
import pytest
from app.modules.resources.media import api_host, BailianMedia


@pytest.mark.parametrize('value', [
    'http://dashscope.aliyuncs.com', 'https://evil.example',
    'https://ws-demo.cn-beijing.maas.aliyuncs.com.evil.example',
    'https://ws-demo.cn-singapore.maas.aliyuncs.com',
    'https://user:pass@dashscope.aliyuncs.com',
    'https://dashscope.aliyuncs.com/api/v1',
    'https://dashscope.aliyuncs.com?redirect=evil',
])
def test_reject_unapproved_media_endpoint(monkeypatch, value):
    monkeypatch.setenv('WENJIN_BAILIAN_API_HOST', value)
    with pytest.raises(ValueError):
        api_host()


def test_workspace_host_used_without_duplicate_api_path(monkeypatch):
    monkeypatch.setenv('WENJIN_BAILIAN_API_HOST', 'https://ws-demo.cn-beijing.maas.aliyuncs.com/')
    def respond(request):
        assert str(request.url) == 'https://ws-demo.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
        assert request.headers['authorization'] == 'Bearer test-only'
        return httpx.Response(200, json={'output': {'audio': {'url': 'https://fixtures.oss-cn-beijing.aliyuncs.com/audio.wav'}}})
    port = BailianMedia('test-only', httpx.MockTransport(respond))
    try:
        assert port.submit({'kind': 'audio', 'model': 'qwen3-tts-flash', 'prompt': 'test'})['url'].endswith('audio.wav')
    finally:
        port.close()

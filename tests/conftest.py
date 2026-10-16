"""回归默认禁止真实 HTTP 出网；TestClient 的内存传输不受影响。"""
import httpx
import pytest


@pytest.fixture(autouse=True)
def block_external_http(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("测试禁止真实 HTTP 出网，请注入 FakeLLM 或 MockTransport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)

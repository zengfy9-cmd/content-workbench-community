import time

import pytest

from content_workbench import scys_reference as scys
from content_workbench.references import ReferenceError


class Store:
    def __init__(self): self.values = {}
    def get(self, key): return self.values.get(key)
    def set(self, key, value): self.values[key] = value


def test_oauth_pkce_state_single_use_and_no_browser_token(monkeypatch):
    store = Store()
    calls = []
    class Response:
        status_code = 200
        def __init__(self, data): self.data = data
        def json(self): return self.data
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response({'client_id': 'test-client'} if url.endswith('/register') else {'access_token': 'private-token', 'expires_in': 3600})
    monkeypatch.setattr(scys.httpx, 'post', post)
    link = scys.begin(store, 'http://localhost:8765')
    assert 'code_challenge_method=S256' in link and 'private-token' not in link
    assert len(calls) == 1
    with pytest.raises(ReferenceError): scys.finish(store, 'wrong', 'code')
    assert len(calls) == 1
    state = store.get('scys_reference_pending')['state']
    scys.finish(store, state, 'code')
    assert store.get('scys_reference_token')['access_token'] == 'private-token'
    assert calls[1][1]['data']['code_verifier']
    with pytest.raises(ReferenceError): scys.finish(store, state, 'code')
    assert len(calls) == 2


def test_scys_missing_permission_never_fetches_public_page():
    with pytest.raises(ReferenceError, match='连接生财 MCP'):
        scys.read(Store(), 'https://scys.com/articleDetail/xq_topic/123')
    with pytest.raises(ReferenceError, match='详情链接'):
        scys.read(Store(), 'https://evil.example/articleDetail/xq_topic/123')


def test_scys_mcp_only_calls_topic_detail(monkeypatch):
    store = Store()
    store.set('scys_reference_token', {'access_token': 'private-token', 'expires_at': time.time()+3600})
    calls = []
    class Client:
        def __init__(self, **kwargs): self.headers = kwargs['headers']
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, json):
            calls.append(json)
            assert url == scys.RESOURCE
            result = {'protocolVersion': '2024-11-05'}
            if json['method'] == 'tools/call':
                assert json['params'] == {'name': 'topicDetail', 'arguments': {'entityType':'xq_topic','entityId':'123'}}
                result = {'structuredContent': {'topicUserDTO': {'name': '真实生财作者'}, 'topicDTO': {'showTitle': '生财原文', 'articleContent': '<p>简短正文</p>', 'articleContentContainFeishuDoc': '<p>'+'原文内容'*30+'</p><img src="https://example.org/a.png">'}}}
            class Response:
                status_code = 200
                headers = {'content-type': 'application/json'}
                def json(self): return {'result': result}
            return Response()
    monkeypatch.setattr(scys.httpx, 'Client', Client)
    result = scys.read(store, 'https://scys.com/articleDetail/xq_topic/123')
    assert result['title'] == '生财原文' and 'a.png' in result['body']
    assert result['author'] == '真实生财作者' and '原文内容' in result['body']
    assert [c['method'] for c in calls] == ['initialize','notifications/initialized','tools/call']

"""Explicit owner OAuth and read-only topicDetail access to Shengcai MCP."""
import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx

from .references import ReferenceError, extract_article

RESOURCE = 'https://mcp.scys.com/shengcai-web/mcp'
OAUTH = 'https://mcp.scys.com/mcp-oauth'


def _json(response):
    if response.status_code >= 400:
        raise ReferenceError('生财连接失败或授权已过期，请重新连接生财 MCP')
    try:
        return response.json()
    except ValueError:
        raise ReferenceError('生财服务返回格式异常，请稍后重试') from None


def begin(store, origin):
    redirect = origin + '/scys/callback'
    client = store.get('scys_reference_client')
    if not client or client.get('redirect') != redirect:
        data = _json(httpx.post(OAUTH + '/register', json={
            'client_name': '内容工作台 · 对标原文', 'redirect_uris': [redirect],
            'grant_types': ['authorization_code', 'refresh_token'],
            'response_types': ['code'], 'token_endpoint_auth_method': 'none',
        }, timeout=20))
        client = {'id': data['client_id'], 'redirect': redirect}
        store.set('scys_reference_client', client)
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    store.set('scys_reference_pending', {'verifier': verifier, 'state': state, 'expires': time.time() + 600})
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return OAUTH + '/authorize?' + urlencode({
        'client_id': client['id'], 'redirect_uri': redirect, 'response_type': 'code',
        'scope': 'mcp', 'resource': RESOURCE, 'state': state,
        'code_challenge': challenge, 'code_challenge_method': 'S256',
    })


def finish(store, state, code):
    pending = store.get('scys_reference_pending') or {}
    if not state or not secrets.compare_digest(state, pending.get('state', '')) or pending.get('expires', 0) < time.time():
        raise ReferenceError('授权请求已过期，请回工作台重新连接')
    store.set('scys_reference_pending', None)
    client = store.get('scys_reference_client') or {}
    token = _json(httpx.post(OAUTH + '/token', data={
        'grant_type': 'authorization_code', 'code': code, 'client_id': client['id'],
        'redirect_uri': client['redirect'], 'code_verifier': pending['verifier'], 'resource': RESOURCE,
    }, timeout=20))
    _save_token(store, token)


def _save_token(store, token):
    if not token.get('access_token'):
        raise ReferenceError('生财授权未完成，请重新连接')
    token['expires_at'] = time.time() + int(token.get('expires_in', 3600))
    store.set('scys_reference_token', token)


def read(store, url):
    parts = urlsplit(url)
    match = re.fullmatch(r'/articleDetail/(xq_topic|forum_topic)/(\d+)/?', parts.path)
    if parts.hostname not in {'scys.com', 'www.scys.com'} or not match:
        raise ReferenceError('请使用生财文章详情链接，例如 scys.com/articleDetail/xq_topic/…')
    token = store.get('scys_reference_token') or {}
    if not token.get('access_token'):
        raise ReferenceError('请先点击“连接生财 MCP”，完成授权后再次读取这篇原文')
    if token.get('expires_at', 0) < time.time() + 30:
        if not token.get('refresh_token'):
            raise ReferenceError('生财授权已过期，请重新连接')
        refreshed = _json(httpx.post(OAUTH + '/token', data={
            'grant_type': 'refresh_token', 'refresh_token': token['refresh_token'],
            'client_id': (store.get('scys_reference_client') or {})['id'], 'resource': RESOURCE,
        }, timeout=20))
        refreshed.setdefault('refresh_token', token['refresh_token'])
        _save_token(store, refreshed)
        token = refreshed
    with httpx.Client(timeout=45, follow_redirects=False, headers={
        'Authorization': 'Bearer ' + token['access_token'],
        'Accept': 'application/json, text/event-stream',
    }) as client:
        def rpc(method, params, rid):
            r = client.post(RESOURCE, json={'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
            if r.status_code >= 400:
                raise ReferenceError('生财 MCP 读取失败，请检查授权后重试')
            if r.headers.get('mcp-session-id'):
                client.headers['Mcp-Session-Id'] = r.headers['mcp-session-id']
            if 'text/event-stream' in r.headers.get('content-type', ''):
                messages = [json.loads(line[5:].strip()) for line in r.text.splitlines() if line.startswith('data:')]
                result = next((m for m in messages if m.get('id') == rid), {})
            else:
                result = r.json()
            if result.get('error'):
                raise ReferenceError('生财 MCP 暂时无法读取，请稍后重试')
            return result.get('result', {})
        init = rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'content-workbench-references', 'version': '1.0'}}, 1)
        client.headers['MCP-Protocol-Version'] = init.get('protocolVersion', '2024-11-05')
        client.post(RESOURCE, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        result = rpc('tools/call', {'name': 'topicDetail', 'arguments': {'entityType': match[1], 'entityId': match[2]}}, 2)
    if result.get('isError'):
        raise ReferenceError('生财 MCP 拒绝读取或请求受限，请稍后重试；不会改用公开网页冒充原文')
    data = result.get('structuredContent')
    if not data:
        data = json.loads(next((c['text'] for c in result.get('content', []) if c.get('type') == 'text'), '{}'))
    topic = data.get('topicDTO') or {}
    body = topic.get('articleContentContainFeishuDoc') or topic.get('articleContent')
    if not body:
        raise ReferenceError('生财 MCP 没有返回正文，请检查此文章的访问权限')
    article = extract_article('<article>' + body + '</article>', url)
    article['title'] = str(topic.get('showTitle') or article['title'])[:300]
    article['author'] = str((data.get('topicUserDTO') or {}).get('name') or '')[:120]
    return article

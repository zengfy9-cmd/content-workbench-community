from fastapi.testclient import TestClient

from content_workbench.app import create_app
from content_workbench.config import Settings

ORIGIN = 'http://localhost:8765'


def local_client(address='127.0.0.1', **settings):
    return TestClient(create_app(Settings(login_mode='local', **settings), start_jobs=False),
                      base_url=ORIGIN, client=(address, 50000))


def test_local_mode_enters_without_credentials_but_keeps_origin_guard():
    c = local_client()
    assert c.get('/api/auth/state').json() == dict(registered=True, logged_in=True, login_mode='local')
    assert c.get('/api/materials').status_code == 200
    assert c.get('/api/settings').json()['login_mode'] == 'local'
    assert c.post('/api/drafts', json={'platform': 'wechat', 'title': '本机测试'}).status_code == 403
    assert c.post('/api/drafts', json={'platform': 'wechat', 'title': '本机测试'}, headers={'Origin': ORIGIN}).status_code == 200


def test_local_mode_rejects_remote_tunnels_and_cross_site_requests():
    c = local_client()
    assert local_client('192.168.1.2').get('/api/materials').status_code == 403
    for headers in ({'Forwarded': 'for=127.0.0.1'}, {'X-Forwarded-For': '127.0.0.1'},
                    {'CF-Connecting-IP': '127.0.0.1'}, {'Sec-Fetch-Site': 'cross-site'},
                    {'Origin': 'https://other.example'}):
        assert c.get('/api/materials', headers=headers).status_code == 403
    assert c.get('/api/materials', headers={'Host': 'other.example'}).status_code == 421


def test_local_mode_does_not_bypass_access_configuration():
    assert not Settings(login_mode='local', access_team='example').local_login
    assert not Settings(login_mode='passkey').local_login

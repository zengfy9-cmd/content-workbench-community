import io
import json
import uuid
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
from content_workbench import auth,db,llm,model_api,password_auth,cloud_backup
from content_workbench.app import create_app
from content_workbench.config import Settings,SecretStore,ensure_setup_code
from test_article_images import png

URL='https://content.example.test'
H={'Origin':URL}


def cloud():
    s=Settings(storage_mode='cloud',login_mode='password',origins=[URL],allowed_hosts=['content.example.test'],
               rp_id='content.example.test',cloud_url=URL,llm_backend='deepseek-api',writing_model='claude-opus-5-5',model_connector_path='builtin:daitu')
    s.save();return TestClient(create_app(s,start_jobs=False),base_url=URL)


def owner(c):
    response=c.post('/api/auth/password/register',json={'setup_code':ensure_setup_code(),'username':'owner.test','password':'a long test password 2026'},headers=H)
    assert response.status_code==200,response.text


def test_private_cloud_second_device_login_persists_text_images_and_layout():
    first=cloud()
    assert first.get('/api/drafts').status_code==401
    assert first.post('/api/auth/password/register',json={'setup_code':'wrong','username':'owner.test','password':'a long test password 2026'},headers=H).status_code==403
    owner(first)
    cookie=first.cookies.get(auth.COOKIE);assert cookie
    assert 'Secure' in first.post('/api/auth/password/login',json={'username':'owner.test','password':'a long test password 2026'},headers=H).headers['set-cookie']
    did=first.post('/api/drafts',json={'title':'跨电脑测试','platform':'wechat','body':'原来的正文。'},headers=H).json()['id']
    image=first.post(f'/api/drafts/{did}/images',files={'files':('image.png',png(),'image/png')},headers=H).json()[0]
    body='第一段。\n\n![示意图](/api/images/'+str(image['id'])+')\n\n最后一段。'
    first.put(f'/api/drafts/{did}',json={'body':body,'cover_wechat':image['id'],'wechat_theme':'green-white-clean'},headers=H).raise_for_status()
    first.close()
    # New app instance and new cookie jar: no old computer/browser session.
    second=cloud()
    assert second.get('/api/auth/state').json()['registered'] is True
    assert second.get(f'/api/images/{image["id"]}').status_code==401
    assert second.post('/api/auth/password/login',json={'username':'owner.test','password':'incorrect'},headers=H).status_code==401
    second.post('/api/auth/password/login',json={'username':'OWNER.TEST','password':'a long test password 2026'},headers=H).raise_for_status()
    saved=second.get(f'/api/drafts/{did}').json()
    assert saved['body']==body and saved['cover_wechat']==image['id'] and saved['wechat_theme']=='green-white-clean'
    assert second.get(f'/api/images/{image["id"]}').content==png()
    assert second.post('/api/auth/password/register',json={},headers=H).status_code==409
    record=db.row('SELECT * FROM owner_account')
    assert record['password_hash']!='a long test password 2026' and len(record['salt'])==32
    assert second.post('/api/auth/password/login',json={},headers={'Origin':'https://evil.test'}).status_code==403


def test_cloud_apis_do_not_need_codex_or_local_executor(monkeypatch):
    cloud();SecretStore().set('daitu_api_key','test-daitu-credential');SecretStore().set('deepseek_api_key','test-deepseek-credential')
    from content_workbench import codex_native,deepseek_api,native_gateway
    monkeypatch.setattr(codex_native,'Client',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Codex must not be used')))
    monkeypatch.setattr(native_gateway,'rpc',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Gateway must not be used for text')))
    calls=[]
    class Reply:
        status_code=200
        def json(self):return {'model':'claude-opus-5-5','content':[{'type':'text','text':'云端起草正常'}],'stop_reason':'end_turn'}
    class Client:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def post(self,url,**kw):calls.append((url,kw['json']['model']));return Reply()
    monkeypatch.setattr(model_api.httpx,'Client',Client)
    assert llm.run('测试云端写稿',task='write')=='云端起草正常'
    assert calls==[('https://api.daitu.cc/v1/messages','claude-opus-5-5')]
    monkeypatch.setattr(deepseek_api,'run',lambda *a,**kw:'DeepSeek云端正常')
    assert llm.run('测试云端分类',task='classify')=='DeepSeek云端正常'
    assert model_api.status(Settings.load())['ok'] is True


def test_online_snapshot_restores_database_and_its_pictures(tmp_path):
    c=cloud();owner(c)
    did=c.post('/api/drafts',json={'title':'备份','platform':'wechat','body':'保留这一版。'},headers=H).json()['id']
    iid=c.post(f'/api/drafts/{did}/images',files={'files':('backup.png',png(),'image/png')},headers=H).json()[0]['id']
    result=cloud_backup.snapshot()
    from content_workbench.config import data_dir
    stage=data_dir()/'cloud-backups'/result['path']
    assert (stage/'complete.json').exists()
    import sqlite3
    with closing(sqlite3.connect(stage/'workbench.db')) as saved:
        assert saved.execute('SELECT body FROM drafts WHERE id=?',(did,)).fetchone()[0]=='保留这一版。'
        file=saved.execute('SELECT file FROM images WHERE id=?',(iid,)).fetchone()[0]
        assert (stage/'images'/file).read_bytes()==png()
    c.put(f'/api/drafts/{did}',json={'body':'之后的修改'},headers=H).raise_for_status()
    assert (stage/'complete.json').exists()


def test_login_rate_limit_and_no_password_key_in_settings():
    c=cloud();owner(c)
    assert c.get('/api/settings').json()['login_mode']=='password'
    for _ in range(20):assert c.post('/api/auth/password/login',json={'username':'other','password':'wrong'},headers=H).status_code==401
    assert c.post('/api/auth/password/login',json={'username':'other','password':'wrong'},headers=H).status_code==429
    assert 'password_hash' not in c.get('/api/settings').text


def test_migrated_local_instance_opens_cloud_and_relays_old_api_to_same_database(monkeypatch):
    from starlette.responses import JSONResponse
    from content_workbench import cloud_relay
    calls=[]
    async def relay(request,settings):calls.append((request.method,request.url.path));return JSONResponse({'id':123,'cloud':True})
    monkeypatch.setattr(cloud_relay,'forward',relay)
    s=Settings(login_mode='local',cloud_url='https://content.example.test')
    c=TestClient(create_app(s,start_jobs=False),base_url='http://localhost:8765',client=('127.0.0.1',12345))
    response=c.get('/',follow_redirects=False)
    assert response.status_code==302 and response.headers['location']=='https://content.example.test/'
    assert c.post('/api/drafts',json={'body':'同一份云端稿件'},headers={'Origin':'http://localhost:8765'}).json()['cloud'] is True
    assert calls==[('POST','/api/drafts')]
    assert db.row('SELECT COUNT(*) n FROM drafts')['n']==0


def test_cloud_machine_key_is_not_accepted_as_a_browser_cookie_or_cross_origin():
    c=cloud();SecretStore().set('cloud_machine_key','private-machine-credential')
    assert c.get('/api/drafts',headers={'X-Workbench-Machine':'wrong'}).status_code==401
    assert c.get('/api/drafts',headers={'X-Workbench-Machine':'private-machine-credential'}).status_code==200
    assert c.post('/api/drafts',json={'platform':'wechat','body':'不能跨站保存'},headers={'Origin':'https://evil.test','X-Workbench-Machine':'private-machine-credential'}).status_code==403

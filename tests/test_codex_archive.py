import base64
import io
from PIL import Image
from content_workbench import db, llm
from test_app import client, login, ORIGIN


def test_archive_native_draft_images_versions_and_conflicts(monkeypatch):
    monkeypatch.setattr(llm,'run',lambda *a,**k: (_ for _ in ()).throw(AssertionError('must not call model')))
    c=client();login(c);h={'Origin':ORIGIN}
    image=io.BytesIO();Image.new('RGB',(3,3)).save(image,format='PNG')
    p={'content_id':'acceptance','project':'验收项目','platform':'wechat','title':'测试标题','body':'正文\n{{image:1}}','confirmation':'测试确认，不是实际用户定稿','sources':['测试来源'],'images':[{'name':'test.png','base64':base64.b64encode(image.getvalue()).decode()}],'expected_revision':0}
    r=c.post('/api/codex/archive',json=p,headers=h);assert r.status_code==200,r.text
    receipt=r.json();did=receipt['draft_id'];draft=c.get(f'/api/drafts/{did}').json()
    assert draft['status']=='draft' and draft['group_id']
    assert '测试来源' in draft['notes']
    assert c.get(f"/api/images/{draft['images'][0]['id']}").content==image.getvalue()
    assert c.post('/api/codex/archive',json=p,headers=h).json()['reused']
    assert c.post('/api/codex/archive',json={**p,'title':'新版'},headers=h).status_code==409
    updated={**p,'title':'新版','expected_revision':1,'expected_updated_at':draft['updated_at']}
    r=c.post('/api/codex/archive',json=updated,headers=h);assert r.status_code==200,r.text
    assert r.json()['draft_id']==did and r.json()['revision']==2
    assert len(c.get(f'/api/drafts/{did}/versions').json())>=2
    assert db.row('SELECT COUNT(*) n FROM jobs')['n']==0
    c.put(f'/api/drafts/{did}',json={'body':'网页编辑'},headers=h)
    assert c.post('/api/codex/archive',json=updated,headers=h).status_code==409
    assert c.post('/api/codex/archive',json={**p,'content_id':'bad','images':[{'base64':'invalid'}]},headers=h).status_code==400
    assert db.row('SELECT COUNT(*) n FROM drafts')['n']==1


def test_archive_requires_auth_and_origin():
    c=client()
    assert c.post('/api/codex/archive',json={},headers={'Origin':ORIGIN}).status_code==401
    login(c)
    assert c.post('/api/codex/archive',json={}).status_code==403

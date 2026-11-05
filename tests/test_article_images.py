import base64
import io
import uuid
from pathlib import Path
import pytest
from PIL import Image
from content_workbench import article_images,codex_native,llm,db
from content_workbench.config import Settings
from test_app import client,login,ORIGIN


def png():
    out=io.BytesIO();Image.new('RGB',(32,24),'white').save(out,format='PNG');return out.getvalue()


def setup(monkeypatch,tmp_path):
    refs=[]
    for name in ['style.png','own-characters.png','own-master.png']:
        path=tmp_path/name;path.write_bytes(png());refs.append(path)
    monkeypatch.setattr(article_images,'references',lambda:refs.copy())
    monkeypatch.setattr(article_images,'launch',article_images.execute)
    def generate(prompt,references,**kw):
        assert '不改写正文' in prompt
        assert len(references) in (3,4)
        kw['on_event']({'thread_id':'original','turn_id':'turn'})
        return {'images':[{'bytes':png(),'extension':'.png','prompt':prompt}],'message':''}
    monkeypatch.setattr(codex_native,'run_images',generate)
    c=client();login(c)
    return c,{'Origin':ORIGIN}


def test_article_preview_final_image_and_attachment_without_rewriting(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    p={'title':'正文标题','article':'已经确定的真实正文','count':4,'notes':'少用小字','request_key':str(uuid.uuid4())}
    did=c.post('/api/drafts',json={'platform':'wechat','title':'原稿','body':p['article']},headers=h).json()['id']
    r=c.post('/api/article-images',json=p,headers=h);assert r.status_code==200,r.text
    tid=r.json()['id'];row=c.get(f'/api/article-images/{tid}').json()
    assert row['state']=='completed' and row['article']==p['article']
    assert c.get(f'/api/article-images/{tid}/image').content==png()
    assert c.post('/api/article-images',json=p,headers=h).json()['id']==tid
    assert c.post('/api/article-images',json={**p,'article':'changed'},headers=h).status_code==409
    assert c.post(f'/api/article-images/{tid}/attach',json={'draft_id':did},headers=h).status_code==409
    assert c.post(f'/api/article-images/{tid}/final',json={'panel':1,'confirm':False},headers=h).status_code==400
    assert c.post(f'/api/article-images/{tid}/final',json={'panel':5,'confirm':True},headers=h).status_code==400
    final={'panel':1,'confirm':True,'request_key':str(uuid.uuid4())}
    fid=c.post(f'/api/article-images/{tid}/final',json=final,headers=h).json()['id']
    assert c.post(f'/api/article-images/{tid}/final',json={**final,'request_key':str(uuid.uuid4())},headers=h).json()['id']==fid
    assert c.get(f'/api/article-images/{fid}').json()['parent_id']==tid
    attachment=c.post(f'/api/article-images/{fid}/attach',json={'draft_id':did},headers=h).json()
    assert c.post(f'/api/article-images/{fid}/attach',json={'draft_id':did},headers=h).json()==attachment
    assert c.get(f'/api/drafts/{did}').json()['body']==p['article']
    assert db.row('SELECT COUNT(*) n FROM publications')['n']==0


def test_quota_error_stored_without_fake_image_or_regeneration(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    calls=[]
    def reject(*a,**kw):calls.append(1);raise codex_native.CodexError('Usage limit reached')
    monkeypatch.setattr(codex_native,'run_images',reject)
    p={'title':'标题','article':'正文','count':4,'request_key':str(uuid.uuid4())}
    tid=c.post('/api/article-images',json=p,headers=h).json()['id']
    row=c.get(f'/api/article-images/{tid}').json()
    assert row['state']=='failed' and row['artifacts']==[] and 'Usage limit' in row['error']
    assert c.get(f'/api/article-images/{tid}/image').status_code==404
    assert c.post(f'/api/article-images/{tid}/recover',headers=h).status_code==409
    assert c.post('/api/article-images',json=p,headers=h).json()['id']==tid
    assert calls==[1]


def test_native_image_data_and_scope(monkeypatch,tmp_path):
    monkeypatch.setenv('CODEX_HOME',str(tmp_path))
    output=tmp_path/'generated_images'/'thread'/'image.png';output.parent.mkdir(parents=True);output.write_bytes(png())
    assert codex_native.read_generated_image({'savedPath':str(output)},'thread')==(png(),'.png')
    assert codex_native.read_generated_image({'result':base64.b64encode(png()).decode()},'thread')==(png(),'.png')
    other=tmp_path/'other.png';other.write_bytes(png())
    with pytest.raises(codex_native.CodexError,match='不属于'):codex_native.read_generated_image({'savedPath':str(other)},'thread')
    with pytest.raises(codex_native.CodexError,match='失败'):codex_native.read_generated_image({'failure':{'type':'usageLimitExceeded'}},'thread')


def test_image_only_mode_blocks_text_and_requires_origin():
    settings=Settings(llm_backend='disabled');settings.save()
    with pytest.raises(llm.LlmUnavailable,match='暂停'):llm.run('不能消耗文字额度',configuration=settings)
    c=client();assert c.get('/api/article-images').status_code==401
    login(c)
    assert c.post('/api/article-images',json={}).status_code==403
    assert c.post('/api/codex/tasks',json={},headers={'Origin':ORIGIN}).status_code==409

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
    for name in ['style.png','example-pair.png','example-character.png']:
        path=tmp_path/name;path.write_bytes(png());refs.append(path)
    monkeypatch.setattr(article_images,'references',lambda:refs.copy())
    monkeypatch.setattr(article_images,'launch',article_images.execute)
    def generate(prompt,references,**kw):
        assert '不改写正文' in prompt
        assert len(references) in (2,3,4)
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


def test_title_only_cover_is_one_final_image_and_attaches_without_rewriting(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    p={'title':'测试工具＋个人工作台','request_key':str(uuid.uuid4())}
    did=c.post('/api/drafts',json={'platform':'wechat','title':'原稿','body':'保留正文'},headers=h).json()['id']
    result=c.post('/api/article-images/cover',json=p,headers=h)
    assert result.status_code==200,result.text
    tid=result.json()['id'];row=c.get(f'/api/article-images/{tid}').json()
    assert row['kind']=='cover' and row['state']=='completed' and row['count']==1
    assert row['article']=='' and row['parent_id'] is None and row['panel'] is None
    prompt=row['artifacts'][0]['prompt']
    assert '<cover_title>\n'+p['title'] in prompt and '21:9' in prompt
    assert '深绿或近黑背景' in prompt and '不生成九宫格' in prompt
    assert c.get(f'/api/article-images/{tid}/image').content==png()
    assert c.post('/api/article-images/cover',json=p,headers=h).json()['id']==tid
    assert c.post('/api/article-images/cover',json={**p,'cover_platform':'square'},headers=h).status_code==409
    assert c.post(f'/api/article-images/{tid}/final',json={'panel':1,'confirm':True,'request_key':str(uuid.uuid4())},headers=h).status_code==409
    attachment=c.post(f'/api/article-images/{tid}/attach',json={'draft_id':did},headers=h).json()
    assert c.post(f'/api/article-images/{tid}/attach',json={'draft_id':did},headers=h).json()==attachment
    assert c.get(f'/api/drafts/{did}').json()['body']=='保留正文'
    assert db.row('SELECT COUNT(*) n FROM publications')['n']==0


def test_cover_keeps_preference_snapshot_and_explicit_style_and_portrait(monkeypatch,tmp_path):
    import json
    c,h=setup(monkeypatch,tmp_path)
    private=article_images.root()
    reference=private/'references'/'chosen.png';reference.parent.mkdir(exist_ok=True);reference.write_bytes(png())
    profile=private/'visual-profile.json'
    profile.write_text(json.dumps({'preferred_style':'dark','reference_file':'chosen.png','guidance':'原有审美判断'}),encoding='utf-8')
    monkeypatch.setattr(article_images,'launch',lambda tid:None)
    p={'title':'标题保留原文','article':'仅作为参考的正文','cover_platform':'xiaohongshu','cover_style':'editorial','request_key':str(uuid.uuid4())}
    tid=c.post('/api/article-images/cover',json=p,headers=h).json()['id']
    profile.write_text(json.dumps({'preferred_style':'handdrawn','guidance':'后来改过的判断'}),encoding='utf-8')
    row=c.get(f'/api/article-images/{tid}').json()
    prompt=article_images.prompt_for(row)
    assert '原有审美判断' in prompt and '后来改过的判断' not in prompt
    assert '3:4' in prompt and '本次风格：暖白或奶油底' in prompt
    assert '旧参考仅借角色与信息层级' in prompt and '仅作为参考的正文' in prompt
    assert c.post('/api/article-images/cover',json=p,headers=h).json()['id']==tid
    article_images.execute(tid)
    assert c.get(f'/api/article-images/{tid}').json()['state']=='completed'


def test_cover_validation_and_preference_errors_do_not_generate(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    calls=[];monkeypatch.setattr(article_images,'launch',lambda tid:calls.append(tid))
    p={'title':'标题','request_key':str(uuid.uuid4())}
    for invalid in [{'title':''},{'title':'x'*201},{'article':None},{'notes':{}},{'cover_style':[]},{'cover_style':'unknown'},{'cover_platform':{}},{'cover_platform':'unknown'}]:
        assert c.post('/api/article-images/cover',json={**p,**invalid},headers=h).status_code==400
    private=article_images.root()
    (private/'visual-profile.json').write_text('invalid json',encoding='utf-8')
    assert c.get('/api/article-images/preferences').status_code==409
    assert c.post('/api/article-images/cover',json=p,headers=h).status_code==409
    (private/'visual-profile.json').write_text('{"reference_file":"../outside.png"}',encoding='utf-8')
    assert c.post('/api/article-images/cover',json=p,headers=h).status_code==409
    (private/'visual-profile.json').write_text('{"reference_file":"missing.png"}',encoding='utf-8')
    assert c.post('/api/article-images/cover',json=p,headers=h).status_code==409
    assert not calls


def test_cover_failure_is_not_retried_and_preferences_are_private(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    calls=[]
    def reject(*args,**kwargs):
        calls.append(1)
        raise codex_native.CodexError('Usage limit reached')
    monkeypatch.setattr(codex_native,'run_images',reject)
    p={'title':'我的封面','request_key':str(uuid.uuid4())}
    tid=c.post('/api/article-images/cover',json=p,headers=h).json()['id']
    row=c.get(f'/api/article-images/{tid}').json()
    assert row['state']=='failed' and row['artifacts']==[] and 'Usage limit' in row['error']
    assert c.post('/api/article-images/cover',json=p,headers=h).json()['id']==tid
    assert calls==[1]
    assert c.get('/api/article-images/preferences').json()['preferred_style']=='dark'
    c.cookies.clear()
    assert c.get('/api/article-images/preferences').status_code==401


def test_image_schema_upgrade_preserves_existing_preview(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    p={'title':'旧任务','article':'旧正文不改','count':4,'request_key':str(uuid.uuid4())}
    tid=c.post('/api/article-images',json=p,headers=h).json()['id']
    with db.tx() as conn:
        for column in ['cover_platform','cover_style','visual_profile']:
            conn.execute('ALTER TABLE article_image_tasks DROP COLUMN '+column)
    upgraded=client();login(upgraded)
    old=upgraded.get(f'/api/article-images/{tid}').json()
    assert old['title']==p['title'] and old['article']==p['article']
    assert old['kind']=='preview' and old['state']=='completed' and old['artifacts']
    assert old['cover_platform']=='wechat' and old['cover_style']=='auto'


def test_cover_and_article_images_queue_instead_of_rejecting(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    started=[];monkeypatch.setattr(article_images,'launch',lambda tid:started.append(tid))
    cover=c.post('/api/article-images/cover',json={'title':'封面标题','request_key':str(uuid.uuid4())},headers=h)
    assert cover.status_code==200,cover.text
    assert cover.json()['queued'] is False and started==[cover.json()['id']]
    article=c.post('/api/article-images',json={'title':'正文标题','article':'已经确定的正文','count':2,'request_key':str(uuid.uuid4())},headers=h)
    assert article.status_code==200,article.text
    assert article.json()['queued'] is True and article.json()['ahead']==1
    states={r['id']:r['state'] for r in c.get('/api/article-images').json()}
    assert states[cover.json()['id']]=='running' and states[article.json()['id']]=='queued'
    queued=article.json()['id']
    assert c.get(f'/api/article-images/{queued}/image').status_code==404
    assert c.post(f'/api/article-images/{queued}/recover',headers=h).status_code==409
    article_images.execute(cover.json()['id'])
    assert started==[cover.json()['id'],queued]
    assert c.get(f'/api/article-images/{queued}').json()['state']=='running'
    article_images.execute(queued)
    done=c.get(f'/api/article-images/{queued}').json()
    assert done['state']=='completed' and done['artifacts'] and done['article']=='已经确定的正文'


def test_queued_task_starts_automatically_after_the_running_one(monkeypatch,tmp_path):
    import threading
    import time
    c,h=setup(monkeypatch,tmp_path)
    active=[];depth=[];lock=threading.Lock()
    def generate(prompt,references,**kw):
        with lock:active.append(1);depth.append(len(active))
        time.sleep(0.3)
        with lock:active.pop()
        return {'images':[{'bytes':png(),'extension':'.png','prompt':prompt}],'message':''}
    monkeypatch.setattr(codex_native,'run_images',generate)
    monkeypatch.setattr(article_images,'launch',lambda tid:threading.Thread(target=article_images.execute,args=(tid,),daemon=True).start())
    cover=c.post('/api/article-images/cover',json={'title':'封面','request_key':str(uuid.uuid4())},headers=h).json()
    article=c.post('/api/article-images',json={'title':'正文','article':'已经确定的正文','count':2,'request_key':str(uuid.uuid4())},headers=h).json()
    assert cover['queued'] is False and article['queued'] is True
    deadline=time.time()+20
    while time.time()<deadline:
        rows=c.get('/api/article-images').json()
        if len(rows)==2 and all(r['state'] in ('completed','failed') for r in rows):break
        time.sleep(0.05)
    assert sorted(r['state'] for r in c.get('/api/article-images').json())==['completed','completed']
    assert max(depth)==1


def test_queued_task_can_be_cancelled_before_it_starts(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    started=[];monkeypatch.setattr(article_images,'launch',lambda tid:started.append(tid))
    first=c.post('/api/article-images/cover',json={'title':'第一张','request_key':str(uuid.uuid4())},headers=h).json()['id']
    second=c.post('/api/article-images/cover',json={'title':'第二张','request_key':str(uuid.uuid4())},headers=h).json()
    assert second['queued'] is True
    cancelled=c.post(f"/api/article-images/{second['id']}/cancel",headers=h)
    assert cancelled.status_code==200 and cancelled.json()['state']=='cancelled'
    article_images.execute(first)
    assert started==[first]
    assert c.get(f"/api/article-images/{second['id']}").json()['state']=='cancelled'

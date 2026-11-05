import json
from pathlib import Path
import pytest
from content_workbench import db, jobs, llm, media_imports as media
from content_workbench.config import Settings
from test_app import client, login, ORIGIN
H={'Origin':ORIGIN}
RAW='[00:01] 说话人1：嗯，AI帮助我整理资料，但结果仍需人工核对。\n这个例子只用于测试，不是用户经历。'

def setup(monkeypatch):
    c=client();login(c)
    def bridge(mid, mode):
        assert mode=='distill'
        base=media.folder(mid)
        raw=(base/'raw.txt').read_text(encoding='utf-8')
        (base/'clean.txt').write_text(raw.replace('嗯，',''),encoding='utf-8')
        (base/'article.md').write_text('## 整理资料\n\nAI帮助整理资料，但结果仍需人工核对。测试中的例子不属于用户经历。',encoding='utf-8')
        (base/'quality.json').write_text('[]',encoding='utf-8')
    monkeypatch.setattr(media,'bridge',bridge)
    return c

def create(c):
    r=c.post('/api/media-imports',json={'text':RAW,'title':'测试原题','distiller':'xiaod'},headers=H)
    assert r.status_code==200,r.text
    return r.json()

def complete(c,mid):
    jobs.run_job(Settings(),'media_import',{'media_id':mid})
    return c.get(f'/api/media-imports/{mid}').json()

def test_transcript_steps_preserve_original_and_skip_audio(monkeypatch):
    c=setup(monkeypatch);item=create(c);row=complete(c,item['id'])
    assert row['state']=='done' and row['stage']==4 and row['skipped']==[1,2]
    assert row['raw']==RAW and row['clean']!=row['raw'] and 'AI实战' in row['tags']
    assert row['material_id'] is None and row['benchmark_id'] is None
    assert not db.rows('SELECT * FROM drafts')
    assert c.get(f"/api/media-imports/{item['id']}/audio").status_code==404

def test_failure_checkpoint_retry_does_not_repeat_completed_stages(monkeypatch):
    c=setup(monkeypatch);item=create(c);mid=item['id'];real=media.bridge
    monkeypatch.setattr(media,'bridge',lambda *_: (_ for _ in ()).throw(RuntimeError('配置待检查')))
    with pytest.raises(RuntimeError):media.run({'media_id':mid})
    row=media.require(mid)
    assert row['state']=='failed' and row['stage']==3 and row['raw']==RAW
    jobs._set(item['job_id'],'failed')
    assert c.post(f'/api/media-imports/{mid}/retry',headers=H).status_code==200
    monkeypatch.setattr(media,'bridge',real)
    assert complete(c,mid)['state']=='done'
    assert c.post(f'/api/media-imports/{mid}/retry',headers=H).status_code==409

def test_save_is_explicit_idempotent_and_preserves_provenance(monkeypatch):
    c=setup(monkeypatch);item=create(c);mid=item['id']
    assert c.post(f'/api/media-imports/{mid}/save',json={'target':'benchmark'},headers=H).status_code==409
    row=complete(c,mid)
    for target,table in [('material','materials'),('benchmark','benchmarks')]:
        ids=[c.post(f'/api/media-imports/{mid}/save',json={'target':target},headers=H).json()['id'] for _ in range(2)]
        assert ids[0]==ids[1]
        saved=c.get(f'/api/{table}/{ids[0]}').json()
        assert saved['media_id']==mid and saved['body']==row['article']
    benchmark=media.require(mid)['benchmark_id']
    assert c.get(f'/api/benchmarks/{benchmark}').json()['analysis'] is None
    assert c.put(f'/api/media-imports/{mid}',json={'title':'整理标题','article':'修订后的整理稿','tags':['资料整理'],'target_platforms':['wechat']},headers=H).status_code==200
    assert media.require(mid)['original_title']=='测试原题'
    assert media.require(mid)['raw']==RAW

def test_platform_versions_have_distinct_ids_share_source_and_no_auto_publish(monkeypatch):
    c=setup(monkeypatch);item=create(c);mid=item['id'];complete(c,mid)
    r=c.post(f'/api/media-imports/{mid}/drafts',json={'platforms':['wechat','xiaohongshu','douyin','x']},headers=H)
    assert r.status_code==200,r.text
    versions=r.json();assert len({v['id'] for v in versions})==4
    same=c.post(f'/api/media-imports/{mid}/drafts',json={'platforms':['douyin']},headers=H).json()
    assert same[0]['id']==versions[2]['id'] and len(db.rows("SELECT * FROM jobs WHERE kind='media_draft'"))==4
    monkeypatch.setattr(llm,'run',lambda prompt,**kw:json.dumps({'title':'测试平台稿','body':'外部作者的案例需要核对。','notes':'补充自己的经历。'},ensure_ascii=False))
    for version in versions:
        jobs.run_job(Settings(),'media_draft',{'draft_id':version['id'],'media_id':mid})
        d=c.get(f"/api/drafts/{version['id']}").json()
        assert d['media_id']==mid and d['status']=='draft' and d['platform']==version['platform']
    assert not db.rows('SELECT * FROM publications')
    assert c.post(f"/api/drafts/{versions[2]['id']}/publish",json={'confirm':True},headers=H).status_code==400

def test_no_empty_or_unknown_platform_or_unsupported_url(monkeypatch):
    c=setup(monkeypatch)
    for body in [{},{'text':'a','url':'https://b23.tv/abc'},{'url':'http://127.0.0.1/a'},{'url':'file:///etc/passwd'},{'url':'https://douyin.com.evil.example/a'},{'text':'x'*200001}]:
        assert c.post('/api/media-imports',json=body,headers=H).status_code==400
    mid=create(c)['id'];complete(c,mid)
    for platform in [[],['invalid'],'wechat',[{}]]:
        r=c.post(f'/api/media-imports/{mid}/drafts',json={'platforms':platform},headers=H)
        assert r.status_code==400,r.text

def test_upload_text_and_invalid_files(monkeypatch):
    c=setup(monkeypatch)
    r=c.post('/api/media-imports/upload',files={'file':('test.txt',RAW.encode(),'text/plain')},data={'distiller':'xiaod'},headers=H)
    assert r.status_code==200,r.text
    assert complete(c,r.json()['id'])['raw']==RAW
    for name,value in [('test.exe',b'data'),('test.wav',b''),('test.txt',b'\xff\xff')]:
        assert c.post('/api/media-imports/upload',files={'file':(name,value)},headers=H).status_code==400
    assert len(db.rows('SELECT * FROM media_imports'))==1

def test_delivery_copy_export_do_not_mark_published(monkeypatch):
    c=setup(monkeypatch)
    did=c.post('/api/drafts',json={'platform':'xiaohongshu','body':'测试正文'},headers=H).json()['id']
    for kind in ['copied','exported']:
        assert c.post(f'/api/drafts/{did}/delivery-record',json={'kind':kind},headers=H).status_code==200
        assert c.get(f'/api/drafts/{did}').json()['status']=='draft'
    assert c.post(f'/api/drafts/{did}/delivery-record',json={'kind':'manual_published','url':'https://www.xiaohongshu.com/a'},headers=H).status_code==400
    assert c.post(f'/api/drafts/{did}/delivery-record',json={'kind':'manual_published','confirm':True,'url':'https://www.xiaohongshu.com/a'},headers=H).status_code==200
    assert c.get(f'/api/drafts/{did}').json()['status']=='published'

def test_captions_skip_timecodes_and_deduplicate(tmp_path):
    path=tmp_path/'source.zh.vtt';path.write_text('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nAI整理资料\nAI整理资料\n00:00:02.000 --> 00:00:03.000\n核对原文\n',encoding='utf-8')
    assert media.caption_text(path)=='AI整理资料\n核对原文'


def test_replayed_import_does_not_overwrite_reviewed_edit(monkeypatch):
    c=setup(monkeypatch);mid=create(c)['id'];complete(c,mid)
    c.put(f'/api/media-imports/{mid}',json={'article':'本人修订保留'},headers=H)
    monkeypatch.setattr(media,'bridge',lambda *_:pytest.fail('Completed import repeated a model call'))
    assert complete(c,mid)['article']=='本人修订保留'

def test_active_retry_and_unauthenticated_mutation_are_blocked(monkeypatch):
    c=setup(monkeypatch);mid=create(c)['id']
    assert c.post(f'/api/media-imports/{mid}/retry',headers=H).status_code==409
    c.cookies.clear()
    assert c.post('/api/media-imports',json={'text':'未授权请求'},headers=H).status_code in (401,403)

def test_repeated_save_cannot_overwrite_existing_library_edit(monkeypatch):
    c=setup(monkeypatch);mid=create(c)['id'];complete(c,mid)
    bid=c.post(f'/api/media-imports/{mid}/save',json={'target':'benchmark'},headers=H).json()['id']
    c.put(f'/api/benchmarks/{bid}',json={'body':'库内人工修订'},headers=H)
    assert c.post(f'/api/media-imports/{mid}/save',json={'target':'benchmark'},headers=H).status_code==409
    assert c.get(f'/api/benchmarks/{bid}').json()['body']=='库内人工修订'


def test_xhs_response_selects_exact_requested_note():
    from content_workbench.tikhub_media import select_note
    data={'data':[{'id':'wrong','video':{'url':'https://cdn.example/wrong'}},{'id':'requested','video_info_v2':{'media':{}}},{'id':'third','video':{}}]}
    assert select_note(data,'requested')['id']=='requested'
    with pytest.raises(RuntimeError):select_note(data,'missing')

def test_cancel_queued_import_preserves_source_and_can_retry(monkeypatch):
    c=setup(monkeypatch);item=create(c);mid=item['id']
    r=c.post(f'/api/media-imports/{mid}/stop',headers=H)
    assert r.status_code==200 and r.json()['state']=='failed'
    assert db.row('SELECT state FROM jobs WHERE id=?',(item['job_id'],))['state']=='cancelled'
    assert (media.folder(mid)/'raw.txt').read_text(encoding='utf-8')==RAW
    assert c.post(f'/api/media-imports/{mid}/retry',headers=H).status_code==200


def test_workbench_distiller_requires_explicit_choice_and_keeps_source(monkeypatch):
    c=setup(monkeypatch)
    item=c.post('/api/media-imports',json={'text':RAW,'title':'测试','distiller':'workbench'},headers=H).json()
    def clean(mid,mode):
        assert mode in ('clean','quality')
        (media.folder(mid)/'clean.txt').write_text('AI帮助整理资料。',encoding='utf-8')
        (media.folder(mid)/'quality.json').write_text('[]',encoding='utf-8')
    monkeypatch.setattr(media,'bridge',clean)
    monkeypatch.setattr(llm,'run',lambda prompt,**kwargs:'## 资料整理\n\nAI帮助整理资料，仍需核对来源。')
    monkeypatch.setattr(llm,'status',lambda:{'detail':'测试模型，非真实调用'})
    result=complete(c,item['id'])
    assert result['state']=='done' and result['raw']==RAW
    assert result['model_info']['provider']=='workbench' and result['distiller']=='workbench'

def test_scribe_duplicate_views_are_not_distilled_twice():
    from content_workbench.xiaod_bridge import transcript_body
    assert transcript_body('## Transcription with Timestamps\n00:01 同一句\n## Full Transcription\n同一句')=='同一句'
    assert transcript_body('普通文字')=='普通文字'


def test_new_import_defaults_to_authorized_workbench_model(monkeypatch):
    c=setup(monkeypatch)
    row=c.post('/api/media-imports',json={'text':'测试文字'},headers=H).json()
    assert row['distiller']=='workbench' and row['asr_model']=='base'

def test_review_full_body_search_and_platform_assignment(monkeypatch):
    c=setup(monkeypatch)
    body='前言'*300+'独特命中词在全文尾部'
    mid=c.post('/api/materials',json={'title':'全文搜索测试','body':body,'extract':False},headers=H).json()['id']
    found=c.get('/api/materials',params={'q':'独特命中词'}).json()
    assert [r['id'] for r in found]==[mid]
    assert '独特命中词' in found[0]['preview']
    assert c.get('/api/materials',params={'q':'%'}).json()==[]
    assert any(r['id']==mid for r in c.get('/api/materials?platform=unassigned').json())
    item=create(c);complete(c,item['id'])
    c.put(f"/api/media-imports/{item['id']}",json={'target_platforms':['wechat','x'],'angle':'创作者整理资料','facts':'本人尚未验证'},headers=H)
    saved=c.post(f"/api/media-imports/{item['id']}/save",json={'target':'material'},headers=H).json()['id']
    assert [r['id'] for r in c.get('/api/materials?platform=wechat').json()]==[saved]
    assert media.require(item['id'])['angle']=='创作者整理资料'


def test_review_new_versions_and_persistent_associations(monkeypatch):
    c=setup(monkeypatch);mid=create(c)['id'];complete(c,mid)
    path=f'/api/media-imports/{mid}/drafts'
    first=c.post(path,json={'platforms':['wechat']},headers=H).json()[0]['id']
    again=c.post(path,json={'platforms':['wechat'],'angle':'新要求'},headers=H).json()[0]['id']
    second=c.post(path,json={'platforms':['wechat'],'angle':'新要求','new_version':True},headers=H).json()[0]['id']
    assert first==again and first!=second
    assert [r['id'] for r in c.get(f'/api/media-imports/{mid}').json()['drafts']]==[second,first]
    payload=db.loads(db.row('SELECT payload FROM jobs WHERE kind=? ORDER BY id DESC',('media_draft',))['payload'])
    assert payload['angle']=='新要求'
    assert c.post(path,json={'platforms':['wechat'],'new_version':'yes'},headers=H).status_code==400


def test_review_history_paging_filter_archive_restore(monkeypatch):
    c=setup(monkeypatch)
    ids=[media.new(f'归档测试{i}','文字') for i in range(105)]
    media.write(ids[0],state='done',article='很久以前的全文关键词')
    pages=[c.get('/api/media-imports',params={'offset':n,'limit':30}).json() for n in range(0,120,30)]
    assert len({r['id'] for page in pages for r in page})==105
    assert c.get('/api/media-imports?q=全文关键词&state=done').json()[0]['id']==ids[0]
    assert c.post(f'/api/media-imports/{ids[0]}/archive',json={'archived':True},headers=H).status_code==200
    assert c.get('/api/media-imports?q=全文关键词').json()==[]
    assert c.get('/api/media-imports?archived=true').json()[0]['id']==ids[0]
    c.post(f'/api/media-imports/{ids[0]}/archive',json={'archived':False},headers=H)
    assert c.get('/api/media-imports?q=全文关键词').json()
    assert c.post(f'/api/media-imports/{ids[1]}/archive',json={'archived':True},headers=H).status_code==409
    assert c.get('/api/media-imports?offset=-1').status_code==422


def test_share_copy_extracts_one_link_without_running_services(monkeypatch):
    monkeypatch.setattr(media.references,'_addresses',lambda *a:['8.8.8.8'])
    monkeypatch.setattr(media,'queue',lambda mid:None)
    with client() as c:
        login(c)
        response=c.post('/api/media-imports',headers=H,json={'url':'2.07 复制打开抖音，看看【作者的作品】 AI学习 https://v.douyin.com/HyG9kZK7l2E/ 08/15 :8pm qe:/ b@a.At'})
        assert response.status_code==200
        assert response.json()['url']=='https://v.douyin.com/HyG9kZK7l2E/'
        assert response.json()['source_platform']=='抖音'
        duplicate=c.post('/api/media-imports',headers=H,json={'url':'https://v.douyin.com/HyG9kZK7l2E/'})
        assert duplicate.json()['id']==response.json()['id']
        assert c.post('/api/media-imports',headers=H,json={'url':'两个 https://v.douyin.com/a/ https://v.douyin.com/b/'}).status_code==400

"""Batch ingestion: actual queues and SQLite, all remote/media/model work stubbed."""
import json
import pytest
from content_workbench import db,jobs,benchmarks,llm,intake_batches as intake,media_imports as media,research_protocol
from content_workbench.config import Settings,SecretStore
from test_app import client,login,ORIGIN
from test_writing_workspace import report

H={'Origin':ORIGIN}


def setup(monkeypatch):
    c=client();login(c)
    def read(url):return {'title':'原文标题','body':'先展示真实成果。\n\n再解释操作过程。\n\n一次结果不能泛化。','url':url,'author':'原作者','original_html':'<p>先展示真实成果。</p>'}
    monkeypatch.setattr(benchmarks,'read_source',read)
    calls=[]
    def model(prompt,**kw):
        calls.append(kw)
        data=json.loads(prompt.split('<资料>\n')[1].rsplit('\n</资料>',1)[0])
        key=data['paragraphs'][0]['key'];quote=data['paragraphs'][0]['text']
        if '基础研究只输出JSON' in prompt:
            return json.dumps({**{k:'有依据的研究，适用范围待验证。' for k in benchmarks.BASIC_FIELDS},'basis':[{'field':k,'paragraph_key':key,'quote':quote} for k in benchmarks.BASIC_FIELDS[:-1]]},ensure_ascii=False)
        return json.dumps(report({'body':'\n\n'.join(p['text'] for p in data['paragraphs'])}),ensure_ascii=False)
    monkeypatch.setattr(llm,'run',model)
    return c,calls


def create(c,items,**kw):
    r=c.post('/api/intake-batches',headers=H,json={'request_key':kw.pop('key','batch-1'),'items':items,'defaults':kw})
    assert r.status_code==200,r.text
    return r.json()


def entry(depth='basic',value='https://example.com/article',**kw):return {'input_kind':'url','input_value':value,'depth':depth,**kw}


def drain():
    for _ in range(100):
        job=jobs._claim(True)
        if not job:return
        payload=json.loads(job['payload'])
        try:jobs.run_job(Settings(),job['kind'],payload);jobs._set(job['id'],'done')
        except Exception as exc:jobs._set(job['id'],'failed',str(exc));jobs._mark_failure(job['kind'],payload,exc)
    pytest.fail('Queue failed to settle')


def test_mixed_batch_archives_originals_and_research_is_not_personal_judgment(monkeypatch):
    c,calls=setup(monkeypatch)
    b=create(c,[entry(),entry('archive','https://example.com/archive'),{'input_kind':'text','input_value':'我做过的一次真实任务','destination':'material','depth':'archive','title':'我的记录'}])
    drain();result=c.get(f'/api/intake-batches/{b["id"]}').json()
    assert {i['state'] for i in result['items']}=={'done'} and not result['active']
    assert len(calls)==1 and db.row('SELECT COUNT(*) n FROM benchmarks')['n']==2
    assert db.row('SELECT COUNT(*) n FROM materials')['n']==1
    assert db.rows('SELECT * FROM benchmark_reviews')==[] and db.rows('SELECT * FROM drafts')==[]
    assert result['items'][0]['analysis_id'] and result['items'][1]['analysis_id'] is None
    bid=result['items'][0]['benchmark_id'];source=c.get(f'/api/benchmarks/{bid}').json()
    assert source['body'].startswith('先展示真实成果') and source['author']=='原作者'


def test_repeated_requests_rows_and_batches_do_not_duplicate_calls(monkeypatch):
    c,calls=setup(monkeypatch);rows=[entry(),entry()]
    b=create(c,rows);assert create(c,rows)['id']==b['id']
    assert len(db.rows("SELECT * FROM jobs WHERE kind='intake_item'"))==1
    drain();again=create(c,[entry()],key='batch-2');drain()
    assert len(calls)==1 and c.get(f'/api/intake-batches/{again["id"]}').json()['items'][0]['reused']
    assert c.post('/api/intake-batches',headers=H,json={'request_key':'batch-1','items':[entry('archive')]}).status_code==409


def test_archive_never_uses_model_or_classification(monkeypatch):
    c,_=setup(monkeypatch);monkeypatch.setattr(llm,'run',lambda *a,**kw:pytest.fail('Archive invoked model'))
    b=create(c,[entry('archive')]);drain()
    assert c.get(f'/api/intake-batches/{b["id"]}').json()['items'][0]['state']=='done'
    assert not db.rows('SELECT * FROM benchmark_analyses')


def test_model_and_rules_frozen_when_batch_submitted(monkeypatch):
    c,calls=setup(monkeypatch);SecretStore().set('deepseek_api_key','test-not-real')
    b=create(c,[entry(model='deepseek-flash')])
    Settings(llm_backend='claude-code',llm_model='opus').save()
    drain();item=c.get(f'/api/intake-batches/{b["id"]}').json()['items'][0]
    assert item['model']=='deepseek-flash' and calls[0]['task']=='research-basic'
    assert calls[0]['configuration'].llm_backend=='deepseek-api'


def test_research_failure_retries_without_refetching(monkeypatch):
    c,_=setup(monkeypatch);real=llm.run
    monkeypatch.setattr(llm,'run',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('模拟研究失败')))
    b=create(c,[entry()]);drain();i=c.get(f'/api/intake-batches/{b["id"]}').json()['items'][0]
    assert i['state']=='failed' and i['benchmark_id']
    monkeypatch.setattr(benchmarks,'read_source',lambda *a:pytest.fail('Refetched original'))
    monkeypatch.setattr(llm,'run',real)
    c.post(f'/api/intake-batches/{b["id"]}/retry',headers=H,json={'item_id':i['id']});drain()
    assert c.get(f'/api/intake-batches/{b["id"]}').json()['items'][0]['state']=='done'


def test_one_link_failure_does_not_stop_other_items_and_can_supplement(monkeypatch):
    c,_=setup(monkeypatch);real=benchmarks.read_source
    monkeypatch.setattr(benchmarks,'read_source',lambda u:(_ for _ in ()).throw(references_error()) if 'blocked' in u else real(u))
    b=create(c,[entry('archive','https://example.com/blocked'),entry('archive')]);drain();items=c.get(f'/api/intake-batches/{b["id"]}').json()['items']
    assert [i['state'] for i in items]==['failed','done']
    r=c.post(f'/api/intake-batches/{b["id"]}/items/{items[0]["id"]}/supplement',headers=H,json={'text':'用户提供的完整原文。'})
    assert r.status_code==200;drain()
    done=c.get(f'/api/intake-batches/{b["id"]}').json()['items'][0]
    assert done['state']=='done' and done['input_value']=='https://example.com/blocked'
    assert benchmarks.require(done['benchmark_id'])['body']=='用户提供的完整原文。'


def references_error():
    from content_workbench.references import ReferenceError
    return ReferenceError('无法读取原文')


def test_uploaded_transcript_is_kept_raw_and_deduplicated(monkeypatch):
    c,calls=setup(monkeypatch)
    raw='[00:01] 原始口播与数字100。\n[00:05] 不清理时间戳。'
    upload=c.post('/api/intake-batches/upload',headers=H,files={'file':('口播.txt',raw.encode(),'text/plain')})
    assert upload.status_code==200,upload.text
    monkeypatch.setattr(media,'bridge',lambda *a:pytest.fail('Text upload invoked XiaoD'))
    b=create(c,[{**upload.json(),'depth':'archive'}]);drain();i=c.get(f'/api/intake-batches/{b["id"]}').json()['items'][0]
    assert i['state']=='done' and benchmarks.require(i['benchmark_id'])['body']==raw and not calls
    second=c.post('/api/intake-batches/upload',headers=H,files={'file':('另一个文件名.txt',raw.encode(),'text/plain')}).json()
    dup=create(c,[{**second,'depth':'archive'}],key='second');drain()
    assert c.get(f'/api/intake-batches/{dup["id"]}').json()['items'][0]['benchmark_id']==i['benchmark_id']


def test_pause_before_start_blocks_remote_calls_and_resume_works(monkeypatch):
    c,calls=setup(monkeypatch);b=create(c,[entry()]);i=b['items'][0]
    assert c.post(f'/api/intake-batches/{b["id"]}/items/{i["id"]}/pause',headers=H,json={}).status_code==200
    drain();assert not calls
    assert c.post(f'/api/intake-batches/{b["id"]}/retry',headers=H,json={'item_id':i['id']}).status_code==200
    drain();assert len(calls)==1


def test_queue_restart_replays_are_idempotent(monkeypatch):
    c,calls=setup(monkeypatch);b=create(c,[entry()]);item=b['items'][0]
    intake.run({'item_id':item['id']})
    intake.run({'item_id':item['id']})
    assert len(db.rows("SELECT * FROM jobs WHERE kind='benchmark_analyze'"))==1
    drain();assert len(calls)==1


def test_invalid_batch_does_not_enqueue_partial_work(monkeypatch):
    c,_=setup(monkeypatch)
    r=c.post('/api/intake-batches',headers=H,json={'request_key':'bad','items':[entry(),entry(model='unsupported-model')]})
    assert r.status_code==400 and not db.rows('SELECT * FROM intake_batches')
    assert not db.rows('SELECT * FROM jobs')


def test_share_text_preview_normalizes_and_preserves_distinct_urls(monkeypatch):
    c,_=setup(monkeypatch)
    r=c.post('/api/intake-batches/preview',headers=H,json={'text':'抖音分享 https://v.douyin.com/Abc/ 公众号 https://example.com/a?utm_source=x。'})
    assert r.status_code==200
    assert [i['input_value'] for i in r.json()['items']]==['https://v.douyin.com/Abc/','https://example.com/a']


def test_two_depths_of_same_source_wait_without_repeated_acquisition(monkeypatch):
    c,calls=setup(monkeypatch);reads=[];real=benchmarks.read_source
    monkeypatch.setattr(benchmarks,'read_source',lambda url:(reads.append(url),real(url))[1])
    b=create(c,[entry('basic'),entry('deep')]);drain()
    items=c.get(f'/api/intake-batches/{b["id"]}').json()['items']
    assert len(reads)==1 and len(calls)==2
    assert {i['state'] for i in items}=={'done'} and items[0]['benchmark_id']==items[1]['benchmark_id']
    assert len({i['analysis_id'] for i in items})==2


def test_audio_extract_only_preserves_timestamps_and_never_distills(monkeypatch):
    c,calls=setup(monkeypatch);raw='[00:01] 原始视频口播。\n[00:05] 原始数字与证据。'
    uploaded=c.post('/api/intake-batches/upload',headers=H,files={'file':('录音.mp3',b'fake-audio','audio/mpeg')}).json()
    class Result:returncode=0
    monkeypatch.setattr(media.subprocess,'run',lambda *a,**kw:Result())
    def bridge(mid,mode):
        assert mode=='transcribe','Batch unexpectedly used paid distillation'
        (media.folder(mid)/'raw.txt').write_text(raw,encoding='utf-8')
    monkeypatch.setattr(media,'bridge',bridge)
    b=create(c,[{**uploaded,'depth':'archive'}]);drain();i=c.get(f'/api/intake-batches/{b["id"]}').json()['items'][0]
    assert i['state']=='done' and not calls
    assert benchmarks.require(i['benchmark_id'])['body']==raw
    assert media.require(i['media_id'])['extraction_only']==1
    assert (media.folder(i['media_id'])/'source.mp3').read_bytes()==b'fake-audio'


def test_cancelled_old_extraction_job_cannot_overwrite_resumed_item(monkeypatch):
    c,calls=setup(monkeypatch);b=create(c,[entry()]);i=b['items'][0];old=i['job_id']
    c.post(f'/api/intake-batches/{b["id"]}/items/{i["id"]}/pause',headers=H,json={})
    c.post(f'/api/intake-batches/{b["id"]}/retry',headers=H,json={'item_id':i['id']})
    intake.run({'item_id':i['id'],'job_id':old})
    assert not db.rows('SELECT * FROM benchmarks')
    drain();assert len(calls)==1


def test_resume_failure_keeps_completed_asr_checkpoint(monkeypatch):
    c,calls=setup(monkeypatch)
    uploaded=c.post('/api/intake-batches/upload',headers=H,files={'file':('录音.mp3',b'fake-audio','audio/mpeg')}).json()
    class Result:returncode=0
    monkeypatch.setattr(media.subprocess,'run',lambda *a,**kw:Result())
    transcribed=[]
    def bridge(mid,mode):
        transcribed.append(mode);(media.folder(mid)/'raw.txt').write_text('保留的原始口播。',encoding='utf-8')
    monkeypatch.setattr(media,'bridge',bridge)
    actual=benchmarks.add_benchmark
    monkeypatch.setattr(benchmarks,'add_benchmark',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('模拟入库失败')))
    b=create(c,[{**uploaded,'depth':'archive'}]);drain()
    monkeypatch.setattr(benchmarks,'add_benchmark',actual)
    c.post(f'/api/intake-batches/{b["id"]}/retry',headers=H,json={});drain()
    assert transcribed==['transcribe'] and not calls
    assert c.get(f'/api/intake-batches/{b["id"]}').json()['items'][0]['state']=='done'


def test_supplement_file_keeps_original_link_and_skips_remote_fetch(monkeypatch):
    c,_=setup(monkeypatch)
    monkeypatch.setattr(benchmarks,'read_source',lambda *a:(_ for _ in ()).throw(references_error()))
    b=create(c,[entry('archive')]);drain();i=c.get(f'/api/intake-batches/{b["id"]}').json()['items'][0]
    uploaded=c.post('/api/intake-batches/upload',headers=H,files={'file':('补充.txt','这是补充的原始转写。'.encode(),'text/plain')}).json()
    r=c.post(f'/api/intake-batches/{b["id"]}/items/{i["id"]}/supplement',headers=H,json={'media_id':int(uploaded['input_value'])})
    assert r.status_code==200;drain()
    done=c.get(f'/api/intake-batches/{b["id"]}').json()['items'][0]
    assert done['state']=='done'
    source=benchmarks.require(done['benchmark_id'])
    assert source['url']=='https://example.com/article' and source['body']=='这是补充的原始转写。'


def test_retry_all_reuses_failed_duplicate_owner(monkeypatch):
    c,calls=setup(monkeypatch);real=benchmarks.read_source
    monkeypatch.setattr(benchmarks,'read_source',lambda *a:(_ for _ in ()).throw(references_error()))
    b=create(c,[entry(),entry()]);drain()
    monkeypatch.setattr(benchmarks,'read_source',real)
    c.post(f'/api/intake-batches/{b["id"]}/retry',headers=H,json={});drain()
    assert len(calls)==1
    assert {i['state'] for i in c.get(f'/api/intake-batches/{b["id"]}').json()['items']}=={'done'}

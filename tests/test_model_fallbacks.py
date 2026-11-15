import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from content_workbench import benchmarks, codex_native, db, deepseek_api, intake_batches, jobs, llm, media_imports as media
from content_workbench.config import Settings, SecretStore
from test_app import client, login, ORIGIN
from test_intake_batches import drain

H = {'Origin': ORIGIN}
RAW = '先展示真实成果。\n\n再解释操作过程。\n\n一次结果不能泛化。'


def setup(monkeypatch):
    s = Settings(llm_backend='codex-cli'); s.save()
    SecretStore().set(deepseek_api.SECRET_KEY, 'test-key')
    c = client(llm_backend='codex-cli'); login(c)
    monkeypatch.setattr(benchmarks, 'read_source', lambda url: {'title':'测试素材', 'body':RAW, 'url':url, 'author':'原作者'})
    native, api = [], []
    def unavailable(*a, **kw):
        native.append(kw)
        raise codex_native.CodexError('Codex usage limit reached')
    monkeypatch.setattr(codex_native, 'run', unavailable)
    def reply(prompt, timeout, task, **kw):
        api.append((task, kw))
        if not kw.get('json_output'):
            return RAW
        data = json.loads(prompt.split('<资料>\n')[1].rsplit('\n</资料>',1)[0])
        passage = data['paragraphs'][0]
        return json.dumps({**{k:'有原文依据，适用范围待验证。' for k in benchmarks.BASIC_FIELDS},
                           'basis':[{'field':k,'paragraph_key':passage['key'],'quote':passage['text']} for k in benchmarks.BASIC_FIELDS[:-1]]},ensure_ascii=False)
    monkeypatch.setattr(deepseek_api, 'run', reply)
    return c, native, api


def batch(c, key='one'):
    r = c.post('/api/intake-batches', json={'request_key':key,'items':[{'input_kind':'url','input_value':'https://example.com/article','depth':'basic'}],
                                           'defaults':{'model':'codex-cli'}}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()['id']


def pending(c):
    r = c.get('/api/model-fallbacks')
    assert r.status_code == 200, r.text
    return r.json()['requests']


def test_no_api_before_consent_then_resume_only_analysis_and_keep_original(monkeypatch):
    c, native, api = setup(monkeypatch)
    bid = batch(c); drain()
    item = c.get(f'/api/intake-batches/{bid}').json()['items'][0]
    assert item['state'] == 'failed' and len(native) == 1 and not api
    requests = pending(c)
    assert len(requests) == 1 and requests[0]['target_model'] == 'deepseek-flash'
    rid = requests[0]['id']
    assert c.post(f'/api/model-fallbacks/{rid}/approve', json={}, headers=H).status_code == 400
    assert not api
    old = db.row('SELECT * FROM benchmarks WHERE id=?', (item['benchmark_id'],))
    approved = c.post(f'/api/model-fallbacks/{rid}/approve', json={'confirm':True}, headers=H)
    assert approved.status_code == 200, approved.text
    duplicate = c.post(f'/api/model-fallbacks/{rid}/approve', json={'confirm':True}, headers=H)
    assert duplicate.json()['job_id'] == approved.json()['job_id'] and duplicate.json()['reused']
    drain()
    current = c.get(f'/api/intake-batches/{bid}').json()['items'][0]
    assert current['state'] == 'done' and current['model'] == 'deepseek-flash'
    assert len(api) == 1 and api[0][0] == 'research-basic' and len(native) == 1
    saved = db.row('SELECT * FROM benchmarks WHERE id=?', (item['benchmark_id'],))
    assert (saved['body'],saved['body_hash']) == (old['body'],old['body_hash'])
    meta = db.loads(db.row('SELECT result FROM benchmark_analyses WHERE id=?', (current['analysis_id'],))['result'])['metadata']
    assert meta['backend'] == 'deepseek-api' and meta['fallback']['authorization_id'] == rid
    assert Settings.load().llm_backend == 'codex-cli' and not pending(c)
    # A future native-Codex batch must not reuse an authorized DeepSeek result as native output.
    second = batch(c, 'two'); drain()
    assert c.get(f'/api/intake-batches/{second}').json()['items'][0]['state'] == 'failed'
    assert len(native) == 2 and len(api) == 1 and len(pending(c)) == 1
    rid2 = pending(c)[0]['id']
    c.post(f'/api/model-fallbacks/{rid2}/approve',json={'confirm':True},headers=H).raise_for_status()
    drain()
    assert len(api) == 1  # Reuse identical, already verified DeepSeek research after consent.
    assert c.get(f'/api/intake-batches/{second}').json()['items'][0]['state'] == 'done'


def test_decline_and_cancellation_never_spend_api(monkeypatch):
    c, _, api = setup(monkeypatch)
    bid = batch(c); drain()
    rid = pending(c)[0]['id']
    assert c.post(f'/api/model-fallbacks/{rid}/decline', json={}, headers=H).status_code == 200
    assert c.post(f'/api/model-fallbacks/{rid}/approve', json={'confirm':True}, headers=H).status_code == 409
    assert not pending(c) and not api
    item = c.get(f'/api/intake-batches/{bid}').json()['items'][0]
    assert db.row('SELECT body FROM benchmarks WHERE id=?', (item['benchmark_id'],))['body'] == RAW
    monkeypatch.setattr(codex_native, 'run', lambda *a, **kw: (_ for _ in ()).throw(codex_native.CodexError('任务已停止')))
    with pytest.raises(llm.LlmUnavailable) as error:
        llm.run('测试', task='analyze')
    assert not isinstance(error.value, llm.ModelBackendUnavailable)


@pytest.mark.parametrize('change', ['source','pause','retry'])
def test_stale_requests_cannot_resume_changed_or_paused_tasks(monkeypatch, change):
    c, _, api = setup(monkeypatch)
    bid = batch(c); drain()
    request = pending(c)[0]
    item = c.get(f'/api/intake-batches/{bid}').json()['items'][0]
    if change == 'source':
        with db.tx() as conn:
            conn.execute('UPDATE benchmarks SET body=?,body_hash=? WHERE id=?', ('新原文',benchmarks.body_hash('新原文'),item['benchmark_id']))
    elif change == 'pause':
        with db.tx() as conn:conn.execute("UPDATE intake_items SET state='paused' WHERE id=?", (item['id'],))
    else:
        c.post(f'/api/intake-batches/{bid}/retry', json={'item_id':item['id']}, headers=H).raise_for_status()
    assert c.post(f'/api/model-fallbacks/{request["id"]}/approve', json={'confirm':True}, headers=H).status_code == 409
    assert not pending(c) and not api


def test_concurrent_approval_enqueues_once_and_missing_key_is_not_consent(monkeypatch):
    c, _, api = setup(monkeypatch)
    batch(c); drain()
    rid = pending(c)[0]['id']
    SecretStore().set(deepseek_api.SECRET_KEY, None)
    assert c.post(f'/api/model-fallbacks/{rid}/approve', json={'confirm':True}, headers=H).status_code == 400
    assert not api
    SecretStore().set(deepseek_api.SECRET_KEY, 'test-key')
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _:c.post(f'/api/model-fallbacks/{rid}/approve', json={'confirm':True}, headers=H), range(4)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()['job_id'] for r in responses}) == 1
    assert c.post(f'/api/model-fallbacks/{rid}/decline', json={}, headers=H).status_code == 409
    drain(); assert len(api) == 1


def test_deepseek_failure_is_visible_and_does_not_loop(monkeypatch):
    c, _, api = setup(monkeypatch)
    bid = batch(c); drain()
    rid = pending(c)[0]['id']
    def insufficient(*a, **kw):
        api.append('failed')
        raise RuntimeError('DeepSeek 账户余额不足（HTTP 402）')
    monkeypatch.setattr(deepseek_api, 'run', insufficient)
    c.post(f'/api/model-fallbacks/{rid}/approve', json={'confirm':True}, headers=H).raise_for_status()
    drain()
    assert len(api) == 1 and not pending(c)
    item = c.get(f'/api/intake-batches/{bid}').json()['items'][0]
    assert item['state'] == 'failed' and '余额不足' in item['error']
    assert db.row('SELECT body FROM benchmarks WHERE id=?', (item['benchmark_id'],))['body'] == RAW


def test_single_media_continues_distillation_without_downloading_or_transcribing_again(monkeypatch):
    c, _, api = setup(monkeypatch)
    mid = media.new('单项测试','文字')
    base = media.folder(mid); (base/'raw.txt').write_text(RAW,encoding='utf-8')
    media.write(mid,raw=RAW,stage=3,distiller='workbench')
    bridge_calls=[]
    def bridge(mid, mode):
        bridge_calls.append(mode)
        if mode=='clean':(base/'clean.txt').write_text(RAW,encoding='utf-8')
        elif mode=='quality':(base/'quality.json').write_text('[]',encoding='utf-8')
        else:pytest.fail('Must reuse the original extraction')
    monkeypatch.setattr(media,'bridge',bridge)
    media.queue(mid); drain()
    assert media.require(mid)['state']=='failed' and not api
    rid=pending(c)[0]['id']
    c.post(f'/api/model-fallbacks/{rid}/approve',json={'confirm':True},headers=H).raise_for_status()
    drain()
    result=media.require(mid)
    assert result['state']=='done' and result['article']==RAW and result['raw']==RAW
    assert result['model_info']['model']=='deepseek-v4-pro' and len(api)==1
    assert set(bridge_calls)=={'clean','quality'} and not pending(c)


def test_validation_download_and_image_failures_never_offer_text_api(monkeypatch):
    c, _, api = setup(monkeypatch)
    monkeypatch.setattr(codex_native,'run',lambda *a,**kw:'not JSON')
    batch(c); drain()
    assert not pending(c) and not api
    # An arbitrary exception carrying the word Codex is not a model failure signal.
    jobs._mark_failure('media_import', {'media_id':99999}, RuntimeError('Codex 下载失败'))
    jobs._mark_failure('video_plan', {'draft_id':99999}, llm.ModelBackendUnavailable('本机不可用',backend='codex-cli',model='test',task='vision'))
    assert not pending(c) and not api


def test_batch_file_distillation_and_following_research_share_only_item_consent(monkeypatch):
    c, native, api = setup(monkeypatch)
    uploaded = c.post('/api/intake-batches/upload', files={'file':('transcript.txt',RAW.encode(),'text/plain')}, headers=H).json()
    mid = int(uploaded['input_value'])
    base = media.folder(mid)
    def bridge(mid, mode):
        if mode=='clean':(base/'clean.txt').write_text(RAW,encoding='utf-8')
        elif mode=='quality':(base/'quality.json').write_text('[]',encoding='utf-8')
        else:pytest.fail('Original transcription should be reused')
    monkeypatch.setattr(media,'bridge',bridge)
    created=c.post('/api/intake-batches',json={'request_key':'file-one','items':[{**uploaded,'depth':'basic'}], 'defaults':{'model':'codex-cli'}},headers=H)
    assert created.status_code==200,created.text
    bid=created.json()['id'];drain()
    assert len(native)==1 and not api
    rid=pending(c)[0]['id']
    c.post(f'/api/model-fallbacks/{rid}/approve',json={'confirm':True},headers=H).raise_for_status()
    drain()
    item=c.get(f'/api/intake-batches/{bid}').json()['items'][0]
    assert item['state']=='done' and len(api)==2
    assert media.require(mid)['raw']==RAW and media.require(mid)['state']=='done'
    assert all(task=='research-basic' for task,_ in api)


def test_missing_local_cli_is_eligible_and_restart_does_not_replay_paid_call(monkeypatch):
    from content_workbench import model_fallbacks
    c, _, api = setup(monkeypatch)
    monkeypatch.setattr(codex_native,'run',lambda *a,**kw:(_ for _ in ()).throw(FileNotFoundError('private installation path')))
    batch(c);drain()
    request=pending(c)[0]
    assert '无法启动' in request['reason'] and 'private installation path' not in request['reason']
    approved=c.post(f'/api/model-fallbacks/{request["id"]}/approve',json={'confirm':True},headers=H).json()
    with db.tx() as conn:conn.execute("UPDATE jobs SET state='running' WHERE id=?",(approved['job_id'],))
    model_fallbacks.stop_interrupted()
    assert db.row('SELECT state FROM jobs WHERE id=?',(approved['job_id'],))['state']=='failed'
    assert not api and not pending(c)


def test_standalone_research_uses_its_existing_source_and_authorization(monkeypatch):
    c, native, api = setup(monkeypatch)
    source=c.post('/api/benchmarks',json={'title':'单独研究','body':RAW,'author':'原作者'},headers=H).json()
    started=c.post(f'/api/benchmarks/{source["id"]}/analyze',json={'depth':'basic','model':'codex-cli'},headers=H)
    assert started.status_code==200,started.text
    drain()
    request=pending(c)[0]
    assert request['task']=='原文研究' and len(native)==1 and not api
    approved=c.post(f'/api/model-fallbacks/{request["id"]}/approve',json={'confirm':True},headers=H)
    assert approved.status_code==200,approved.text
    drain()
    result=c.get(f'/api/benchmarks/{source["id"]}').json()
    assert result['body']==RAW and result['analysis_state']=='done' and len(api)==1
    assert not pending(c)

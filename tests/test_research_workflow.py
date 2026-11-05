"""Research evidence, routing and human decisions tested without paid calls."""
import json
import pytest
from content_workbench import benchmarks, db, llm, research_protocol
from content_workbench.config import Settings, SecretStore
from test_app import client, login, ORIGIN
from test_writing_workspace import report

H={'Origin':ORIGIN}


def source():
    c=client();login(c)
    item=c.post('/api/benchmarks',headers=H,json={'title':'研究样例','body':'先展示真实成果。\n\n接着解释过程。\n\n仅一次任务，不能泛化。'}).json()
    return c,item


def basic(item):
    part=benchmarks.passages(item['body'])[0]
    return {**{k:'结合原文研究，受众属于推测。' for k in benchmarks.BASIC_FIELDS},'basis':[{'field':k,'paragraph_key':part['key'],'quote':part['text']} for k in benchmarks.BASIC_FIELDS[:-1]]}


@pytest.mark.parametrize('depth',['basic','deep'])
def test_research_uses_frozen_rules_and_model_and_retains_previous_version(monkeypatch,depth):
    c,item=source();bid=item['id']
    SecretStore().set('deepseek_api_key','test-only-not-a-key')
    queued=c.post(f'/api/benchmarks/{bid}/analyze',headers=H,json={'depth':depth,'model':'deepseek-flash'}).json()
    job=db.row('SELECT * FROM jobs WHERE id=?',(queued['job_id'],));payload=json.loads(job['payload'])
    Settings(llm_backend='claude-code',llm_model='opus').save()
    calls=[]
    def fake(prompt,**kw):
        assert payload['route']['rules'] in prompt
        calls.append(kw)
        assert kw['task']=='research-basic' and kw['configuration'].llm_backend=='deepseek-api'
        return json.dumps(basic(item) if depth=='basic' else report(item),ensure_ascii=False)
    monkeypatch.setattr(llm,'run',fake)
    benchmarks.analyze(Settings(),payload)
    result=c.get(f'/api/benchmarks/{bid}').json()
    assert result['analysis']['metadata']['model']=='deepseek-flash'
    assert result['analysis']['metadata']['rule_hash']==research_protocol.RULE_HASH
    assert result['body']==item['body'] and result['reviews']==[]
    assert len(result['versions'])==1 and len(calls)==1
    first=result['analysis']['id']
    q=c.post(f'/api/benchmarks/{bid}/analyze',headers=H,json={'depth':depth,'model':'deepseek-flash'}).json()
    benchmarks.analyze(Settings(),json.loads(db.row('SELECT payload FROM jobs WHERE id=?',(q['job_id'],))['payload']))
    assert len(c.get(f'/api/benchmarks/{bid}').json()['versions'])==2
    assert c.get(f'/api/benchmarks/{bid}?analysis_id={first}').json()['analysis']['id']==first


def test_unknown_model_or_depth_rejected_without_job():
    c,item=source()
    for body in ({'model':'pretend-codex'},{'depth':'archive'}):
        assert c.post(f'/api/benchmarks/{item["id"]}/analyze',headers=H,json=body).status_code==400
    assert db.rows('SELECT * FROM jobs')==[]


def test_basic_evidence_must_match_real_passage_and_cover_fields():
    _,item=source();value=basic(item)
    assert benchmarks.validate_basic(value,item)==value
    value['basis'][0]['quote']='编造的话'
    with pytest.raises(Exception,match='逐字'):benchmarks.validate_basic(value,item)
    value=basic(item);value['basis']=value['basis'][:1]
    with pytest.raises(Exception,match='分别关联'):benchmarks.validate_basic(value,item)


def test_judgment_history_preserves_legacy_and_is_version_scoped():
    c,item=source();bid=item['id'];aid=benchmarks.store_research(bid,report(item),item['body_hash'])
    path=f'/api/benchmarks/{bid}/analyses/{aid}/reviews/__report__'
    with db.tx() as conn:conn.execute('INSERT INTO benchmark_reviews(analysis_id,paragraph_key,note,useful,reviewed,updated_at) VALUES(?,?,?,?,?,?)',(aid,'__report__','旧判断',0,0,db.now()))
    assert c.put(path,headers=H,json={'decision':'partial','judgment':{'agree':'可借结构','verify':'效果仍待测试'}}).status_code==200
    assert c.put(path,headers=H,json={'decision':'confirmed','judgment':{'agree':'仅适用于实操教程'}}).status_code==200
    events=c.get(f'/api/benchmarks/{bid}/analyses/{aid}/reviews/history').json()
    assert len(events)==3 and json.loads(events[-1]['record'])['note']=='旧判断'
    fresh=benchmarks.store_research(bid,report(item),item['body_hash'])
    assert c.get(f'/api/benchmarks/{bid}?analysis_id={fresh}').json()['reviews']==[]
    assert c.get(f'/api/benchmarks/{bid}?analysis_id={aid}').json()['reviews'][0]['note']=='旧判断'
    assert c.put(path,headers=H,json={'decision':'magic'}).status_code==400


def test_syntax_repair_keeps_same_model_route(monkeypatch):
    SecretStore().set('deepseek_api_key','test-only-not-a-key')
    route=research_protocol.resolve('deepseek-flash')
    replies=iter(['not json','{"fixed":true}']);calls=[]
    monkeypatch.setattr(llm,'run',lambda *a,**kw:(calls.append(kw),next(replies))[1])
    assert benchmarks._structured('input',route=route)=={'fixed':True}
    assert [call['task'] for call in calls]==['research-basic','research-basic']


def test_external_import_records_caller_model_without_inventing_old_metadata():
    c,item=source();path=f'/api/benchmarks/{item["id"]}/research/import'
    context={'model':'caller-model','rule_version':research_protocol.VERSION,'rule_hash':research_protocol.RULE_HASH}
    value={'body_hash':item['body_hash'],'research':report(item),'context':context}
    result=c.post(path,headers=H,json=value)
    assert result.status_code==200
    assert result.json()['analysis']['metadata']['model_verification']=='caller-reported'
    context['rule_hash']='outdated'
    assert c.post(path,headers=H,json=value).status_code==409
    assert len(c.get(f'/api/benchmarks/{item["id"]}').json()['versions'])==1

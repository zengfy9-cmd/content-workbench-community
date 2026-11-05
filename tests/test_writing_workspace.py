import json
import pytest
from content_workbench import benchmarks, db, workspace_features as workspace, references
from content_workbench.textutil import markdown_parser, wechat_html
from test_app import client, login, ORIGIN

H={'Origin':ORIGIN}


def report(source):
    key=benchmarks.passages(source['body'])[0]['key']
    return {'audience':'持续做内容的人，采集与写作分散。','thesis':'作者认为应串起资料处理与审核。','purpose':'推测：引导读者尝试方法；依据为结尾行动说明。','emotion':'从疲惫到希望，依据为前后叙述。','evidence':'作者自述，未独立验证。','golden_quotes':[],
            'sections':[{'location':'开头','summary':'说明问题','role':'建立共鸣','technique':'具体动作呈现，数据仍待验证','paragraph_key':key}],
            'reference_value':'可借流程呈现方式。','writing_prep':'AI建议：补自己的事实，再填写提纲。'}


def setup():
    c=client();login(c)
    source=c.post('/api/benchmarks',headers=H,json={'title':'原文','body':'这是足够完整的真实原文。'*10,'source':'web','original_html':'<article><h2 style="color:#123456">原文</h2><p>实际正文</p><script>alert(1)</script></article>','read_count':456}).json()
    return c,source


def test_plans_save_edit_start_idempotently_and_keep_references():
    c,source=setup()
    result=c.post('/api/plans',headers=H,json={'day':'2026-10-30','title':'明天的真实任务','angle':'自己的角度','benchmark_ids':[source['id']]}).json()
    pid=result['id']
    assert c.patch(f'/api/plans/{pid}',headers=H,json={'day':'bad'}).status_code==400
    assert c.patch(f'/api/plans/{pid}',headers=H,json={'title':'修改标题','angle':'更清楚的角度','day':'2026-10-29'}).status_code==200
    first=c.post(f'/api/plans/{pid}/start',headers=H,json={}).json()
    assert c.post(f'/api/plans/{pid}/start',headers=H,json={}).json()==first
    draft=c.get(f'/api/drafts/{first["id"]}').json()
    assert draft['title']=='修改标题' and draft['body']=='' and draft['notes']=='更清楚的角度'
    assert c.get(f'/api/drafts/{first["id"]}/benchmarks').json()[0]['benchmark_id']==source['id']
    assert len(c.get('/api/plans').json())==1
    assert c.post('/api/plans',headers=H,json={'day':'2026-10-29','title':'坏引用','benchmark_ids':[99999]}).status_code==400
    assert c.post('/api/plans',json={'title':'未授权'}).status_code==403


def test_original_sanitization_and_read_count():
    c,source=setup()
    assert '<h2' in source['original_html'] and '#123456' in source['original_html']
    assert 'script' not in source['original_html'] and 'alert' not in source['original_html']
    assert c.get('/api/benchmarks').json()[0]['read_count']==456
    assert source['read_count_at']>0
    assert c.put(f'/api/benchmarks/{source["id"]}',headers=H,json={'read_count':-1}).status_code==400
    changed=c.put(f'/api/benchmarks/{source["id"]}',headers=H,json={'read_count':789}).json()
    assert changed['body']==source['body'] and changed['read_count']==789
    html=references.safe_original('<p onclick="evil()" style="color:expression(alert(1));text-align:center">正文</p><img src="http://127.0.0.1/private"><a href="javascript:evil()">链接</a>')
    assert 'onclick' not in html and 'expression' not in html and '127.0.0.1' not in html and 'javascript' not in html and 'text-align:center' in html


def test_research_import_is_versioned_and_matches_current_source():
    c,source=setup();value=report(source)
    imported=c.post(f'/api/benchmarks/{source["id"]}/research/import',headers=H,json={'body_hash':source['body_hash'],'research':value})
    assert imported.status_code==200,imported.text
    version=imported.json()['analysis']['id']
    review=c.put(f'/api/benchmarks/{source["id"]}/analyses/{version}/reviews/__report__',headers=H,json={'note':'我认同需要审核；效果还需验证。'})
    assert review.status_code==200 and review.json()['note'].startswith('我认同')
    assert imported.json()['analysis']['research']==value
    assert c.post(f'/api/benchmarks/{source["id"]}/research/import',headers=H,json={'body_hash':'wrong','research':value}).status_code==409
    value['golden_quotes']=['这句不在原文里']
    assert c.post(f'/api/benchmarks/{source["id"]}/research/import',headers=H,json={'body_hash':source['body_hash'],'research':value}).status_code==400
    assert c.get(f'/api/benchmarks/{source["id"]}').json()['analysis']['id']==version


def test_hot_list_cache_top_five_and_story_ids_only_from_feed(monkeypatch):
    c=client();login(c)
    calls=[]
    def remote(name,args):
        calls.append((name,args))
        if name.endswith('hot_topics'):
            return '\n'.join(f'第 {i} 名：[热点{i}](https://aihot.news/items/event{i})\n信源：原始资料 · 最新进展\n来龙去脉：public_id=story-{i}' for i in range(1,11))
        return '事件详情与来源。'
    monkeypatch.setattr(workspace,'_mcp',remote)
    data=c.post('/api/news/refresh',headers=H,json={}).json()
    assert len(data['items'])==5 and data['items'][0]['rank']==1
    assert c.get('/api/news/hot').json()['updated_at']==data['updated_at'] and len(calls)==1
    assert c.get('/api/news/stories/fabricated').status_code==404
    assert c.get('/api/news/stories/story-1').json()['body']=='事件详情与来源。'
    assert c.get('/api/news/stories/story-1').status_code==200 and len(calls)==2
    monkeypatch.setattr(workspace,'_mcp',lambda *a: (_ for _ in ()).throw(ValueError('offline')))
    assert c.post('/api/news/refresh',headers=H,json={}).status_code==502
    assert len(c.get('/api/news/hot').json()['items'])==5


def test_centered_paragraph_keeps_surrounding_text_and_rejects_html():
    html=markdown_parser().render('前文\n\n:::center\n中间 **加粗**\n:::\n\n后文\n\n<script>bad()</script>')
    assert '<p style="text-align:center">中间 <strong>加粗</strong></p>' in html
    assert '<p>前文</p>' in html and '<p>后文</p>' in html
    assert '<script>' not in html
    from content_workbench.textutil import normalize_emphasis
    marked=markdown_parser().render(normalize_emphasis('**==局部文字==**后面的正文'))
    assert '<mark><strong>局部文字</strong></mark>后面的正文' in marked


def test_research_generation_uses_explicit_analysis_route_and_keeps_old_versions(monkeypatch):
    from content_workbench import llm
    c,source=setup();value=report(source)
    calls=[]
    def model(prompt,**kwargs):
        calls.append(kwargs)
        assert '全文研究报告' in prompt
        return json.dumps(value,ensure_ascii=False)
    monkeypatch.setattr(llm,'run',model)
    queued=c.post(f'/api/benchmarks/{source["id"]}/analyze',headers=H,json={'format':'research'}).json()
    job=db.row('SELECT * FROM jobs WHERE id=?',(queued['job_id'],))
    benchmarks.analyze(None,db.loads(job['payload'],{}))
    result=c.get(f'/api/benchmarks/{source["id"]}').json()
    assert result['analysis']['research']==value and result['analysis_state']=='done'
    assert calls[0]['task']=='analyze' and calls[0]['json_output']


def test_schedule_existing_draft_keeps_text_references_and_reuses_active_plan():
    c,source=setup()
    did=c.post('/api/drafts',headers=H,json={'platform':'wechat','title':'原来的稿子','body':'完整原稿，不能被安排日期覆盖。'}).json()['id']
    c.post(f'/api/benchmarks/{source["id"]}/draft',headers=H,json={'draft_id':did})
    before=c.get(f'/api/drafts/{did}').json()
    payload={'day':'2026-10-29','title':'原来的稿子','draft_id':did,'angle':'自己的下一步'}
    first=c.post('/api/plans',headers=H,json=payload)
    assert first.status_code==200,first.text
    pid=first.json()['id']
    repeated=c.post('/api/plans',headers=H,json={**payload,'day':'2026-10-30','angle':''}).json()
    assert repeated['existing'] and repeated['id']==pid
    plans=c.get('/api/plans').json()
    assert len(plans)==1 and plans[0]['day']=='2026-10-30'
    assert plans[0]['angle']=='自己的下一步' and plans[0]['benchmark_ids']==[source['id']]
    assert c.post(f'/api/plans/{pid}/start',headers=H,json={}).json()=={'id':did}
    assert c.get(f'/api/drafts/{did}').json()==before
    assert len(c.get('/api/drafts').json())==1
    # Larger boards and moving an item before the first one remain valid.
    assert c.patch(f'/api/plans/{pid}',headers=H,json={'position':15000}).status_code==200
    assert c.patch(f'/api/plans/{pid}',headers=H,json={'position':-1}).status_code==200
    assert c.post('/api/plans',headers=H,json={**payload,'draft_id':99999}).status_code==400


def test_missing_linked_draft_does_not_silently_create_a_copy():
    c=client();login(c)
    did=c.post('/api/drafts',headers=H,json={'platform':'wechat','title':'将被删除的稿件'}).json()['id']
    pid=c.post('/api/plans',headers=H,json={'day':'2026-10-29','title':'原稿安排','draft_id':did}).json()['id']
    with db.tx() as conn:
        conn.execute('DELETE FROM drafts WHERE id=?',(did,))
    assert c.post(f'/api/plans/{pid}/start',headers=H,json={}).status_code==409
    assert c.get('/api/drafts').json()==[]


def test_tips_quick_capture_edit_pin_archive_and_restore_without_model_calls(monkeypatch):
    from content_workbench import llm
    monkeypatch.setattr(llm,'run',lambda *a,**kw: pytest.fail('Recording tips must not call a model'))
    c=client();login(c)
    assert c.post('/api/tips',json={'content':'未授权'}).status_code==403
    saved=c.post('/api/tips',headers=H,json={'content':'从真实问题里找选题','origin':'AI建议','source_url':'https://example.com/article'}).json()
    assert saved['title']=='从真实问题里找选题' and saved['validation']=='待验证'
    assert saved['feedback']=='' and saved['category']=='选题'
    tid=saved['id']
    c.post('/api/tips',headers=H,json={'content':'后一条技巧'})
    edited=c.patch(f'/api/tips/{tid}',headers=H,json={'pinned':True,'category':'开头','steps':'先说真实处境','example':'自己的例子','feedback':'单次使用，仍待验证'}).json()
    assert edited['pinned'] and edited['steps']=='先说真实处境' and edited['validation']=='待验证'
    assert c.get('/api/tips').json()[0]['id']==tid
    assert c.patch(f'/api/tips/{tid}',headers=H,json={'archived':True}).json()['archived']==1
    assert c.patch(f'/api/tips/{tid}',headers=H,json={'archived':False}).json()['content']==saved['content']
    assert c.get('/api/ideas').json()==[]
    for bad in ({'content':''},{'category':'未知'},{'source_url':'javascript:alert(1)'},{'pinned':'false'},{'content':[]}):
        assert c.patch(f'/api/tips/{tid}',headers=H,json=bad).status_code==400
    assert c.post('/api/tips',headers=H,json={'content':'   '}).status_code==400
    assert c.patch('/api/tips/99999',headers=H,json={'pinned':True}).status_code==404

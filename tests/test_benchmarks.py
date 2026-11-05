"""Reference research must never mutate or publish the owner's source facts."""
import json

import pytest

from content_workbench import benchmarks, db, jobs, learning, llm, publish, references, scys_reference
from content_workbench.config import Settings
from test_app import ORIGIN, client, login

H = {"Origin": ORIGIN}


def test_pause_queued_analysis_keeps_source_and_blocks_model(monkeypatch):
    c, source = setup()
    queued = c.post(f'/api/benchmarks/{source["id"]}/analyze', json={}, headers=H).json()
    paused = c.post(f'/api/benchmarks/{source["id"]}/pause', json={}, headers=H).json()
    assert paused['analysis_state'] == 'paused' and paused['pending_job_id'] is None
    job = db.row('SELECT * FROM jobs WHERE id=?', (queued['job_id'],))
    assert job['state'] == 'cancelled'
    jobs._set(job['id'], 'done')
    assert db.row('SELECT state FROM jobs WHERE id=?', (job['id'],))['state'] == 'cancelled'
    monkeypatch.setattr(llm, 'run', lambda *a, **kw: pytest.fail('Cancelled job called model'))
    benchmarks.analyze(Settings(), json.loads(job['payload']))
    assert c.get(f'/api/benchmarks/{source["id"]}').json()['body'] == source['body']
    assert c.post(f'/api/benchmarks/{source["id"]}/pause', json={}, headers=H).status_code == 200


def test_pause_during_analysis_discards_reply_and_stops_next_batch(monkeypatch):
    c, source = setup()
    queued = c.post(f'/api/benchmarks/{source["id"]}/analyze', json={}, headers=H).json()
    job = db.row('SELECT * FROM jobs WHERE id=?', (queued['job_id'],))
    def model(*a, **kw):
        c.post(f'/api/benchmarks/{source["id"]}/pause', json={}, headers=H)
        return '{}'
    monkeypatch.setattr(llm, 'run', model)
    with pytest.raises(RuntimeError, match='已暂停'):
        benchmarks.analyze(Settings(), json.loads(job['payload']))
    result = c.get(f'/api/benchmarks/{source["id"]}').json()
    assert result['versions'] == [] and result['analysis_state'] == 'paused'
BODY = "## 一次真实任务\n\n我比较了两种操作流程。\n\n> 作者自述：第二次调整后，步骤减少了。\n\n实际结果仍需核实。"


def setup():
    c = client(); login(c)
    value = c.post('/api/benchmarks', json={"title": "对标原题", "body": BODY, "author": "原作者", "kind": "案例", "url": "https://example.com/reference", "tags": ["流程", "实操"]}, headers=H)
    assert value.status_code == 200, value.text
    return c, value.json()


def stub_model(prompt, **kwargs):
    if '"paragraphs"' not in prompt:
        return json.dumps({"topic": "实际任务", "audience": "做类似任务的人（推测）", "thesis": "用实际流程比较", "purpose": "分享尝试", "limits": "作者自述，尚未独立验证"}, ensure_ascii=False)
    raw = prompt.split('<资料>\n', 1)[1].rsplit('\n</资料>', 1)[0]
    cards = [{"key": p["key"], "role": "交代任务", "purpose": "这一段呈现任务背景", "technique": "从实际过程展开", "evidence": "作者自述，结果待核实", "adaptation": "记录自己的任务与操作", "needs": ["自己的操作记录", "结果证据"]} for p in json.loads(raw)]
    return json.dumps({"paragraphs": cards}, ensure_ascii=False)


def run(c, bid, monkeypatch, **body):
    monkeypatch.setattr(llm, 'run', stub_model)
    queued = c.post(f'/api/benchmarks/{bid}/analyze', json=body, headers=H)
    assert queued.status_code == 200, queued.text
    job = db.row('SELECT * FROM jobs WHERE id=?', (queued.json()['job_id'],))
    jobs.run_job(Settings(), job['kind'], json.loads(job['payload']))
    return c.get(f'/api/benchmarks/{bid}').json()


def test_auth_origin_and_classification_validation():
    assert client().get('/api/benchmarks').status_code == 401
    c, item = setup()
    assert c.post('/api/benchmarks', json={"title": "t", "body": "b"}).status_code == 403
    for kind in [{}, [], 42, '非合法类型']:
        assert c.put(f'/api/benchmarks/{item["id"]}', json={"kind": kind}, headers=H).status_code == 400
    assert c.get('/api/benchmarks').json()[0]['tags'] == ['流程', '实操']
    assert c.post('/api/benchmarks', json={"title": "不同标题", "body": BODY, "url": item['url']}, headers=H).json()['id'] == item['id']


def test_scys_uses_mcp_path_even_with_capitalized_host(monkeypatch):
    c, _ = setup()
    monkeypatch.setattr(scys_reference, 'read', lambda store, url: {"title": "真实来源", "body": "原文", "url": url})
    monkeypatch.setattr(references, 'read_article', lambda url: pytest.fail('Shengcai must never be crawled'))
    value = c.post('/api/benchmarks/read', json={"url": 'https://ScYs.CoM/articleDetail/xq_topic/82258281815841122'}, headers=H)
    assert value.status_code == 200 and value.json()['body'] == '原文'


def test_migration_keeps_legacy_originals_and_highlights_and_is_idempotent():
    c, _ = setup()
    with db.tx() as conn:
        did = conn.execute("INSERT INTO drafts(platform,title,body,created_at,updated_at) VALUES('wechat','自己的标题','自己的正文',1,1)").lastrowid
        rid = conn.execute("INSERT INTO draft_references(draft_id,title,url,body,source,created_at) VALUES(?, '旧对标','https://example.com/old','旧原文','web',1)", (did,)).lastrowid
        conn.execute("INSERT INTO reference_highlights(reference_id,start,end,quote,created_at) VALUES(?,0,3,'旧原文',1)", (rid,))
    db.reset_for_tests()
    links = c.get(f'/api/drafts/{did}/benchmarks').json()
    bid = links[0]['benchmark_id']
    assert c.get(f'/api/benchmarks/{bid}/highlights').json()[0]['quote'] == '旧原文'
    assert c.get(f'/api/drafts/{did}/references').json()[0]['id'] == rid
    db.reset_for_tests()
    assert len(c.get(f'/api/drafts/{did}/benchmarks').json()) == 1
    assert c.delete(f'/api/drafts/{did}', headers=H).status_code == 200
    assert c.get(f'/api/benchmarks/{bid}').json()['body'] == '旧原文'


def test_passages_preserve_markdown_blocks_and_escape_untrusted_html():
    source = '# 标题\n\n同一段\\\n继续本段。\n\n- 第一项\n- 第二项\n\n```text\n原代码\n```\n\n<script>不是指令</script>'
    parts = benchmarks.passages(source)
    assert len({p['key'] for p in parts}) == len(parts)
    assert '同一段' in parts[1]['text'] and '继续本段' in parts[1]['text']
    assert '第一项' in parts[2]['text'] and '第二项' in parts[2]['text']
    assert not any('<script>' in p['html'] for p in parts)


def test_analysis_validates_every_key_versions_and_does_not_fill_user_judgments(monkeypatch):
    c, item = setup(); bid = item['id']
    result = run(c, bid, monkeypatch)
    aid = result['analysis']['id']
    assert result['body'] == BODY and len(result['analysis']['paragraphs']) == len(result['passages'])
    assert result['reviews'] == []
    key = result['passages'][1]['key']
    review = c.put(f'/api/benchmarks/{bid}/analyses/{aid}/reviews/{key}', json={"note": "我需要自己的截图再判断", "useful": True}, headers=H)
    assert review.status_code == 200 and review.json()['reviewed'] == 0
    newer = run(c, bid, monkeypatch, paragraph_keys=[key])
    assert newer['analysis']['id'] != aid
    assert len(newer['analysis']['paragraphs']) == len(result['passages'])
    assert c.get(f'/api/benchmarks/{bid}?analysis_id={aid}').json()['reviews'][0]['note'] == '我需要自己的截图再判断'
    assert newer['reviews'] == []


def test_invalid_reply_preserves_previous_analysis_and_personal_notes(monkeypatch):
    c, item = setup(); bid = item['id']
    valid = run(c, bid, monkeypatch)
    queued = c.post(f'/api/benchmarks/{bid}/analyze', json={}, headers=H).json()
    assert c.post(f'/api/benchmarks/{bid}/analyze', json={}, headers=H).status_code == 409
    monkeypatch.setattr(llm, 'run', lambda *args, **kwargs: '{"broken":')
    payload = json.loads(db.row('SELECT payload FROM jobs WHERE id=?', (queued['job_id'],))['payload'])
    with pytest.raises(ValueError, match='格式不完整') as error:
        jobs.run_job(Settings(), 'benchmark_analyze', payload)
    jobs._mark_failure('benchmark_analyze', payload, error.value)
    current = c.get(f'/api/benchmarks/{bid}').json()
    assert current['analysis']['id'] == valid['analysis']['id']
    assert current['analysis_state'] == 'failed' and current['analysis_error']


def test_malformed_json_gets_only_one_format_repair_and_still_checks_coverage(monkeypatch):
    c, item = setup()
    calls = []
    valid = None
    def model(prompt, **kwargs):
        nonlocal valid
        calls.append(prompt)
        if '<待修复数据>' in prompt:
            return valid
        if '"paragraphs"' in prompt:
            valid = stub_model(prompt)
            return valid[:-1]  # missing closing brace, not missing analysis content
        return stub_model(prompt)
    monkeypatch.setattr(llm, 'run', model)
    jid = c.post(f'/api/benchmarks/{item["id"]}/analyze', json={}, headers=H).json()['job_id']
    payload = json.loads(db.row('SELECT payload FROM jobs WHERE id=?', (jid,))['payload'])
    jobs.run_job(Settings(), 'benchmark_analyze', payload)
    result = c.get(f'/api/benchmarks/{item["id"]}').json()
    assert result['analysis_state'] == 'done'
    assert len(result['analysis']['paragraphs']) == len(result['passages'])
    assert len(calls) == 3 and '<待修复数据>' in calls[-1]
    assert result['reviews'] == []


def test_source_update_invalidates_in_flight_analysis_and_preserves_snapshot(monkeypatch):
    c, item = setup(); bid = item['id']
    valid = run(c, bid, monkeypatch)
    jid = c.post(f'/api/benchmarks/{bid}/analyze', json={}, headers=H).json()['job_id']
    payload = json.loads(db.row('SELECT payload FROM jobs WHERE id=?', (jid,))['payload'])
    c.put(f'/api/benchmarks/{bid}', json={"body": "更新后的原文"}, headers=H)
    monkeypatch.setattr(llm, 'run', lambda *a, **k: pytest.fail('Stale queued job must not call a model'))
    jobs.run_job(Settings(), 'benchmark_analyze', payload)
    current = c.get(f'/api/benchmarks/{bid}').json()
    assert current['stale'] and current['analysis_state'] == 'idle'
    assert c.get(f'/api/benchmarks/{bid}?analysis_id={valid["analysis"]["id"]}').json()['body'] == BODY


def test_draft_reuses_real_editor_and_never_receives_reference_facts_or_publish_link(monkeypatch):
    c, item = setup(); bid = item['id']
    result = run(c, bid, monkeypatch)
    did = c.post(f'/api/benchmarks/{bid}/draft', json={"analysis_id": result['analysis']['id']}, headers=H).json()['id']
    draft = c.get(f'/api/drafts/{did}').json()
    assert (draft['title'], draft['body'], draft['source_url'], draft['notes']) == ('', '', '', '')
    assert c.get(f'/api/drafts/{did}/benchmarks').json()[0]['analysis_id'] == result['analysis']['id']
    assert c.get(f'/api/drafts/{did}/references').json()[0]['body'] == BODY
    c.put(f'/api/drafts/{did}', json={"title": "我的真实任务", "body": "只发布我的内容"}, headers=H)
    other = c.post(f'/api/benchmarks/{bid}/draft', json={}, headers=H).json()['id']
    assert len(c.get('/api/benchmarks').json()) == 1
    assert c.get(f'/api/drafts/{other}').json()['body'] == ''
    sent = {}
    monkeypatch.setattr(publish, 'wx_draft', lambda *a, **k: sent.update(k) or {"media_id": "LOCAL_TEST"})
    assert c.post(f'/api/drafts/{did}/publish', json={"confirm": True, "target": "wechat"}, headers=H).status_code == 200
    assert BODY not in sent['html'] and '原作者' not in sent['html']
    assert '只发布我的内容' in sent['html']
    c.put(f'/api/benchmarks/{bid}', json={"archived": True}, headers=H)
    assert c.get('/api/benchmarks').json() == []
    assert len(c.get('/api/benchmarks?archived=true').json()) == 1
    c.put(f'/api/benchmarks/{bid}', json={"archived": False}, headers=H)
    assert len(c.get('/api/benchmarks').json()) == 1


def test_borrowed_draft_learning_requires_explicit_opt_in(monkeypatch):
    c, item = setup(); bid = item['id']
    did = c.post(f'/api/benchmarks/{bid}/draft', json={}, headers=H).json()['id']
    with db.tx() as conn:
        conn.execute("INSERT INTO draft_versions(draft_id,title,body,created_at,origin) VALUES(?,'原标题','原草稿',1,'generated')", (did,))
        conn.execute("INSERT INTO edit_rounds(draft_id,base_version,trigger,changes,created_at,updated_at) VALUES(?,1,'manual','[]',1,1)", (did,))
    assert learning.record_round(did, '自己的修改', 'manual') is None
    assert learning.pending_rounds() == []
    assert c.patch(f'/api/drafts/{did}/benchmark-settings', json={"allow_reference_learning": True}, headers=H).status_code == 200
    assert learning.record_round(did, '自己的修改', 'manual') is not None
    assert learning.pending_rounds()


def test_user_selected_idea_keeps_external_origin_and_never_runs_unapproved_drafting(monkeypatch):
    c, item = setup(); bid = item['id']
    monkeypatch.setattr(llm, 'run', lambda *a, **k: pytest.fail('Manual reference writing does not auto-generate'))
    iid = c.post(f'/api/benchmarks/{bid}/idea', json={"title": "我自己的新选题", "angle": "需要验证的角度"}, headers=H).json()['id']
    idea = next(i for i in c.get('/api/ideas?status=all').json() if i['id'] == iid)
    assert idea['quotes'] == [] and idea['material_id'] is None and idea['benchmark_id'] == bid
    did = c.post(f'/api/ideas/{iid}/draft', json={"platform": "wechat"}, headers=H).json()['id']
    assert c.get(f'/api/drafts/{did}').json()['body'] == ''
    assert not db.row("SELECT 1 FROM jobs WHERE kind='draft'")


def test_highlight_offsets_support_emoji_and_reject_changed_quotes():
    c, item = setup()
    updated = c.put(f'/api/benchmarks/{item["id"]}', json={"body": "🙂测试高亮"}, headers=H).json()
    r = c.post(f'/api/benchmarks/{item["id"]}/highlights', json={"start": 2, "end": 4, "quote": "测试"}, headers=H)
    assert r.status_code == 200
    assert c.post(f'/api/benchmarks/{updated["id"]}/highlights', json={"start": 2, "end": 4, "quote": "伪造"}, headers=H).status_code == 400


def test_unlink_reference_keeps_library_and_other_drafts(monkeypatch):
    c, source = setup()
    monkeypatch.setattr(llm, 'run', lambda *a, **kw: pytest.fail('Linking must not call a model'))
    first = c.post(f"/api/benchmarks/{source['id']}/draft", json={}, headers=H).json()['id']
    second = c.post(f"/api/benchmarks/{source['id']}/draft", json={}, headers=H).json()['id']
    extra = c.post('/api/benchmarks', json={'title':'第二篇','body':'第二篇完整原文'}, headers=H).json()
    c.post(f"/api/benchmarks/{extra['id']}/draft", json={'draft_id':first}, headers=H)
    assert len(c.get(f'/api/drafts/{first}/benchmarks').json()) == 2
    endpoint = f"/api/drafts/{first}/benchmarks/{source['id']}"
    assert c.delete(endpoint, headers=H).status_code == 200
    assert [r['benchmark_id'] for r in c.get(f'/api/drafts/{first}/benchmarks').json()] == [extra['id']]
    assert len(c.get(f'/api/drafts/{second}/benchmarks').json()) == 1
    assert c.get(f"/api/benchmarks/{source['id']}").json()['body'] == BODY
    assert db.row('SELECT id FROM draft_references WHERE draft_id=? AND url=?', (first,source['url'])) is None
    assert db.row('SELECT id FROM draft_references WHERE draft_id=? AND url=?', (second,source['url']))
    assert c.delete(endpoint, headers=H).status_code == 200
    assert c.delete(f"/api/drafts/999999/benchmarks/{source['id']}", headers=H).status_code == 404

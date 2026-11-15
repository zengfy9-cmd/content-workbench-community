import json
from types import SimpleNamespace

import httpx
import pytest

from content_workbench import db, deepseek_api, jobs, llm, model_api
from content_workbench.config import Settings, data_dir
from test_app import ORIGIN, client, login

H = {'Origin': ORIGIN}
ARTICLE = {'title': '安装教程', 'body': '正文\n\n```prompt\n请安装工具\n```\n\n结尾。', 'notes': ''}


@pytest.mark.parametrize('wrapper', ['{}', '```json\n{}\n```', '先给出结果：\n{}\n说明结束。', '\ufeff{}', '````json\n{}\n````'])
def test_outer_json_is_read_before_article_prompt_blocks(wrapper):
    assert llm.extract_json(wrapper.format(json.dumps(ARTICLE, ensure_ascii=False))) == ARTICLE


def test_literal_line_breaks_are_preserved_but_incomplete_roots_are_not_repaired():
    assert llm.extract_json('{"body":"第一行\n第二行"}')['body'] == '第一行\n第二行'
    for broken in ['{"body":"半篇', '{"title":"标题","nested":{"ok":true}', '```json\n{"body":"未结束\n```']:
        with pytest.raises(ValueError, match='JSON'):
            llm.extract_json(broken)


def make_draft(c, body=''):
    iid = c.post('/api/ideas', json={'title': '测试起草'}, headers=H).json()['id']
    with db.tx() as conn:
        did = conn.execute("INSERT INTO drafts(idea_id,platform,title,body,status,error,created_at,updated_at) VALUES (?,'wechat','原题',?,'failed','测试失败',?,?)",
                           (iid, body, db.now(), db.now())).lastrowid
    return did, iid


@pytest.mark.parametrize('result', [[], {'title': '题'}, {'title': '题', 'body': ''}, {'title': '题', 'body': {'bad': True}}, {'title': '', 'body': '正文'}])
def test_invalid_fields_keep_original_article_and_private_reply(monkeypatch, result):
    c = client(); login(c)
    did, _ = make_draft(c, '原有正文')
    monkeypatch.setattr(jobs.llm, 'run', lambda *a, **kw: json.dumps(result))
    monkeypatch.setattr(jobs.prompts, 'case_search_on', lambda *a: False)
    with pytest.raises(ValueError):
        jobs._draft(Settings(), {'draft_id': did})
    assert db.row('SELECT body FROM drafts WHERE id=?', (did,))['body'] == '原有正文'
    assert not db.rows('SELECT * FROM draft_versions WHERE draft_id=?', (did,))
    assert (data_dir()/'failed-draft-replies'/f'{did}.txt').exists()


def test_retry_is_same_empty_draft_and_freezes_chosen_model(monkeypatch):
    s = Settings(writing_model='claude-opus-5-5', llm_backend='disabled'); s.save()
    c = client(writing_model=s.writing_model, llm_backend='disabled'); login(c)
    did, _ = make_draft(c)
    assert c.post(f'/api/drafts/{did}/retry-draft', json={'writing_model': 'deepseek-v4-pro'}, headers=H).status_code == 200
    assert c.post(f'/api/drafts/{did}/retry-draft', json={'writing_model': 'claude-opus-5-5'}, headers=H).status_code == 409
    payload = db.loads(db.row("SELECT payload FROM jobs WHERE kind='draft'")['payload'])
    assert payload == {'draft_id': did, 'writing_model': 'deepseek-v4-pro'}
    calls = []
    monkeypatch.setattr(model_api, 'run', lambda *a, **kw: pytest.fail('Wrong provider'))
    def run(prompt, timeout, task, **kw):
        calls.append((task, kw))
        assert kw['json_output'] is True
        return json.dumps(ARTICLE, ensure_ascii=False)
    monkeypatch.setattr(deepseek_api, 'run', run)
    flags = iter([['待核对文风'], []])
    monkeypatch.setattr(jobs.prompts, 'voice_flags', lambda *a: next(flags))
    monkeypatch.setattr(jobs.prompts, 'case_search_on', lambda *a: False)
    jobs._draft(s, payload)
    assert len(calls) == 2 and all(task == 'write' for task, _ in calls)
    result = db.row('SELECT * FROM drafts WHERE id=?', (did,))
    assert result['status'] == 'draft' and '```prompt' in result['body']
    assert 'DeepSeek 官方' in db.row('SELECT body FROM comments WHERE draft_id=?', (did,))['body']
    assert Settings.load().writing_model == 'claude-opus-5-5' and Settings.load().llm_backend == 'disabled'


def test_retry_and_invalid_model_never_overwrite_an_existing_article():
    c = client(); login(c)
    did, iid = make_draft(c, '已有正文')
    assert c.post(f'/api/drafts/{did}/retry-draft', json={'writing_model': 'deepseek-v4-pro'}, headers=H).status_code == 409
    for bad in [None, '', 'other', {}]:
        assert c.post(f'/api/ideas/{iid}/draft', json={'platform': 'wechat', 'writing_model': bad}, headers=H).status_code == 400
    assert len(c.get('/api/drafts').json()) == 1
    assert not db.rows("SELECT * FROM jobs WHERE kind='draft'")


def test_preparation_request_carries_per_article_choice_and_keeps_default():
    c = client(writing_model='claude-opus-5-5'); login(c)
    iid = c.post('/api/ideas', json={'title': '有依据的选题'}, headers=H).json()['id']
    r = c.post(f'/api/ideas/{iid}/draft', json={'platform': 'wechat', 'preparation': {}, 'writing_model': 'deepseek-v4-pro'}, headers=H)
    assert r.status_code == 200, r.text
    assert db.loads(db.row("SELECT payload FROM jobs WHERE kind='draft'")['payload'])['writing_model'] == 'deepseek-v4-pro'
    assert c.get('/api/settings').json()['writing_model'] == 'claude-opus-5-5'
    assert c.put('/api/settings', json={'writing_model': 'deepseek-v4-pro'}, headers=H).status_code == 200


def test_concurrent_manual_edit_is_not_overwritten_by_late_model_result(monkeypatch):
    c = client(); login(c)
    did, _ = make_draft(c)
    def reply(*a, **kw):
        with db.tx() as conn:
            conn.execute('UPDATE drafts SET body=?,updated_at=? WHERE id=?', ('用户的新正文', db.now(), did))
        return json.dumps(ARTICLE)
    monkeypatch.setattr(jobs.llm, 'run', reply)
    monkeypatch.setattr(jobs.prompts, 'voice_flags', lambda *a: [])
    monkeypatch.setattr(jobs.prompts, 'case_search_on', lambda *a: False)
    with pytest.raises(ValueError, match='已有修改'):
        jobs._draft(Settings(), {'draft_id': did})
    assert c.get(f'/api/drafts/{did}').json()['body'] == '用户的新正文'


def test_claude_json_guidance_and_quota_error_are_safe(monkeypatch):
    fake = SimpleNamespace(read_credentials=lambda: {}, claude_provider=lambda _: ('https://api.daitu.cc/v1','test-key'),
                           MODELS={}, redact=lambda text, _: text)
    monkeypatch.setattr(model_api, 'connector', lambda _: fake)
    code, calls = [200], []
    def handler(request):
        payload = json.loads(request.content); calls.append(payload)
        assert 'JSON' in payload['system'] and '\\n' in payload['system']
        if code[0] != 200:
            return httpx.Response(code[0], json={'error': {'code': 'insufficient_quota', 'message': 'private prompt and test-key'}})
        return httpx.Response(200, json={'model': 'claude-opus-5-5', 'stop_reason':'end_turn', 'content':[{'type':'text','text':json.dumps(ARTICLE)}]})
    real = httpx.Client
    monkeypatch.setattr(model_api.httpx, 'Client', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    s = Settings(llm_backend='disabled', writing_model='claude-opus-5-5')
    assert llm.extract_json(llm.run('JSON', configuration=s, json_output=True)) == ARTICLE
    code[0] = 402
    monkeypatch.setattr(deepseek_api, 'run', lambda *a, **kw: pytest.fail('No silent fallback'))
    with pytest.raises(llm.LlmUnavailable, match='额度或余额不足') as error:
        llm.run('JSON', configuration=s, json_output=True)
    assert 'DeepSeek' in str(error.value) and 'test-key' not in str(error.value) and 'private prompt' not in str(error.value)
    assert len(calls) == 2

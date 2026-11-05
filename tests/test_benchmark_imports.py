import json

import pytest

from content_workbench import benchmark_imports as imports, benchmarks, db, jobs, llm, references
from content_workbench.config import Settings
from test_app import ORIGIN, client, login

H = {'Origin': ORIGIN}
URL = 'https://example.org/article'


def owner():
    c = client(); login(c)
    return c


def complete():
    for job in db.rows("SELECT * FROM jobs WHERE kind='benchmark_import' AND state='queued' ORDER BY id"):
        payload = json.loads(job['payload'])
        try:
            jobs.run_job(Settings(), job['kind'], payload)
            jobs._set(job['id'], 'done')
        except Exception as exc:
            jobs._set(job['id'], 'failed', str(exc))
            jobs._mark_failure(job['kind'], payload, exc)


def article(url):
    return {'title': '真实流程教程', 'body': '来自原网页的真实正文。' * 12, 'author': '原网页作者', 'tags': ['网页标签'], 'url': url}


def test_batch_auth_validation_dedup_and_no_model_work_in_request(monkeypatch):
    assert client().get('/api/benchmark-imports/latest').status_code == 401
    c = owner()
    assert c.post('/api/benchmark-imports', json={'text': URL}).status_code == 403
    assert c.post('/api/benchmark-imports', json={'text': None}, headers=H).status_code == 400
    assert c.post('/api/benchmark-imports', json={'text': '不是链接'}, headers=H).status_code == 400
    assert c.post('/api/benchmark-imports', json={'text': '\n'.join(URL+str(i) for i in range(21))}, headers=H).status_code == 400
    monkeypatch.setattr(llm, 'run', lambda *a, **k: pytest.fail('POST only queues jobs'))
    batch = c.post('/api/benchmark-imports', json={'text': f'分享文章 {URL}#one\n{URL}#two\nhttps://EXAMPLE.org/article。'}, headers=H).json()
    assert batch['active'] and len(batch['items']) == 1
    assert db.rows('SELECT * FROM benchmarks') == []
    assert c.get('/api/benchmark-imports/latest').json()['id'] == batch['id']


def test_each_link_has_independent_progress_author_and_classification(monkeypatch):
    c = owner()
    def read(url):
        if url.endswith('/blocked'):
            raise references.ReferenceError('网站需要登录，请粘贴原文')
        return article(url)
    monkeypatch.setattr(benchmarks, 'read_source', read)
    monkeypatch.setattr(llm, 'run', lambda *a, **k: json.dumps({'kind': '教程', 'tags': ['操作流程', 'AI实战'], 'author': '模型猜测的名字'}))
    batch = c.post('/api/benchmark-imports', json={'text': URL+'\nhttps://example.org/blocked'}, headers=H).json()
    complete()
    result = c.get(f'/api/benchmark-imports/{batch["id"]}').json()
    assert not result['active']
    good, bad = result['items']
    assert good['state'] == 'saved' and good['author'] == '原网页作者' and good['kind'] == '教程'
    assert good['tags'] == ['操作流程', 'AI实战'] and bad['state'] == 'failed' and '登录' in bad['error']
    assert len(c.get('/api/benchmarks').json()) == 1
    assert db.rows('SELECT * FROM benchmark_analyses') == []
    # Unknown bylines stay unknown even when the model supplies a name.
    monkeypatch.setattr(benchmarks, 'read_source', lambda url: {**article(url), 'author': ''})
    batch = c.post('/api/benchmark-imports', json={'text': URL+'-no-author'}, headers=H).json()
    complete()
    assert c.get(f'/api/benchmark-imports/{batch["id"]}').json()['items'][0]['author'] == ''


def test_duplicate_and_resumed_jobs_do_not_replace_user_metadata(monkeypatch):
    c = owner()
    original = c.post('/api/benchmarks', json={**article(URL), 'kind': '观点', 'tags': ['本人整理']}, headers=H).json()
    monkeypatch.setattr(benchmarks, 'read_source', lambda *a: pytest.fail('Known URLs skip the source and model'))
    batch = c.post('/api/benchmark-imports', json={'text': URL}, headers=H).json()
    payload = json.loads(db.rows("SELECT * FROM jobs WHERE kind='benchmark_import'")[0]['payload'])
    imports.run(payload); imports.run(payload)
    item = c.get(f'/api/benchmark-imports/{batch["id"]}').json()['items'][0]
    assert item['state'] == 'duplicate' and item['benchmark_id'] == original['id']
    assert c.get(f'/api/benchmarks/{original["id"]}').json()['tags'] == ['本人整理']


def test_classification_failure_keeps_source_and_metadata(monkeypatch):
    c = owner()
    monkeypatch.setattr(benchmarks, 'read_source', article)
    monkeypatch.setattr(llm, 'run', lambda *a, **k: '{"kind":')
    batch = c.post('/api/benchmark-imports', json={'text': URL}, headers=H).json()
    complete()
    item = c.get(f'/api/benchmark-imports/{batch["id"]}').json()['items'][0]
    assert item['state'] == 'saved' and item['warning'] and item['kind'] == '未分类'
    assert item['author'] == '原网页作者' and item['tags'] == ['网页标签']
    assert c.get(f'/api/benchmarks/{item["benchmark_id"]}').json()['body'] == article(URL)['body']


def test_reimport_fills_missing_author_without_changing_body_or_custom_tags(monkeypatch):
    c = owner()
    original = c.post('/api/benchmarks', json={**article(URL), 'author': '', 'kind': '未分类', 'tags': ['本人标签']}, headers=H).json()
    monkeypatch.setattr(benchmarks, 'read_source', lambda url: {**article(url), 'body': '网站后续修改的正文，不替换旧原文'})
    monkeypatch.setattr(llm, 'run', lambda *a, **k: '{"kind":"教程","tags":["AI流程"]}')
    batch = c.post('/api/benchmark-imports', json={'text': URL}, headers=H).json()
    complete()
    result = c.get(f'/api/benchmarks/{original["id"]}').json()
    assert result['author'] == '原网页作者' and result['kind'] == '教程'
    assert result['body'] == original['body'] and result['tags'] == ['本人标签']
    assert c.get(f'/api/benchmark-imports/{batch["id"]}').json()['items'][0]['state'] == 'duplicate'


def test_retry_only_failed_items_and_no_ai_when_disabled(monkeypatch):
    c = owner()
    def broken(url):
        if url.endswith('/retry'):
            raise references.ReferenceError('暂时无法读取')
        return article(url)
    monkeypatch.setattr(benchmarks, 'read_source', broken)
    monkeypatch.setattr(llm, 'run', lambda *a, **k: pytest.fail('Auto classification is off'))
    batch = c.post('/api/benchmark-imports', json={'text': URL+'\nhttps://example.org/retry', 'auto_classify': False}, headers=H).json()
    complete()
    first = c.get(f'/api/benchmark-imports/{batch["id"]}').json()['items'][0]['benchmark_id']
    monkeypatch.setattr(benchmarks, 'read_source', article)
    c.post(f'/api/benchmark-imports/{batch["id"]}/retry', headers=H)
    complete()
    result = c.get(f'/api/benchmark-imports/{batch["id"]}').json()
    assert not result['active'] and all(i['state'] == 'saved' for i in result['items'])
    assert result['items'][0]['benchmark_id'] == first and len(db.rows('SELECT * FROM benchmarks')) == 2


def test_read_failure_never_sends_bad_source_to_model(monkeypatch):
    c = owner()
    monkeypatch.setattr(llm, 'run', lambda *a, **k: pytest.fail('Private or unreadable URL never reaches model'))
    monkeypatch.setattr(references, '_addresses', lambda *a: (_ for _ in ()).throw(references.ReferenceError('不能读取本机或内网地址')))
    batch = c.post('/api/benchmark-imports', json={'text': 'https://localhost/private'}, headers=H).json()
    complete()
    result = c.get(f'/api/benchmark-imports/{batch["id"]}').json()
    assert result['items'][0]['state'] == 'failed' and not db.rows('SELECT * FROM benchmarks')


def test_author_metadata_precedence_and_json_ld_do_not_execute_scripts():
    body = '<article><h1>原文标题</h1><p>' + '真实文章的正文和操作步骤。'*10 + '</p></article>'
    wx = references.extract_article('<meta name="author" content="元数据作者"><span id="js_author_name">实际署名</span><a id="js_name">公众号账号</a>'+body, 'https://mp.weixin.qq.com/s/test')
    assert wx['author'] == '实际署名' and wx['account'] == '公众号账号'
    account = references.extract_article('<a id="js_name">公众号账号</a>'+body, 'https://mp.weixin.qq.com/s/test')
    assert account['author'] == '公众号账号'
    generic = references.extract_article('<script type="application/ld+json">{"@type":"Article","author":[{"name":"作者甲"},{"name":"作者乙"}]}</script><script>evil()</script>'+body, URL)
    assert generic['author'] == '作者甲、作者乙' and 'evil' not in generic['body']
    assert references.extract_article(body, URL)['author'] == ''

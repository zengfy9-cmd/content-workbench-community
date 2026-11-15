"""Collections keep publisher provenance and the owner's notes before research."""
import io

import pytest
from PIL import Image

from content_workbench import benchmarks, db, mobile_inbox
from test_app import client, login


@pytest.fixture(autouse=True)
def isolate_statistics(monkeypatch):
    monkeypatch.setattr(mobile_inbox.reading_metrics, 'ensure', lambda *args, **kwargs: None)


@pytest.mark.parametrize(('url', 'platform'), [
    ('https://mp.weixin.qq.com/s/example', '公众号'),
    ('https://www.douyin.com/video/1', '抖音'),
    ('https://xhslink.com/example', '小红书'),
    ('https://www.bilibili.com/video/example', 'B站'),
    ('https://scys.com/articleDetail/xq_topic/1', '生财'),
    ('https://mp.weixin.qq.com.example.org/article', '网页'),
    ('', '原平台待确认'),
])
def test_source_platform_is_not_the_collection_channel(url, platform):
    assert benchmarks.source_platform({'url': url}) == platform


def test_notes_are_visible_before_analysis_and_survive_duplicate_import(monkeypatch):
    c = client()
    login(c)
    monkeypatch.setattr(benchmarks, 'read_source', lambda url: {
        'title': '原作者文章', 'url': url, 'body': '原作者正文。', 'author': '原作者',
    })
    article = {'message_id': 'article', 'msg_type': 'text',
               'content': 'https://mp.weixin.qq.com/s/example', 'create_time': '2026-11-06 18:01'}
    mobile_inbox.ingest(article)
    note = {'message_id': 'reply', 'parent_id': 'article', 'content': '可以参考开头，但效果还需验证。',
            'create_time': '2026-11-06 18:02', 'msg_type': 'text'}
    mobile_inbox.ingest(note)
    mobile_inbox.ingest(note)
    mobile_inbox.ingest({**article, 'message_id': 'repeat'})
    bid = db.row('SELECT id FROM benchmarks')['id']
    # Old importer labels must not masquerade as content tags; actual tags stay.
    with db.tx() as conn:
        conn.execute('UPDATE benchmarks SET tags=? WHERE id=?', (db.dumps(['手机收藏', '写法']), bid))
    summary = c.get('/api/benchmarks').json()[0]
    detail = c.get(f'/api/benchmarks/{bid}').json()
    assert summary['source_platform'] == detail['source_platform'] == '公众号'
    assert summary['collection_channel'] == '飞书群转发'
    assert summary['tags'] == detail['tags'] == ['写法']
    assert summary['mobile_note_count'] == 1
    assert summary['mobile_note_preview'] == note['content']
    assert detail['mobile_notes'] == [{'note': note['content'], 'stamp': note['create_time']}]
    assert detail['analysis'] is None
    assert len(c.get('/api/benchmarks').json()) == 1
    assert detail['body'] == '原作者正文。'


def test_several_notes_remain_independent_of_research_versions(monkeypatch):
    monkeypatch.setattr(benchmarks, 'read_source', lambda url: {'title': '文章', 'body': '原文', 'url': url})
    mobile_inbox.ingest({'message_id': 'article', 'content': 'https://mp.weixin.qq.com/s/test', 'msg_type': 'text'})
    for number in range(2):
        mobile_inbox.ingest({'message_id': f'reply-{number}', 'root_id': 'article',
                             'content': f'我补充的判断{number}', 'msg_type': 'text'})
    item = benchmarks.list_benchmarks()[0]
    assert item['mobile_note_count'] == 2
    assert item['mobile_note_preview'] == '我补充的判断1'
    notes = benchmarks.bundle(item['id'])['mobile_notes']
    assert [n['note'] for n in notes] == ['我补充的判断0', '我补充的判断1']


def image_downloader(monkeypatch):
    calls = []

    def download(args, cwd=None):
        calls.append(args)
        file = cwd / 'original.png'
        buffer = io.BytesIO()
        Image.new('RGB', (24, 32), 'blue').save(buffer, 'PNG')
        file.write_bytes(buffer.getvalue())
        return {'saved_path': str(file)}

    monkeypatch.setattr(mobile_inbox.sources, '_lark', download)
    return calls


def test_image_post_saves_original_and_full_caption_without_placeholder_title(monkeypatch):
    calls = image_downloader(monkeypatch)
    c = client()
    login(c)
    message = {'message_id': 'picture', 'msg_type': 'post',
               'content': '![Image](img_test)\n我的完整判断\n第二段的用途', 'create_time': '2026-11-06 18:01'}
    bid = mobile_inbox.ingest(message)
    mobile_inbox.ingest(message)
    detail = c.get(f'/api/benchmarks/{bid}').json()
    assert detail['title'] == '图片素材 · 我的完整判断'
    assert detail['original_html'] == ''
    assert detail['mobile_notes'][0]['note'] == '我的完整判断\n第二段的用途'
    assert 'img_test' not in detail['body']
    assert detail['source_platform'] == '原平台待确认'
    image_id = db.row('SELECT image_id FROM mobile_capture_images')['image_id']
    assert f'/api/images/{image_id}' in detail['body']
    response = c.get(f'/api/images/{image_id}')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/png'
    assert Image.open(io.BytesIO(response.content)).size == (24, 32)
    assert len(calls) == 1
    assert len(c.get('/api/benchmarks').json()) == 1


def test_plain_image_message_and_multiple_post_images(monkeypatch):
    image_downloader(monkeypatch)
    bid = mobile_inbox.ingest({'message_id': 'one', 'msg_type': 'image', 'content': '{"image_key":"img_one"}'})
    assert benchmarks.require(bid)['title'] == '图片素材'
    second = mobile_inbox.ingest({'message_id': 'two', 'msg_type': 'post',
                                  'content': '![Image](img_one)\n![Image](img_two)\n![Image](img_one)\n我的判断'})
    assert benchmarks.require(second)['body'].count('/api/images/') == 2
    assert len(db.rows('SELECT * FROM mobile_capture_images')) == 3


def test_repair_reuses_old_asset_and_keeps_import_time(monkeypatch):
    image_downloader(monkeypatch)
    content = '![Image](img_old)\n我想借鉴这个开头'
    old = benchmarks.add_benchmark({'title': content[:70], 'body': content})
    with db.tx() as conn:
        conn.execute('INSERT INTO mobile_captures VALUES(?,?,?,?)', ('old', old['id'], '', '2026-11-06 18:01'))
        conn.execute("UPDATE benchmarks SET original_html='<div></div>' WHERE id=?", (old['id'],))
    bid = mobile_inbox.ingest({'message_id': 'old', 'content': content, 'msg_type': 'post'}, repair_images=True)
    repaired = benchmarks.bundle(bid)
    assert bid == old['id']
    assert repaired['created_at'] == old['created_at']
    assert repaired['mobile_notes'][0]['note'] == '我想借鉴这个开头'
    assert len(benchmarks.list_benchmarks()) == 1
    assert repaired['title'] == '图片素材 · 我想借鉴这个开头'
    assert repaired['original_html'] == ''
    with pytest.raises(ValueError, match='已被修改'):
        mobile_inbox.ingest({'message_id': 'old', 'content': content, 'msg_type': 'post'}, repair_images=True)


def test_failed_image_download_does_not_create_empty_article_and_can_retry(monkeypatch):
    message = {'message_id': 'retry', 'msg_type': 'post', 'content': '![Image](img_retry)\n保留这句判断'}
    def fail(*args, **kwargs):
        raise mobile_inbox.sources.SourceError('读取失败')
    monkeypatch.setattr(mobile_inbox.sources, '_lark', fail)
    with pytest.raises(mobile_inbox.sources.SourceError):
        mobile_inbox.ingest(message)
    assert db.rows('SELECT * FROM benchmarks') == []
    assert db.rows('SELECT * FROM mobile_captures') == []
    image_downloader(monkeypatch)
    mobile_inbox.ingest(message)
    assert len(benchmarks.list_benchmarks()) == 1


def test_article_import_requests_metrics_after_saving_original_and_note(monkeypatch):
    calls = []
    monkeypatch.setattr(benchmarks, 'read_source', lambda url: {'title': '文章', 'body': '原文', 'url': url,
                                                               'reading_url': 'complete-article-url'})
    def metrics(bid, **kwargs):
        item = benchmarks.bundle(bid)
        assert item['body'] == '原文'
        assert item['mobile_notes'][0]['note'] == '我的判断'
        calls.append(kwargs)
    monkeypatch.setattr(mobile_inbox.reading_metrics, 'ensure', metrics)
    mobile_inbox.ingest({'message_id': 'article', 'msg_type': 'text',
                         'content': 'https://mp.weixin.qq.com/s/example 我的判断'})
    assert calls == [{'query_url': 'complete-article-url'}]

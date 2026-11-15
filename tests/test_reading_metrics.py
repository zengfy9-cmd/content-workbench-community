from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from content_workbench import benchmarks, db, reading_metrics, references

URL = 'https://mp.weixin.qq.com/s/example'
HTML = '''var biz="exampleBiz=="; var mid="2247480001"; var idx="1";
var sn="0123456789abcdef0123456789abcdef";'''


def source():
    return benchmarks.add_benchmark({'title': '原题', 'url': URL, 'body': '原正文', 'tags': ['原标签']})


def test_short_link_identity_is_resolved_without_executing_scripts():
    full = references.wechat_reading_url(URL, HTML)
    query = parse_qs(urlsplit(full).query)
    assert query == {'__biz': ['exampleBiz=='], 'mid': ['2247480001'], 'idx': ['1'],
                     'sn': ['0123456789abcdef0123456789abcdef']}
    assert references.wechat_reading_url(full) == full
    assert references.wechat_reading_url(URL, 'var read_num = 0;') == ''
    assert references.wechat_reading_url('https://example.org', HTML) == ''


@pytest.mark.parametrize('count', [0, 12345, '23456'])
def test_count_is_only_read_from_real_stats(count):
    payload = {'code': 200, 'data': {'read_num': count, 'get_data_succ': 1, 'show_read': 1}}
    assert reading_metrics.count_from_stats(payload) == int(count)


@pytest.mark.parametrize('data', [{'read_num': None}, {'like_count': 9}, {'read_num': True},
                                 {'read_num': -1}, {'read_num': 1.5}, {'read_num': '10万+'},
                                 {'read_num': 0, 'show_read': 0}, {'read_num': 0, 'get_data_succ': 0}])
def test_missing_hidden_or_invalid_read_counts_are_not_saved_as_zero(data):
    with pytest.raises(ValueError):
        reading_metrics.count_from_stats({'code': 200, 'data': data})


def test_acquisition_uses_complete_link_and_preserves_source(monkeypatch):
    old = source()
    monkeypatch.setattr(reading_metrics.tikhub_media, 'key', lambda: 'test-access')
    monkeypatch.setattr(references, '_download', lambda url: (HTML, url))
    calls = []
    def stats(endpoint, **kwargs):
        calls.append(kwargs)
        assert endpoint == reading_metrics.ENDPOINT
        assert '__biz=' in kwargs['json']['url']
        assert kwargs['follow_redirects'] is False
        return httpx.Response(200, json={'code': 200, 'data': {'read_num': 20312, 'show_read': 1, 'get_data_succ': 1}})
    monkeypatch.setattr(httpx, 'post', stats)
    reading_metrics.ensure(old['id'])
    reading_metrics.ensure(old['id'])
    item = benchmarks.bundle(old['id'])
    assert item['read_count'] == 20312 and item['read_count_at']
    assert benchmarks.list_benchmarks()[0]['read_count'] == 20312
    assert item['body'] == old['body'] and item['tags'] == old['tags']
    assert item['created_at'] == old['created_at'] and item['updated_at'] == old['updated_at']
    assert len(calls) == 1


def test_failure_is_visible_cools_down_and_does_not_discard_article(monkeypatch):
    old = source()
    monkeypatch.setattr(reading_metrics.tikhub_media, 'key', lambda: 'test-access')
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        return httpx.Response(400, json={'detail': 'provider internal response'})
    monkeypatch.setattr(httpx, 'post', fail)
    full = references.wechat_reading_url(URL, HTML)
    reading_metrics.ensure(old['id'], query_url=full)
    reading_metrics.ensure(old['id'], query_url=full)
    item = benchmarks.require(old['id'])
    assert item['read_count'] is None and item['read_count_at'] is None
    assert '查询失败' in item['read_count_error']
    assert 'provider internal' not in item['read_count_error']
    assert benchmarks.list_benchmarks()[0]['read_count_error'] == item['read_count_error']
    assert item['body'] == old['body']
    assert len(calls) == 2


def test_compact_stats_are_tried_once_after_raw_response_fails(monkeypatch):
    old = source()
    monkeypatch.setattr(reading_metrics.tikhub_media, 'key', lambda: 'test-access')
    calls = []
    def stats(*args, **kwargs):
        calls.append(kwargs['json']['raw'])
        return httpx.Response(400) if kwargs['json']['raw'] else httpx.Response(200, json={'code': 200, 'data': {'read_num': 85495}})
    monkeypatch.setattr(httpx, 'post', stats)
    reading_metrics.ensure(old['id'], query_url=references.wechat_reading_url(URL, HTML))
    assert calls == [True, False]
    assert benchmarks.require(old['id'])['read_count'] == 85495


def test_manual_count_is_preserved_and_unknown_source_is_skipped(monkeypatch):
    old = source()
    benchmarks.save_source_metadata(old['id'], {'read_count': 777})
    monkeypatch.setattr(reading_metrics.tikhub_media, 'key', lambda: pytest.fail('Existing data must be reused'))
    reading_metrics.ensure(old['id'], force=True)
    assert benchmarks.require(old['id'])['read_count'] == 777
    image = benchmarks.add_benchmark({'title': '图片素材', 'body': '原图'})
    reading_metrics.ensure(image['id'])


def test_changed_url_during_request_does_not_receive_old_statistics(monkeypatch):
    old = source()
    monkeypatch.setattr(reading_metrics.tikhub_media, 'key', lambda: 'test-access')
    def stats(*args, **kwargs):
        with db.tx() as conn:
            conn.execute('UPDATE benchmarks SET url=? WHERE id=?', ('https://mp.weixin.qq.com/s/changed', old['id']))
        return httpx.Response(200, json={'code': 200, 'data': {'read_num': 12}})
    monkeypatch.setattr(httpx, 'post', stats)
    reading_metrics.ensure(old['id'], query_url=references.wechat_reading_url(URL, HTML))
    assert benchmarks.require(old['id'])['read_count'] is None

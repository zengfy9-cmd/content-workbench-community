"""WeChat read counts and Douyin likes; do not interchange the two."""
import json
from urllib.parse import urlsplit

import httpx

from . import db, references, tikhub_media

ENDPOINT = 'https://api.tikhub.io/api/v1/wechat_mp/v2/fetch_article_stats'
RETRY_AFTER = 24 * 3600

def cached_douyin_likes(bid,mid):
    """Read only the original download cache; this never calls TikHub."""
    from .config import data_dir
    row=db.row("SELECT b.url,b.like_count,m.url AS media_url,m.source_platform FROM benchmarks b "
               "JOIN media_imports m ON m.id=b.media_id WHERE b.id=? AND m.id=?",(bid,mid))
    if not row or row['like_count'] is not None or row['source_platform']!='抖音' or row['url']!=row['media_url']:return
    path=data_dir()/'media-imports'/str(mid)/'tikhub-response.json'
    try:
        count=tikhub_media.douyin_likes(tikhub_media.douyin_note(json.loads(path.read_text(encoding='utf-8')),row['url']))
        captured_at=path.stat().st_mtime
    except (OSError,ValueError,AttributeError,RuntimeError):return
    if count is not None:
        with db.tx() as conn:
            conn.execute("UPDATE benchmarks SET like_count=?,like_count_at=?,like_count_source='TikHub · 抖音作品缓存' "
                         "WHERE id=? AND media_id=? AND url=? AND like_count IS NULL",
                         (count,captured_at,bid,mid,row['url']))

def backfill_cached_douyin():
    for row in db.rows("SELECT b.id,b.media_id FROM benchmarks b JOIN media_imports m ON m.id=b.media_id "
                       "WHERE b.like_count IS NULL AND m.source_platform='抖音'"):
        cached_douyin_likes(row['id'],row['media_id'])


def count_from_stats(payload):
    data = payload.get('data')
    if payload.get('code') != 200 or not isinstance(data, dict):
        raise ValueError('统计服务未返回有效阅读量')
    if data.get('get_data_succ', 1) != 1 or data.get('show_read', 1) != 1:
        raise ValueError('来源未开放这篇文章的阅读量')
    count = data.get('read_num')
    if isinstance(count, str) and count.isdigit():
        count = int(count)
    if type(count) is not int or not 0 <= count <= 10_000_000_000:
        raise ValueError('来源未返回有效阅读量')
    return count


def store_count(bid, url, count):
    """Never replace a manually entered value or a concurrently changed URL."""
    with db.tx() as conn:
        conn.execute("UPDATE benchmarks SET read_count=?,read_count_at=?,read_count_checked_at=?,"
                     "read_count_source='TikHub · 公众号统计',read_count_error='' "
                     'WHERE id=? AND url=? AND read_count IS NULL', (count, db.now(), db.now(), bid, url))


def ensure(bid, *, query_url='', force=False):
    source = db.row('SELECT * FROM benchmarks WHERE id=?', (bid,))
    if not source or source['read_count'] is not None:
        return
    url = source['url']
    if (urlsplit(url).hostname or '').lower() != 'mp.weixin.qq.com':
        return
    if not force and source['read_count_checked_at'] and db.now() - source['read_count_checked_at'] < RETRY_AFTER:
        return
    with db.tx() as conn:
        conn.execute('UPDATE benchmarks SET read_count_checked_at=? WHERE id=?', (db.now(), bid))
    try:
        credential = tikhub_media.key()
        if not credential:
            raise ValueError('未连接公众号统计服务')
        full_url = references.wechat_reading_url(query_url) or references.wechat_reading_url(url)
        if not full_url:
            html, final_url = references._download(url)
            full_url = references.wechat_reading_url(final_url, html)
        if not full_url:
            raise ValueError('未取得文章完整链接，暂不能查询阅读量')
        # Authorization is sent only to the configured TikHub host, never to WeChat.
        for raw in (True, False):
            response = httpx.post(ENDPOINT, headers={'Authorization': 'Bearer ' + credential},
                                  json={'url': full_url, 'raw': raw}, timeout=40, follow_redirects=False)
            # The same endpoint's compact-count response can succeed when its
            # raw-stat response fails. Never retry an already billed 200 reply.
            if response.status_code != 400:
                break
        if response.status_code != 200:
            raise ValueError(f'统计服务查询失败（HTTP {response.status_code}），稍后可重试')
        count = count_from_stats(response.json())
        store_count(bid, url, count)
    except httpx.HTTPError:
        error = '统计服务暂时无法连接，稍后可重试'
    except ValueError as exc:
        # Do not expose provider response bodies, request headers or credentials.
        error = str(exc)[:160]
    else:
        return
    with db.tx() as conn:
        conn.execute('UPDATE benchmarks SET read_count_error=? WHERE id=? AND url=? AND read_count IS NULL',
                     (error, bid, url))


def backfill():
    """At most five missing collections per scan; failures cool down for a day."""
    for row in db.rows("SELECT DISTINCT b.id FROM benchmarks b JOIN mobile_captures m ON m.bid=b.id "
                       "WHERE b.read_count IS NULL AND b.url LIKE 'https://mp.weixin.qq.com/%' "
                       'AND (b.read_count_checked_at IS NULL OR b.read_count_checked_at<?) ORDER BY b.id LIMIT 5',
                       (db.now() - RETRY_AFTER,)):
        ensure(row['id'])

"""Writing plans and cached, read-only AIHOT news."""
import json
import re
import threading
import time
from datetime import date

import httpx
from fastapi import APIRouter, Body, HTTPException

from . import db

router = APIRouter()
_news_lock = threading.Lock()


def setup():
    with db.tx() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS writing_plans (
          id INTEGER PRIMARY KEY, day TEXT NOT NULL, title TEXT NOT NULL, angle TEXT NOT NULL DEFAULT '',
          kind TEXT NOT NULL DEFAULT 'planned', state TEXT NOT NULL DEFAULT 'todo',
          idea_id INTEGER, draft_id INTEGER, benchmark_ids TEXT NOT NULL DEFAULT '[]',
          source_url TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS news_cache (key TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS content_tips (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '选题', origin TEXT NOT NULL DEFAULT '我的心得',
            validation TEXT NOT NULL DEFAULT '待验证', scenario TEXT NOT NULL DEFAULT '',
            steps TEXT NOT NULL DEFAULT '', example TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '', feedback TEXT NOT NULL DEFAULT '',
            pinned INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL, updated_at REAL NOT NULL);''')


def valid_day(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise HTTPException(400, '请选择有效日期') from None


@router.get('/api/plans')
def plans():
    rows = db.rows('SELECT p.*,d.title AS draft_title,d.status AS draft_status FROM writing_plans p LEFT JOIN drafts d ON d.id=p.draft_id ORDER BY day,position,p.id')
    for row in rows:
        row['benchmark_ids'] = db.loads(row['benchmark_ids'], [])
    return rows


@router.post('/api/plans')
def add_plan(body: dict = Body(...)):
    title = str(body.get('title', '')).strip()
    if not title or len(title) > 300:
        raise HTTPException(400, '请填写计划标题（300字内）')
    day = valid_day(body.get('day'))
    ids = body.get('benchmark_ids', [])
    if not isinstance(ids, list) or len(ids) > 20 or any(type(i) is not int or not db.row('SELECT id FROM benchmarks WHERE id=?', (i,)) for i in ids):
        raise HTTPException(400, '请选择有效的对标文章')
    idea = body.get('idea_id')
    if idea is not None and (type(idea) is not int or not db.row('SELECT id FROM ideas WHERE id=?', (idea,))):
        raise HTTPException(400, '选题不存在')
    kind = body.get('kind', 'planned')
    if kind not in ('planned', 'extra'):
        raise HTTPException(400, '计划类型无效')
    draft_id = body.get('draft_id')
    if draft_id is not None and (type(draft_id) is not int or not db.row('SELECT id FROM drafts WHERE id=?', (draft_id,))):
        raise HTTPException(400, '请选择有效的稿件')
    url = str(body.get('source_url', ''))[:4096]
    if url and not url.startswith(('https://', 'http://')):
        raise HTTPException(400, '来源链接无效')
    with db.tx() as c:
        # Scheduling an existing draft is a date change, never a copy of its text.
        existing = c.execute("SELECT * FROM writing_plans WHERE draft_id=? AND kind=? AND state NOT IN ('done','archived') ORDER BY id LIMIT 1", (draft_id, kind)).fetchone() if draft_id else None
        if existing:
            merged = list(dict.fromkeys(db.loads(existing['benchmark_ids'], []) + ids))
            angle = str(body.get('angle', '')).strip()[:4000] or existing['angle']
            c.execute('UPDATE writing_plans SET day=?,angle=?,benchmark_ids=? WHERE id=?', (day, angle, db.dumps(merged), existing['id']))
            return {'id': existing['id'], 'existing': True}
        if draft_id:
            linked = c.execute('SELECT benchmark_id FROM draft_benchmarks WHERE draft_id=?', (draft_id,)).fetchall()
            ids = list(dict.fromkeys(ids + [r['benchmark_id'] for r in linked]))
        pid = c.execute('INSERT INTO writing_plans(day,title,angle,kind,idea_id,draft_id,benchmark_ids,source_url,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                        (day, title, str(body.get('angle', ''))[:4000], kind, idea, draft_id, db.dumps(list(dict.fromkeys(ids))), url, db.now())).lastrowid
        c.execute('UPDATE writing_plans SET position=? WHERE id=?',(pid*1000,pid))
    return {'id': pid}


@router.patch('/api/plans/{pid}')
def update_plan(pid: int, body: dict = Body(...)):
    if not db.row('SELECT id FROM writing_plans WHERE id=?', (pid,)):
        raise HTTPException(404, '计划不存在')
    fields = {}
    if 'title' in body:
        title=str(body['title']).strip()
        if not title or len(title)>300:
            raise HTTPException(400,'请填写有效标题')
        fields['title']=title
    if 'angle' in body:
        fields['angle']=str(body['angle'])[:4000]
    if 'benchmark_ids' in body:
        ids=body['benchmark_ids']
        if not isinstance(ids,list) or len(ids)>20 or any(type(i) is not int or not db.row('SELECT id FROM benchmarks WHERE id=?',(i,)) for i in ids):
            raise HTTPException(400,'对标文章无效')
        fields['benchmark_ids']=db.dumps(list(dict.fromkeys(ids)))
    if 'day' in body:
        fields['day'] = valid_day(body['day'])
    if 'state' in body:
        if body['state'] not in ('todo', 'writing', 'done', 'archived'):
            raise HTTPException(400, '计划状态无效')
        fields['state'] = body['state']
    if 'position' in body:
        if type(body['position']) is not int or not -1000000000 <= body['position'] <= 1000000000:
            raise HTTPException(400, '排序无效')
        fields['position'] = body['position']
    if fields:
        with db.tx() as c:
            c.execute('UPDATE writing_plans SET ' + ','.join(k+'=?' for k in fields) + ' WHERE id=?', (*fields.values(), pid))
    return {'ok': True}


@router.post('/api/plans/{pid}/start')
def start_plan(pid: int):
    from .benchmarks import reference_draft
    with db.tx() as c:
        plan = c.execute('SELECT * FROM writing_plans WHERE id=?', (pid,)).fetchone()
        if not plan:
            raise HTTPException(404, '计划不存在')
        did = plan['draft_id']
        if did and not c.execute('SELECT id FROM drafts WHERE id=?', (did,)).fetchone():
            raise HTTPException(409, '原稿已被删除，请重新关联稿件或新建计划')
        if not did:
            did = c.execute("INSERT INTO drafts(platform,title,body,notes,status,created_at,updated_at,idea_id) VALUES('wechat',?,'',?,'draft',?,?,?)",
                            (plan['title'], plan['angle']+('\n\n热点来源：'+plan['source_url'] if plan['source_url'] else ''), db.now(), db.now(), plan['idea_id'])).lastrowid
        c.execute("UPDATE writing_plans SET draft_id=?,state='writing' WHERE id=?", (did, pid))
    for bid in db.loads(plan['benchmark_ids'], []):
        if db.row('SELECT id FROM benchmarks WHERE id=?', (bid,)):
            reference_draft(bid, {'draft_id': did})
    return {'id': did}


TIP_CHOICES = {'category': ('选题', '标题', '开头', '结构', '素材', '排版'),
               'origin': ('我的心得', '他人分享', 'AI建议'),
               'validation': ('待验证', '试用中', '用过有效')}
TIP_TEXT = {'title': 200, 'content': 12000, 'scenario': 3000, 'steps': 6000,
            'example': 6000, 'source_url': 4096, 'feedback': 4000}


def tip_values(body, creating=False):
    values = {}
    for key, limit in TIP_TEXT.items():
        if key not in body:
            continue
        value = body[key]
        if not isinstance(value, str) or len(value.strip()) > limit:
            raise HTTPException(400, f'{key} 内容格式或长度无效')
        values[key] = value.strip()
    if creating:
        values.setdefault('content', '')
        values['title'] = values.get('title') or values['content'].split('\n')[0][:80]
    if any(key in values and not values[key] for key in ('title', 'content')):
        raise HTTPException(400, '请至少写下一句技巧')
    url = values.get('source_url', '')
    if url and not re.fullmatch(r'https?://\S+', url):
        raise HTTPException(400, '来源请填写完整网页链接')
    for key, choices in TIP_CHOICES.items():
        if key in body:
            if body[key] not in choices:
                raise HTTPException(400, f'{key} 选项无效')
            values[key] = body[key]
    for key in ('pinned', 'archived'):
        if key in body:
            if type(body[key]) is not bool:
                raise HTTPException(400, '置顶与归档状态无效')
            values[key] = int(body[key])
    return values


@router.get('/api/tips')
def list_tips():
    return db.rows('SELECT t.*,s.benchmark_id AS source_benchmark_id,s.analysis_id AS source_analysis_id FROM content_tips t LEFT JOIN method_sources s ON s.tip_id=t.id ORDER BY t.pinned DESC,t.updated_at DESC,t.id DESC')


@router.post('/api/tips')
def add_tip(body: dict = Body(...)):
    values = tip_values(body, creating=True)
    values.update(created_at=db.now(), updated_at=db.now())
    with db.tx() as c:
        cur = c.execute(f"INSERT INTO content_tips({','.join(values)}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))
    return db.row('SELECT * FROM content_tips WHERE id=?', (cur.lastrowid,))


@router.patch('/api/tips/{tid}')
def update_tip(tid: int, body: dict = Body(...)):
    if not db.row('SELECT id FROM content_tips WHERE id=?', (tid,)):
        raise HTTPException(404, '技巧不存在')
    values = tip_values(body)
    values['updated_at'] = db.now()
    with db.tx() as c:
        c.execute(f"UPDATE content_tips SET {','.join(k+'=?' for k in values)} WHERE id=?", (*values.values(), tid))
    return db.row('SELECT * FROM content_tips WHERE id=?', (tid,))


def _mcp(name, arguments):
    with httpx.Client(timeout=35, follow_redirects=False, trust_env=False, headers={'Accept': 'application/json, text/event-stream'}) as client:
        def rpc(method, params, rid):
            response = client.post('https://aihot.news/api/mcp', json={'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
            response.raise_for_status()
            if response.headers.get('mcp-session-id'):
                client.headers['Mcp-Session-Id'] = response.headers['mcp-session-id']
            if 'text/event-stream' in response.headers.get('content-type', ''):
                messages = [json.loads(line[5:].strip()) for line in response.text.splitlines() if line.startswith('data:') and line[5:].strip()]
                data = next((m for m in messages if m.get('id') == rid), {})
            else:
                data = response.json()
            if data.get('error'):
                raise ValueError('AIHOT 接口返回异常')
            return data.get('result', {})
        init = rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'content-workbench', 'version': '1.0'}}, 1)
        client.headers['MCP-Protocol-Version'] = init.get('protocolVersion', '2024-11-05')
        client.post('https://aihot.news/api/mcp', json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        result = rpc('tools/call', {'name': name, 'arguments': arguments}, 2)
        if result.get('isError'):
            raise ValueError('AIHOT 暂时无法返回内容')
        return '\n'.join(c.get('text', '') for c in result.get('content', []) if c.get('type') == 'text')


def refresh_news():
    if not _news_lock.acquire(blocking=False):
        return
    try:
        text = _mcp('aihot_get_hot_topics', {})
        items = []
        for match in re.finditer(r'第\s*(\d+)\s*名[：:]\s*\[([^\]]+)\]\((https://aihot\.news/[^)]+)\)(.*?)(?=第\s*\d+\s*名|$)', text, re.S):
            rank, title, url, detail = match.groups()
            story = re.search(r'public_id=([a-zA-Z0-9-]+)', detail)
            items.append({'rank': int(rank), 'title': title, 'url': url, 'story_id': story.group(1) if story else None, 'detail': detail.strip()[:1200]})
        if not items:
            raise ValueError('AIHOT 榜单格式暂时无法读取')
        with db.tx() as c:
            c.execute("INSERT OR REPLACE INTO news_cache VALUES('hot',?,?)", (db.dumps({'items': items[:5]}), db.now()))
    finally:
        _news_lock.release()


def _quiet_refresh():
    try:
        refresh_news()
    except Exception:
        pass


@router.get('/api/news/hot')
def news():
    cached = db.row("SELECT * FROM news_cache WHERE key='hot'")
    stale = not cached or time.time() - cached['updated_at'] > 3600
    if stale:
        threading.Thread(target=_quiet_refresh, daemon=True).start()
    return {**(db.loads(cached['body'], {}) if cached else {'items': []}), 'updated_at': cached['updated_at'] if cached else None, 'stale': stale}


@router.post('/api/news/refresh')
def refresh():
    try:
        refresh_news()
    except Exception:
        raise HTTPException(502, 'AIHOT 更新失败，请稍后重试；保留上次榜单') from None
    return news()


@router.get('/api/news/stories/{public_id}')
def story(public_id: str):
    cached = db.row("SELECT body FROM news_cache WHERE key='hot'")
    items = db.loads(cached['body'], {}).get('items', []) if cached else []
    item = next((i for i in items if i.get('story_id') == public_id), None)
    key = 'story:' + public_id
    old = db.row('SELECT * FROM news_cache WHERE key=?', (key,))
    if old and time.time() - old['updated_at'] < 3600:
        return db.loads(old['body'], {})
    if not item:
        raise HTTPException(404, '请从当前热点榜选择事件')
    try:
        body = _mcp('aihot_get_story', {'public_id': public_id})
    except Exception:
        raise HTTPException(502, '热点详情暂时无法读取，可打开来源查看') from None
    result = {**item, 'body': body}
    with db.tx() as c:
        c.execute('INSERT OR REPLACE INTO news_cache VALUES(?,?,?)', (key, db.dumps(result), db.now()))
    return result

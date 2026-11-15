"""Import the configured owner's collections from one private Feishu conversation."""
import json
import re
import tempfile
from fastapi import APIRouter, HTTPException
from datetime import datetime, timedelta
from pathlib import Path
from . import benchmarks, db, images, reading_metrics, sources
from .config import data_dir

URL = re.compile(r'https?://[^\s<>"）)】]+')
router=APIRouter()

def setup():
    with db.tx() as c:
        c.execute("CREATE TABLE IF NOT EXISTS mobile_pending(mid TEXT PRIMARY KEY,content TEXT NOT NULL,stamp TEXT NOT NULL,candidate_bid INTEGER,archived_bid INTEGER,state TEXT NOT NULL DEFAULT 'pending')")
        c.execute('CREATE TABLE IF NOT EXISTS mobile_messages(mid TEXT PRIMARY KEY,payload TEXT NOT NULL,received_at REAL NOT NULL)')

def content_of(m):
    content=m.get('content') or ''
    if isinstance(content,str):
        try:parsed=json.loads(content)
        except ValueError:return content
        if isinstance(parsed,dict):content=parsed
        else:return content
    if isinstance(content,dict):
        if isinstance(content.get('text'),str):return content['text']
        def walk(v):
            if isinstance(v,dict):
                if v.get('tag')=='a':return [v.get('href',''),v.get('text','')]
                if v.get('tag')=='text':return [v.get('text','')]
                return [s for x in v.values() for s in walk(x)]
            if isinstance(v,list):return [s for x in v for s in walk(x)]
            return []
        return '\n'.join(walk(content))
    return str(content)

def share_note(content,url):
    own=re.search(r'(?:我的判断|我的评价)[：:]\s*([\s\S]+)',content)
    if own:return own.group(1).strip()
    if any(s in content for s in ('复制打开抖音','长按复制此条消息','复制此链接','打开抖音搜索')):return ''
    return content.replace(url,'').strip()

def queue_video(bid,url,content=''):
    from . import media_imports
    if benchmarks.source_platform({'url':url}) not in ('抖音','小红书','B站','YouTube'):return
    if '查看TA的更多作品' in content or '查看 TA 的更多作品' in content:
        if benchmarks.require(bid)['title'].startswith('待补正文'):
            benchmarks.edit_benchmark(bid,{'title':'抖音账号主页 · 待选择具体视频','body':'这条分享指向作者账号，尚未提供具体视频作品。请补充某条作品的分享链接后提取正文。\n\n原链接：'+url})
        return
    source=benchmarks.require(bid)
    if source.get('media_id'):return
    old=db.row('SELECT id FROM media_imports WHERE url=? ORDER BY id DESC LIMIT 1',(url,))
    mid=old['id'] if old else media_imports.new(source['title'],'视频',url,source['source_platform'])
    with db.tx() as c:
        c.execute('UPDATE benchmarks SET media_id=? WHERE id=?',(mid,bid))
        c.execute("UPDATE media_imports SET benchmark_id=?,extraction_only=1,distiller='workbench',auto_xiaod=? WHERE id=?",
                  (bid,int(not old and source['source_platform']=='抖音'),mid))
    (media_imports.folder(mid)/'repair-target.json').write_text(db.dumps({'id':bid,'body_hash':source['body_hash']}),encoding='utf-8')
    row=media_imports.require(mid)
    reading_metrics.cached_douyin_likes(bid,mid)
    if row['state']=='done' and row['article'].strip():media_imports.finish_reading_repair(mid,row['article'])
    elif not old or row['state'] not in ('running','queued'):media_imports.queue(mid)

def pending(m,content,candidate=None,archived_bid=None):
    with db.tx() as c:
        c.execute('INSERT OR IGNORE INTO mobile_pending(mid,content,stamp,candidate_bid,archived_bid) VALUES(?,?,?,?,?)',(m['message_id'],content,m.get('create_time',''),candidate,archived_bid))

@router.get('/api/mobile-inbox/pending')
def pending_notes():
    return db.rows("SELECT p.*,b.title AS candidate_title FROM mobile_pending p LEFT JOIN benchmarks b ON b.id=p.candidate_bid WHERE p.state='pending' ORDER BY p.stamp,p.mid")

@router.post('/api/mobile-inbox/pending/{mid}/material')
def save_as_material(mid:str):
    row=db.row('SELECT * FROM mobile_pending WHERE mid=?',(mid,))
    if not row:raise HTTPException(404,'文字不存在')
    with db.tx() as c:
        previous=c.execute("SELECT id FROM materials WHERE source_ref=?",('feishu:'+mid,)).fetchone()
        ident=previous['id'] if previous else c.execute("INSERT INTO materials(source,source_ref,title,body,status,created_at) VALUES('note',?,?,?,'ready',?)",('feishu:'+mid,row['content'][:70],row['content'],db.now())).lastrowid
        c.execute("UPDATE mobile_pending SET state='material' WHERE mid=?",(mid,))
    return {'id':ident}

@router.post('/api/mobile-inbox/pending/{mid}/link')
def link_note(mid:str,body:dict):
    row=db.row('SELECT * FROM mobile_pending WHERE mid=?',(mid,))
    if not row:raise HTTPException(404,'评价不存在')
    bid=body.get('benchmark_id')
    if type(bid) is not int:raise HTTPException(400,'请选择对应对标')
    benchmarks.require(bid)
    with db.tx() as c:
        c.execute('INSERT INTO mobile_captures(mid,bid,note,stamp) VALUES(?,?,?,?) ON CONFLICT(mid) DO UPDATE SET bid=excluded.bid,note=excluded.note',(mid,bid,row['content'],row['stamp']))
        c.execute("UPDATE mobile_pending SET state='linked' WHERE mid=?",(mid,))
    return {'ok':True,'benchmark_id':bid}

@router.post('/api/mobile-inbox/ingest')
def ingest_messages(body:dict):
    messages=body.get('messages')
    if not isinstance(messages,list) or len(messages)>100:raise HTTPException(400,'每批最多100条消息')
    for m in messages:
        if not isinstance(m,dict) or not isinstance(m.get('message_id'),str) or len(db.dumps(m))>120000000:raise HTTPException(400,'消息格式无效或附件过大')
    for m in messages:ingest(m)
    return {'ok':True,'received':len(messages)}

@router.post('/api/mobile-inbox/reconcile')
def reconcile(body:dict):
    """Explicit source-message mappings; archive only untouched importer artifacts."""
    messages=body.get('messages',[]);links=body.get('links',{})
    if body.get('confirm') is not True or not isinstance(messages,list) or len(messages)>100 or not isinstance(links,dict):raise HTTPException(400,'请提供已核对的消息关联')
    indexed={m['message_id']:m for m in messages}
    backup={'messages':messages,'captures':db.rows('SELECT * FROM mobile_captures'),'benchmarks':db.rows('SELECT * FROM benchmarks')}
    path=data_dir()/'backups'/'feishu-repair';path.mkdir(parents=True,exist_ok=True)
    import uuid
    (path/(uuid.uuid4().hex+'.json')).write_text(db.dumps(backup),encoding='utf-8')
    result=[]
    for m in messages:
        with db.tx() as c:c.execute('INSERT OR IGNORE INTO mobile_messages VALUES(?,?,?)',(m['message_id'],db.dumps(m),db.now()))
        mid=m['message_id'];content=content_of(m);urls=URL.findall(content)
        capture=db.row('SELECT * FROM mobile_captures WHERE mid=?',(mid,))
        if urls:
            if capture:
                if capture['note']==content.replace(urls[0],'').strip() and not share_note(content,urls[0]):
                    with db.tx() as c:c.execute("UPDATE mobile_captures SET note='' WHERE mid=?",(mid,))
                source=benchmarks.require(capture['bid'])
                if source['body'].startswith('尚未取得原文。'):queue_video(source['id'],urls[0],content)
            continue
        if not capture:pending(m,content);continue
        source=benchmarks.require(capture['bid'])
        # Refuse to repurpose any record that has since been edited or researched.
        referenced=db.row('SELECT 1 FROM draft_benchmarks WHERE benchmark_id=?',(source['id'],)) or db.row('SELECT 1 FROM ideas WHERE benchmark_id=?',(source['id'],))
        if source['body']!=content or source.get('current_analysis_id') or (referenced and body.get('archive_referenced_notes') is not True):
            result.append({'mid':mid,'state':'kept_modified'});continue
        target_mid=links.get(mid)
        target=db.row('SELECT bid FROM mobile_captures WHERE mid=?',(target_mid,)) if target_mid in indexed else None
        with db.tx() as c:
            c.execute('UPDATE benchmarks SET archived=1 WHERE id=?',(source['id'],))
            if target:c.execute('UPDATE mobile_captures SET bid=?,note=? WHERE mid=?',(target['bid'],content,mid))
            else:
                c.execute('DELETE FROM mobile_captures WHERE mid=?',(mid,))
                c.execute('INSERT OR IGNORE INTO mobile_pending(mid,content,stamp,archived_bid) VALUES(?,?,?,?)',(mid,content,m.get('create_time',''),source['id']))
        result.append({'mid':mid,'state':'linked' if target else 'pending','benchmark_id':target['bid'] if target else None})
    return {'ok':True,'results':result}
IMAGE_REF = re.compile(r'!\[[^\]]*\]\((img_[\w-]+)\)')


def image_parts(message):
    """The CLI represents post images as Markdown resource keys, not URLs."""
    content = message.get('content') or ''
    if message.get('msg_type') == 'image':
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                pass
        if isinstance(content, dict) and re.fullmatch(r'img_[\w-]+', content.get('image_key', '')):
            return [content['image_key']], ''
    if not isinstance(content, str):
        return [], ''
    return list(dict.fromkeys(IMAGE_REF.findall(content))), IMAGE_REF.sub('', content).strip()


def download_image(message_id, resource_key, inline=None):
    cached = db.row('SELECT image_id FROM mobile_capture_images WHERE mid=? AND resource_key=?',
                    (message_id, resource_key))
    if cached:
        image = images.get(cached['image_id'])
        if image and images.path_of(image).is_file():
            return image
    if inline:
        import base64
        raw=base64.b64decode(inline,validate=True)
        image=images.save(None,'飞书图片',raw)
        with db.tx() as conn:
            conn.execute('INSERT OR REPLACE INTO mobile_capture_images(mid,resource_key,image_id) VALUES(?,?,?)',(message_id,resource_key,image['id']))
        return image
    with tempfile.TemporaryDirectory(dir=data_dir()) as temp:
        root = Path(temp).resolve()
        result = sources._lark(['im', '+messages-resources-download', '--as', 'user',
                                '--message-id', message_id, '--file-key', resource_key,
                                '--type', 'image', '--output', 'original'], cwd=root)
        downloaded = Path(result['saved_path'])
        downloaded = (downloaded if downloaded.is_absolute() else root / downloaded).resolve()
        if not downloaded.is_relative_to(root) or not downloaded.is_file():
            raise sources.SourceError('飞书图片下载路径无效')
        if downloaded.stat().st_size > images.MAX_UPLOAD:
            raise images.ImageError('图片太大了，单张不超过 20MB')
        image = images.save(None, '飞书图片', downloaded.read_bytes())
    with db.tx() as conn:
        conn.execute('INSERT OR REPLACE INTO mobile_capture_images(mid,resource_key,image_id) VALUES(?,?,?)',
                     (message_id, resource_key, image['id']))
    return image


def ingest_images(message, keys, caption, existing=None):
    original = None
    if existing:
        original = benchmarks.require(existing['bid'])
        # Repair only the old verbatim placeholder; keep any subsequent edits.
        if original['body'] != message.get('content') or not IMAGE_REF.search(original['body']):
            raise ValueError('这条图片素材已被修改，保留当前内容，未自动覆盖')
    saved = [download_image(message['message_id'], key, (message.get('_inline_images') or {}).get(key)) for key in keys]
    title = '图片素材' + (' · ' + caption.splitlines()[0][:60] if caption else '')
    body = '图片原件已保存，尚未识别图中文字。\n\n' + '\n\n'.join(
        f'![飞书原图 {number}](/api/images/{image["id"]})' for number, image in enumerate(saved, 1))
    if original:
        benchmarks.edit_benchmark(existing['bid'], {'title': title, 'body': body})
        with db.tx() as conn:
            conn.execute("UPDATE benchmarks SET original_html='' WHERE id=? AND original_html='<div></div>'",
                         (existing['bid'],))
            conn.execute('UPDATE mobile_captures SET note=? WHERE mid=?',
                         (existing['note'] or caption, message['message_id']))
        return existing['bid']
    source = benchmarks.add_benchmark({'title': title, 'body': body, 'kind': '其他'})
    with db.tx() as conn:
        conn.execute('INSERT INTO mobile_captures(mid,bid,note,stamp) VALUES(?,?,?,?)',
                     (message['message_id'], source['id'], caption, message.get('create_time', '')))
    return source['id']


def ingest(m, *, repair_images=False):
    mid = m['message_id']
    existing = db.row('SELECT * FROM mobile_captures WHERE mid=?',(mid,))
    if db.row('SELECT 1 FROM mobile_pending WHERE mid=?',(mid,)):return
    if existing and not repair_images:
        return
    keys, caption = image_parts(m)
    if keys:
        return ingest_images(m, keys, caption, existing)
    if existing:
        return
    content = content_of(m)
    with db.tx() as c:c.execute('INSERT OR IGNORE INTO mobile_messages VALUES(?,?,?)',(mid,db.dumps(m),db.now()))
    parent = m.get('parent_id') or m.get('root_id')
    previous = db.row('SELECT bid FROM mobile_captures WHERE mid=?',(parent,)) if parent else None
    urls = URL.findall(content)
    url = urls[0].rstrip('，。；') if urls else ''
    note = ''
    reading_url = ''
    if previous and not url:
        bid = previous['bid']
        note = content
    else:
        prior = db.row('SELECT id FROM benchmarks WHERE url=? ORDER BY id LIMIT 1',(url,)) if url else None
        if url and m.get('msg_type')=='text':
            note = share_note(content,url)
        if prior:
            bid = prior['id']
        else:
            if url:
                if benchmarks.source_platform({'url':url}) in ('抖音','小红书','B站','YouTube'):
                    article = {'url':url,'title':'待补正文 · '+url[:90],'body':'尚未取得原文。原链接：'+url}
                else:
                    try:
                        article = benchmarks.read_source(url)
                        reading_url = article.get('reading_url', '')
                    except Exception:
                        article = {'url':url,'title':'待补正文 · '+url[:90],'body':'尚未取得原文。原链接：'+url}
            else:
                pending(m,content)
                return
            bid = benchmarks.add_benchmark(article)['id']
    with db.tx() as c:
        c.execute('INSERT OR IGNORE INTO mobile_captures(mid,bid,note,stamp) VALUES(?,?,?,?)',(mid,bid,note,m.get('create_time','')))
    reading_metrics.ensure(bid, query_url=reading_url)
    if url and benchmarks.require(bid)['body'].startswith('尚未取得原文。'):queue_video(bid,url,content)

def sync():
    file = data_dir()/'mobile-inbox.json'
    if not file.exists(): return
    cfg = json.loads(file.read_text(encoding='utf-8-sig'))
    start = db.row('SELECT stamp FROM mobile_cursor WHERE chat=?',(cfg['chat'],))
    end = datetime.now().astimezone()
    args = ['im','+chat-messages-list','--as','user','--chat-id',cfg['chat'],'--no-reactions','--order','asc','--end',end.isoformat(timespec='seconds')]
    if start: args += ['--start',start['stamp']]
    token = ''
    while True:
        page = sources._lark(args+(['--page-token',token] if token else []))
        for m in page['messages']:
            if not m.get('deleted') and m.get('sender',{}).get('id')==cfg['owner'] and m.get('msg_type')!='system':
                ingest(m)
        if not page.get('has_more'): break
        nxt = page.get('page_token')
        if not nxt or nxt==token: raise ValueError('飞书消息分页中断')
        token = nxt
    with db.tx() as c:
        c.execute('INSERT OR REPLACE INTO mobile_cursor VALUES(?,?)',(cfg['chat'],(end-timedelta(minutes=2)).isoformat(timespec='seconds')))
    reading_metrics.backfill()

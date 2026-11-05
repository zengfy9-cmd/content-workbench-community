"""Import already-reviewed Codex output without generation or publication."""
import base64
import hashlib
import io
import re
import secrets
from PIL import Image
from fastapi import HTTPException
from . import db, images
from .platforms import PLATFORMS


def register(app):
    @app.post('/api/codex/archive')
    def archive(payload: dict):
        for key in ('content_id', 'project', 'title', 'body', 'confirmation'):
            if not isinstance(payload.get(key), str) or not payload[key].strip():
                raise HTTPException(400, f'缺少 {key}')
        if payload.get('platform') not in PLATFORMS:
            raise HTTPException(400, '平台无效')
        if '/api/images/' in payload['body']:
            raise HTTPException(400, '请使用配图占位符，不直接引用其他稿件的图片编号')
        expected = payload.get('expected_revision')
        if type(expected) is not int or expected < 0:
            raise HTTPException(400, '需要 expected_revision，首次为 0')
        sources = payload.get('sources', [])
        items = payload.get('images', [])
        if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources) or not isinstance(items, list) or len(items) > 30:
            raise HTTPException(400, '来源或图片格式无效')
        prepared = []
        for i, item in enumerate(items, 1):
            try:
                raw = base64.b64decode(item['base64'], validate=True)
                if len(raw) > images.MAX_UPLOAD:
                    raise ValueError()
                image = Image.open(io.BytesIO(raw)); image.verify()
                image = Image.open(io.BytesIO(raw))
                mime, ext = images.ALLOWED[image.format]
                if '{{image:%d}}' % i not in payload['body']:
                    raise ValueError()
                prepared.append((str(item.get('name', f'配图{i}'))[:120], raw, mime, ext, image.width, image.height))
            except Exception:
                raise HTTPException(400, f'图片 {i} 无效、过大或正文缺少对应占位符') from None
        markers = {int(n) for n in re.findall(r'\{\{image:(\d+)\}\}', payload['body'])}
        if markers != set(range(1, len(items)+1)):
            raise HTTPException(400, '图片占位符与附件数量不一致')
        canonical = {k: payload[k] for k in ('content_id','project','platform','title','body','confirmation')}
        canonical.update(sources=sources, images=[{'name':p[0], 'sha256':hashlib.sha256(p[1]).hexdigest()} for p in prepared])
        fingerprint = hashlib.sha256(db.dumps(canonical).encode()).hexdigest()
        files = []
        try:
            with db.tx() as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS codex_archives(content_id TEXT, revision INTEGER, draft_id INTEGER REFERENCES drafts(id), fingerprint TEXT, snapshot TEXT, created_at REAL, PRIMARY KEY(content_id,revision))')
                previous = conn.execute('SELECT * FROM codex_archives WHERE content_id=? ORDER BY revision DESC LIMIT 1',(payload['content_id'],)).fetchone()
                if previous:
                    old = db.loads(previous['snapshot'], {})
                    if old['project'] != payload['project'] or old['platform'] != payload['platform']:
                        raise HTTPException(409, '内容编号已绑定其他项目或平台')
                    draft = conn.execute('SELECT * FROM drafts WHERE id=?',(previous['draft_id'],)).fetchone()
                    if not draft:
                        raise HTTPException(409, '原稿已删除，请使用新的内容编号')
                    if previous['fingerprint'] == fingerprint:
                        if draft['body'] != old.get('rendered_body') or draft['title'] != old['title']:
                            raise HTTPException(409, '归档后稿件已编辑，原归档仍保留；请先比较当前稿件')
                        return {'draft_id':draft['id'], 'revision':previous['revision'], 'image_ids':old['image_ids'], 'reused':True, 'url':f"http://localhost:8765/#/drafts/{draft['id']}"}
                    if payload.get('expected_updated_at') != draft['updated_at'] or draft['status'] != 'draft':
                        raise HTTPException(409, '稿件已编辑或不处于草稿状态，请先读取最新稿件比较')
                if expected != (previous['revision'] if previous else 0):
                    raise HTTPException(409, '归档版本冲突，请先核对最新回执')
                stamp = db.now()
                conn.execute('INSERT OR IGNORE INTO content_groups(name,created_at) VALUES(?,?)',(payload['project'],stamp))
                group = conn.execute('SELECT id FROM content_groups WHERE name=?',(payload['project'],)).fetchone()['id']
                did = previous['draft_id'] if previous else conn.execute('INSERT INTO drafts(platform,title,body,group_id,created_at,updated_at) VALUES(?,?,\'\',?,?,?)',(payload['platform'],payload['title'],group,stamp,stamp)).lastrowid
                if previous:
                    conn.execute('INSERT INTO draft_versions(draft_id,title,body,created_at,origin) VALUES(?,?,?,?,?)',(did,draft['title'],draft['body'],stamp,'before-codex-archive'))
                body = payload['body']
                image_ids = []
                for i, (name, raw, mime, ext, width, height) in enumerate(prepared, 1):
                    file = images.images_dir() / (secrets.token_hex(16)+ext)
                    files.append(file); file.write_bytes(raw); file.chmod(0o600)
                    iid = conn.execute('INSERT INTO images(draft_id,name,file,mime,width,height,bytes,created_at) VALUES(?,?,?,?,?,?,?,?)',(did,name,file.name,mime,width,height,len(raw),stamp)).lastrowid
                    image_ids.append(iid)
                    body = body.replace('{{image:%d}}' % i, f'![配图{i}](/api/images/{iid})')
                notes = f"Codex 归档编号：{payload['content_id']}\n确认依据：{payload['confirmation']}\n来源：\n" + '\n'.join(sources)
                if previous and draft['notes']:
                    notes = draft['notes'] + '\n\n' + notes
                conn.execute('UPDATE drafts SET title=?,body=?,notes=?,group_id=?,updated_at=? WHERE id=?',(payload['title'],body,notes,group,stamp,did))
                conn.execute('INSERT INTO draft_versions(draft_id,title,body,created_at,origin) VALUES(?,?,?,?,?)',(did,payload['title'],body,stamp,'codex-archive'))
                revision = expected + 1
                canonical['rendered_body'] = body
                canonical['image_ids'] = image_ids
                conn.execute('INSERT INTO codex_archives VALUES(?,?,?,?,?,?)',(payload['content_id'],revision,did,fingerprint,db.dumps(canonical),stamp))
            return {'draft_id':did,'revision':revision,'updated_at':stamp,'image_ids':image_ids,'reused':False,'url':f'http://localhost:8765/#/drafts/{did}'}
        except Exception:
            for file in files:
                file.unlink(missing_ok=True)
            raise

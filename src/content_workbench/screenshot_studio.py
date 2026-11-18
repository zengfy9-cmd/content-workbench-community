"""The existing Xilo canvas editor, backed by the workbench's private database."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import math
import re
import secrets
import zipfile
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from markdown_it.helpers import parseLinkDestination, parseLinkTitle
from PIL import Image

from . import db, images

router = APIRouter()
ASSETS = Path(__file__).with_name('screenshot_assets')
LIMIT = 200_000_000
PREFIX = '/api/screenshot-projects'


def setup():
    with db.tx() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS screenshot_projects(
          id TEXT PRIMARY KEY, title TEXT NOT NULL, draft_id INTEGER,
          project TEXT NOT NULL, bindings TEXT NOT NULL, revision INTEGER NOT NULL,
          write_token TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS screenshot_exports(
          project_id TEXT NOT NULL REFERENCES screenshot_projects(id), id TEXT NOT NULL,
          data BLOB NOT NULL, mime TEXT NOT NULL, filename TEXT NOT NULL,
          created_at REAL NOT NULL, PRIMARY KEY(project_id,id));
        ''')


def project(pid):
    row = db.row('SELECT * FROM screenshot_projects WHERE id=?', (pid,))
    if not row:
        raise HTTPException(404, '截图项目不存在')
    return row


def decode_image(value, png_only=False):
    if not isinstance(value, str) or len(value) > 29_000_000:
        raise ValueError('每张图片需为不超过 20 MB 的 PNG、JPG 或 WebP')
    match = re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=\r\n]+)', value)
    if not match:
        raise ValueError('仅支持内嵌 PNG、JPG、WebP 图片，不读取外部网址')
    raw = base64.b64decode(match[2], validate=True)
    if len(raw) > images.MAX_UPLOAD:
        raise ValueError('每张图片不超过 20 MB')
    with Image.open(io.BytesIO(raw)) as im:
        if im.format not in (('PNG',) if png_only else ('PNG', 'JPEG', 'WEBP')):
            raise ValueError('图片格式不正确')
        if im.width * im.height > 40_000_000:
            raise ValueError('图片分辨率过大')
        im.verify()
    return raw


def validate(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('项目格式不正确')
    slides = value.get('slides')
    if not isinstance(slides, list) or not 1 <= len(slides) <= 64:
        raise ValueError('每个截图项目需有 1–64 张图片')
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > LIMIT:
        raise ValueError('项目超过 200 MB，请按组保存')
    ids = set()
    if value.get('defaultWatermark'):
        decode_image(value['defaultWatermark'])
    for slide in slides:
        sid = slide.get('id')
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', sid) or sid in ids:
            raise ValueError('截图编号无效或重复')
        ids.add(sid)
        decode_image(slide.get('image'))
        style = slide.get('style', {})
        for key in ('width', 'height'):
            number = style.get(key)
            if not isinstance(number, (int, float)) or not math.isfinite(number) or not 320 <= number <= 4096:
                raise ValueError('画布宽高需在 320–4096 px 之间')
        if style.get('bgImage'):
            decode_image(style['bgImage'])
        if style.get('watermark', {}).get('src'):
            decode_image(style['watermark']['src'])
        annotations = slide.get('annotations', [])
        if not isinstance(annotations, list) or len(annotations) > 300:
            raise ValueError('标注对象过多')
        for a in annotations:
            if not isinstance(a, dict) or a.get('type') not in ('rect', 'arrow', 'text', 'number', 'mosaic'):
                raise ValueError('标注类型无效')
    return value


def image_refs(text):
    # Xilo's parser masks code without changing Unicode character offsets.
    masked = re.sub(r'(?ms)^([ \t]*)(`{3,}|~{3,})[^\n]*\n.*?^\1\2[^\n]*(?:\n|$)', lambda m: ' ' * len(m[0]), text)
    masked = re.sub(r'(`+)([^`]|(?!\1)`)*?\1', lambda m: ' ' * len(m[0]), masked)
    refs = []
    for m in re.finditer(r'(?<!\\)(?:\\\\)*!\[(?:\\.|[^\]\\])*\]\([ \t]*', masked):
        start=m.end()
        stop=masked.find('\n\n',start)
        limit=len(masked) if stop<0 else stop
        dest=parseLinkDestination(masked,start,limit)
        if not dest.ok:continue
        tail=dest.pos
        while tail<limit and masked[tail] in ' \t\n':tail+=1
        if tail>dest.pos and tail<limit and masked[tail]!=')':
            title=parseLinkTitle(masked,tail,limit,None)
            if not title.ok:continue
            tail=title.pos
            while tail<limit and masked[tail] in ' \t\n':tail+=1
        if tail>=limit or masked[tail]!=')':continue
        end=dest.pos
        if text[start:start+1]=='<':start+=1;end-=1
        if end>start:refs.append({'start':start,'end':end,'original':text[start:end]})
    return refs


def candidates(did):
    draft = db.row('SELECT id,title,body,status FROM drafts WHERE id=?', (did,))
    if not draft:
        raise HTTPException(404, '稿件不存在')
    rows, seen = [], set()
    for ref in image_refs(draft['body']):
        if not re.fullmatch(r'/api/images/\d{1,18}',ref['original']):continue
        iid = int(ref['original'].rsplit('/', 1)[1])
        image = images.get(iid)
        if not image or image['draft_id'] != did or iid in seen:
            continue
        seen.add(iid)
        rows.append({'id': iid, 'name': image['name'], 'url': ref['original'],
                     'context': draft['body'][max(0,ref['start']-160):ref['end']+160]})
    return draft, rows


@router.get(PREFIX)
def listing():
    return db.rows('SELECT id,title,draft_id,revision,created_at,updated_at FROM screenshot_projects ORDER BY updated_at DESC')


@router.get('/api/drafts/{did}/screenshot-candidates')
def draft_candidates(did: int):
    draft, rows = candidates(did)
    return {'draft_id': did, 'title': draft['title'], 'images': rows}


@router.post(PREFIX)
def create(body: dict = Body(...)):
    did, bindings = body.get('draft_id'), {}
    try:
        if did is not None:
            draft, rows = candidates(did)
            selected = body.get('image_ids', [])
            if not isinstance(selected, list) or not selected:
                raise ValueError('请先选中要编辑的截图；不会把所有配图自动当成截图')
            available = {r['id']: r for r in rows}
            if len(selected) != len(set(selected)) or any(i not in available for i in selected):
                raise ValueError('图片不属于当前原稿正文')
            slides = []
            for n, iid in enumerate(selected):
                image = images.get(iid)
                raw = images.path_of(image).read_bytes()
                src = f'data:{image["mime"]};base64,' + base64.b64encode(raw).decode()
                sid = f's{n+1:03}'
                bindings[sid] = {'url': f'/api/images/{iid}', 'source_id': iid,
                                  'source_hash':hashlib.sha256(src.encode()).hexdigest()}
                slides.append({'id': sid, 'name': image['name'], 'source': f'/api/images/{iid}',
                    'image': src, 'context': available[iid]['context'], 'references': [],
                    'evidence': '用户在原稿中选中，作为截图编辑', 'reviewed': False, 'annotations': [],
                    'style': {'width': 1600, 'height': 1000, 'padding': 70, 'scale': 1,
                        'offsetX': 0, 'offsetY': 0, 'radius': 24, 'shadow': 25,
                        'bg': ['#087f8c', '#b5e8ba'], 'bgImage': None}})
            value = {'version': 1, 'title': draft['title'] or '原稿截图',
                     'defaultWatermark': None, 'slides': slides, 'excluded': []}
        else:
            value = copy.deepcopy(body.get('project'))
            if isinstance(value, dict):
                # Imported local projects retain artwork, never local file-write bindings.
                for slide in value.get('slides', []):
                    slide['manualAdded'] = True
                    slide['references'] = []
        validate(value)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise HTTPException(400, str(exc)) from None
    pid = secrets.token_hex(12)
    title = str(value.get('title') or '截图项目')[:300]
    now = db.now()
    with db.tx() as c:
        c.execute('INSERT INTO screenshot_projects VALUES(?,?,?,?,?,?,?,?,?)',
                  (pid,title,did,db.dumps(value),db.dumps(bindings),1,secrets.token_urlsafe(24),now,now))
    return {'id': pid, 'title': title, 'draft_id': did}


@router.get(PREFIX + '/{pid}/editor', response_class=HTMLResponse)
def editor(pid: str):
    project(pid)
    html = (ASSETS/'editor.html').read_text(encoding='utf-8')
    return HTMLResponse(html, headers={'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'"})


@router.get(PREFIX + '/{pid}/project.json')
def get_project(pid: str):
    return JSONResponse(db.loads(project(pid)['project']), headers={'Cache-Control': 'no-store'})


@router.get(PREFIX + '/{pid}/session')
def session(pid: str, include_project: bool = False):
    p = project(pid)
    draft = db.row('SELECT title FROM drafts WHERE id=?',(p['draft_id'],)) if p['draft_id'] else None
    saved_export=db.row('SELECT 1 AS found FROM screenshot_exports WHERE project_id=? LIMIT 1',(pid,))
    value={'token':p['write_token'], 'revision':p['revision'],
        'article': (draft['title'] or '原稿') if draft else None, 'defaultWatermark':None,
        'exported':bool(saved_export),'storage':'cloud'}
    if draft:
        _, _, active, _=return_plan(p)
        slides={s['id']:s for s in db.loads(p['project'])['slides']}
        value['returned']=sum(b.get('returned_state')==slide_hash(slides[sid]) for sid,b in active.items() if sid in slides)
        value['return_count']=len(active)
    if include_project:value['project']=db.loads(p['project'])
    return JSONResponse(value, headers={'Cache-Control':'no-store'})


def check_write(pid, request):
    p = project(pid)
    if not secrets.compare_digest(request.headers.get('X-Editor-Token', ''), p['write_token']):
        raise HTTPException(403, '截图项目编辑凭据无效，请刷新')
    if request.headers.get('X-Editor-Revision') != str(p['revision']):
        raise HTTPException(409, '项目已在其他页面更新，请刷新后再编辑')
    return p


@router.post(PREFIX + '/{pid}/save')
def save(pid: str, request: Request, body: dict):
    try:
        validate(body)
    except (ValueError, TypeError, KeyError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, str(exc)) from None
    with db.tx() as c:
        p = check_write(pid, request)
        old = db.loads(p['project'])
        original = {s['id']:s for s in old['slides']}
        bindings = db.loads(p['bindings'])
        for slide in body['slides']:
            if slide['id'] in original and slide['image'] != original[slide['id']]['image']:
                raise HTTPException(400, '不允许覆盖项目原图，请作为新图片加入')
            if slide['id'] in bindings and hashlib.sha256(slide['image'].encode()).hexdigest()!=bindings[slide['id']]['source_hash']:
                raise HTTPException(400,'原稿截图的原图不能被替换')
        # Previous version is kept alongside the current one in the database.
        c.execute('CREATE TABLE IF NOT EXISTS screenshot_project_history(project_id TEXT,revision INTEGER,project TEXT,created_at REAL,PRIMARY KEY(project_id,revision))')
        c.execute('INSERT OR IGNORE INTO screenshot_project_history VALUES(?,?,?,?)', (pid,p['revision'],p['project'],db.now()))
        c.execute('DELETE FROM screenshot_project_history WHERE project_id=? AND revision NOT IN (SELECT revision FROM screenshot_project_history WHERE project_id=? ORDER BY revision DESC LIMIT 5)',(pid,pid))
        c.execute('UPDATE screenshot_projects SET project=?,revision=revision+1,updated_at=? WHERE id=?', (db.dumps(body),db.now(),pid))
    return {'ok':True,'revision':p['revision']+1}


@router.post(PREFIX + '/{pid}/export')
def export(pid: str, request: Request, body: dict):
    p = check_write(pid, request)
    inputs = body.get('images', [])
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 64:
        raise HTTPException(400, '请选择要导出的图片')
    try:
        raw = [decode_image(row['png'], png_only=True) for row in inputs]
    except (ValueError, TypeError, KeyError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, str(exc)) from None
    output = io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for i,data in enumerate(raw):z.writestr(f'{i+1:03}-annotated.png',data)
        z.writestr('mapping.json', json.dumps(body.get('mapping',[]),ensure_ascii=False))
    batch = secrets.token_hex(10)
    single = body.get('single') is True
    data, mime, name = (raw[0],'image/png','001-annotated.png') if single else (output.getvalue(),'application/zip','screenshots.zip')
    with db.tx() as c:
        c.execute('INSERT INTO screenshot_exports VALUES(?,?,?,?,?,?)',(pid,batch,data,mime,name,db.now()))
    return {'path':'已保存到工作台私有内容库','batch':batch,'count':len(raw),
            'download':f'{PREFIX}/{pid}/exports/{batch}', 'revision':p['revision']}


@router.get(PREFIX + '/{pid}/exports/{bid}')
def download(pid: str, bid: str):
    project(pid)
    row = db.row('SELECT data,mime,filename FROM screenshot_exports WHERE project_id=? AND id=?',(pid,bid))
    if not row:raise HTTPException(404,'导出文件不存在')
    return Response(row['data'],media_type=row['mime'],headers={'Cache-Control':'no-store',
        'Content-Disposition':f'attachment; filename="{row["filename"]}"'})


def slide_hash(slide):
    return hashlib.sha256(json.dumps(slide,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def return_plan(p):
    d=db.row('SELECT * FROM drafts WHERE id=?',(p['draft_id'],)) if p['draft_id'] else None
    refs=image_refs(d['body']) if d else []
    bindings=db.loads(p['bindings'])
    active={sid:b for sid,b in bindings.items() if any(r['original']==b['url'] for r in refs)}
    return d,refs,active,bindings


def plan_token(p,d):
    return hashlib.sha256(db.dumps([p['id'],p['revision'],d['id'],d['title'],d['body'],d['updated_at']]).encode()).hexdigest()


@router.get(PREFIX + '/{pid}/return-plan')
def preview_return(pid: str):
    with db.tx():
        p=project(pid)
        d,refs,active,_=return_plan(p)
        if not d:raise HTTPException(409,'此项目尚未绑定工作台原稿，可编辑和导出')
        if d['status'] in ('generating','revising'):raise HTTPException(409,'正文仍在处理，请完成后再回填')
        if not active:raise HTTPException(409,'原稿已没有对应截图，未修改文章')
        slides={s['id']:s for s in db.loads(p['project'])['slides']}
        return {'title':d['title'] or '未命名稿件','plan_token':plan_token(p,d),'items':[
            {'id':sid,'name':slides[sid]['name'],'positions':[i for i,ref in enumerate(refs,1) if ref['original']==b['url']]}
            for sid,b in active.items() if sid in slides and not slides[sid].get('manualAdded')]}


@router.post(PREFIX + '/{pid}/return-article')
def return_article(pid: str, request: Request, body: dict):
    from .draft_workflow import copied_files
    with copied_files() as created, db.tx() as c:
        p = check_write(pid, request)
        d,current_refs,active,bindings=return_plan(p)
        if not d:raise HTTPException(409,'此项目尚未绑定工作台原稿，可编辑和导出')
        if d['status'] in ('generating','revising'):raise HTTPException(409,'正文仍在处理，请完成后再回填')
        if not active:raise HTTPException(409,'原稿已没有对应截图，未修改文章')
        if body.get('confirm') is not True or body.get('plan_token')!=plan_token(p,d):
            raise HTTPException(409,'请重新核对目标稿件和图片后确认，原稿未改动')
        slides={s['id']:s for s in db.loads(p['project'])['slides']}
        selected=body.get('selected_ids')
        if not isinstance(selected,list) or not selected or any(not isinstance(s,str) or s not in active or s not in slides or slides[s].get('manualAdded') for s in selected) or len(set(selected))!=len(selected):
            raise HTTPException(409,'选中的截图与原稿绑定不匹配，原稿未改动')
        chosen={sid:active[sid] for sid in selected}
        supplied = body.get('images',[])
        if not isinstance(supplied,list) or len(supplied)>64:raise HTTPException(400,'回填图片格式不正确')
        uploaded = {r['id']:r.get('png') for r in supplied if isinstance(r,dict) and isinstance(r.get('id'),str)}
        if len(uploaded)!=len(supplied) or chosen.keys()!=uploaded.keys():
            raise HTTPException(409,'回填图片与原稿绑定不匹配，请刷新项目')
        try: decoded={sid:decode_image(uploaded[sid],png_only=True) for sid in chosen}
        except (ValueError, TypeError, OSError, Image.DecompressionBombError) as exc:raise HTTPException(400,str(exc)) from None
        replacements, edits = {}, []
        for sid,b in chosen.items():
            digest=hashlib.sha256(decoded[sid]).hexdigest()
            bindings[sid]={**b,'returned_state':slide_hash(slides[sid])}
            if b.get('export_hash') == digest:
                continue
            raw=decoded[sid]
            file=secrets.token_hex(10)+'.png'
            path=images.images_dir()/file
            created.append(path)
            path.write_bytes(raw);path.chmod(0o600)
            with Image.open(io.BytesIO(raw)) as im:
                cursor=c.execute('INSERT INTO images(draft_id,name,file,mime,width,height,bytes,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    (d['id'],'截图标注-'+sid+'.png',file,'image/png',im.width,im.height,len(raw),db.now()))
            new_url=f'/api/images/{cursor.lastrowid}'
            replacements[b['url']]=new_url
            bindings[sid].update(url=new_url,export_hash=digest)
        for ref in current_refs:
            if ref['original'] in replacements:edits.append((ref['start'],ref['end'],replacements[ref['original']]))
        updated=d['body']
        for start,end,url in sorted(edits,reverse=True):updated=updated[:start]+url+updated[end:]
        if updated != d['body']:
            c.execute('INSERT INTO draft_versions(draft_id,title,body,origin,created_at) VALUES(?,?,?,?,?)',(d['id'],d['title'],d['body'],'before-screenshot-return',db.now()))
            # This transaction holds the database lock from read through replacement.
            c.execute('UPDATE drafts SET body=?,updated_at=? WHERE id=?',(updated,db.now(),d['id']))
        c.execute('UPDATE screenshot_projects SET bindings=?,revision=revision+1,updated_at=? WHERE id=?',(db.dumps(bindings),db.now(),pid))
        revision=p['revision']+1
        returned=sum(
            bindings[sid].get('returned_state')==slide_hash(slides[sid]) for sid in active if sid in slides)
    return {'count':sum(1 for r in current_refs if any(r['original']==b['url'] for b in chosen.values())),
        'skipped':len(bindings)-len(active),'mode':'replace','backup':'原稿历史版本已保留',
        'revision':revision,'draft_id':d['id'],'changed':updated!=d['body'],'returned':returned,
        'return_count':len(active),'title':d['title'] or '未命名稿件'}

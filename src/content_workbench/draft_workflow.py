"""Human review and version lineage; never rewrites a source draft."""
import hashlib
import uuid
from contextlib import contextmanager
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from markdown_it import MarkdownIt
from . import comments, db, learning, jobs, llm, idea_cards, ai_rules, images
from .config import Settings
from .platforms import PLATFORMS, writing_prompt

router = APIRouter()
KINDS={'xiaohongshu':'xiaohongshu','video':'douyin','long':'wechat','wechat':'wechat','x_article':'wechat'}

def setup():
    with db.tx() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS draft_reviews(
          draft_id INTEGER PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
          body_hash TEXT NOT NULL DEFAULT '', layout_hash TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS final_copies(
          draft_id INTEGER PRIMARY KEY,
          platform TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
          body_hash TEXT NOT NULL, saved_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS final_copy_history(
          draft_id INTEGER NOT NULL, version INTEGER NOT NULL, platform TEXT NOT NULL,
          title TEXT NOT NULL, body TEXT NOT NULL, body_hash TEXT NOT NULL, saved_at REAL NOT NULL,
          PRIMARY KEY(draft_id,version));
        CREATE TABLE IF NOT EXISTS final_copy_images(
          draft_id INTEGER NOT NULL, image_id INTEGER NOT NULL, mime TEXT NOT NULL, data BLOB NOT NULL,
          PRIMARY KEY(draft_id,image_id));
        CREATE TABLE IF NOT EXISTS draft_lineage(
          draft_id INTEGER PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
          request_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, snapshot TEXT NOT NULL, model TEXT,
          created_at REAL NOT NULL);
        """)
        # Legacy rows already hold the confirmed text. Preserve still-available
        # inline images before later edits or deletion of the source draft.
        for row in c.execute("SELECT draft_id,body FROM final_copies").fetchall():
            pin_final_images(c,row['draft_id'],row['body'])


def final_image_ids(body):
    return {int(src[12:]) for token in MarkdownIt('commonmark',{'html':False}).parse(body or '')
            for child in token.children or [] if child.type=='image'
            and (src:=child.attrGet('src') or '').startswith('/api/images/') and src[12:].isascii() and src[12:].isdigit()
            and len(src[12:])<=18}


def pin_final_images(c,did,body):
    for iid in final_image_ids(body):
        if c.execute('SELECT 1 FROM final_copy_images WHERE draft_id=? AND image_id=?',(did,iid)).fetchone():
            continue
        im=images.get(iid)
        if not im or im['draft_id']!=did:
            continue
        try: raw=images.path_of(im).read_bytes()
        except OSError: continue
        c.execute('INSERT INTO final_copy_images VALUES(?,?,?,?)',(did,iid,im['mime'],raw))


def latest_version(did):
    return db.row('SELECT COALESCE(MAX(version),0)+1 AS n FROM final_copy_history WHERE draft_id=?',(did,))['n']

def draft(did):
    row=db.row('SELECT * FROM drafts WHERE id=?',(did,))
    if not row:raise HTTPException(404,'稿件不存在')
    return row

def signatures(d):
    body=hashlib.sha256(db.dumps([d['title'],d['body']]).encode()).hexdigest()
    layout=hashlib.sha256(db.dumps([body,*[d.get(k) for k in ('wechat_theme','summary','source_url','cover_wechat','cover_x','cover_crop','xhs')],
        Settings.load().wechat_branding,db.row('SELECT number FROM wechat_brand_numbers WHERE draft_id=?',(d['id'],))]).encode()).hexdigest()
    return body,layout

def review(did):
    d=draft(did);b,l=signatures(d)
    saved=db.row('SELECT * FROM draft_reviews WHERE draft_id=?',(did,)) or {}
    threads=comments.open_actions(did)
    issues=[{'id':a['id'],'quote':a['quote'][:90]} for a in db.loads(d.get('review_issues'),[])
            if isinstance(a,dict) and 'id' in a and isinstance(a.get('quote'),str)]
    for t in threads:
        t['body']=t['body'][:180]
        t['quote']=t['quote'][:90] if t['quote'] else None
        t['waiting_ai']=(t.pop('last_author') or t['author'])=='gary'
    reasons=[]
    if threads:reasons.append(f'{len(threads)} 条批注未解决')
    if issues:reasons.append(f'{len(issues)} 处待改标记未清除')
    if d['status'] in ('generating','revising'):reasons.append('正文仍在处理')
    elif d['status']=='failed':reasons.append('正文起草失败')
    if not d['body'].strip():reasons.append('正文为空')
    body_ok=not reasons and saved.get('body_hash')==b
    return {'body_ok':body_ok,'layout_ok':body_ok and saved.get('layout_hash')==l,'open_comments':len(threads),
            'marks':len(issues),'body_hash':b,'layout_hash':l,'blocked':bool(reasons),
            'open_threads':threads,'review_marks':issues,'blockers':reasons}


def require_review(did, layout=False):
    value=review(did)
    if not value['layout_ok' if layout else 'body_ok']:
        raise HTTPException(409,'请先解决全部批注和待改标记，并确认最新'+('正文与排版' if layout else '正文'))
    return value

def reviewed_draft(did,layout=True):
    with db.tx():
        d=draft(did)
        require_review(did,layout=layout)
        return d


@router.get('/api/drafts/{did}/workflow')
def workflow(did:int):
    value=review(did)
    value['draft_status']=draft(did)['status']
    final=db.row('SELECT body_hash,saved_at FROM final_copies WHERE draft_id=?',(did,))
    value['final_copy']={'saved_at':final['saved_at'],'current':final['body_hash']==value['body_hash']} if final else None
    row=db.row('SELECT * FROM draft_lineage WHERE draft_id=?',(did,))
    value['lineage']={**row,'snapshot':db.loads(row['snapshot'],{})} if row else None
    value['variants']=[{'id':r['draft_id'],'kind':r['kind'],'title':r['title'],'status':r['status']} for r in
        db.rows('SELECT l.*,d.title,d.status FROM draft_lineage l JOIN drafts d ON d.id=l.draft_id')
        if any(s['id']==did for s in db.loads(r['snapshot'],{}).get('sources',[]))]
    value['habits']=[{'id':h['id'],'rule':h['rule']} for h in learning.load().get('lessons',[])
                     if h.get('enabled',True) and (h.get('source')=='gary' or h.get('reviewed') is True)]
    return value

@router.post('/api/drafts/{did}/approve')
def approve(did:int,body:dict):
    if body.get('confirm') is not True or body.get('stage') not in ('body','layout'):
        raise HTTPException(400,'请明确确认正文或排版')
    with db.tx() as c:
        v=review(did)
        key='body_hash' if body['stage']=='body' else 'layout_hash'
        if body.get('hash')!=v[key]:raise HTTPException(409,'稿件或排版已改变，请重新检查')
        if v['blocked']:raise HTTPException(409,'还有批注、待改标记或未完成的正文')
        if body['stage']=='layout' and not v['body_ok']:raise HTTPException(409,'请先确认正文')
        old=c.execute('SELECT * FROM draft_reviews WHERE draft_id=?',(did,)).fetchone()
        c.execute('INSERT OR REPLACE INTO draft_reviews VALUES(?,?,?,?)',
            (did,v['body_hash'],v['layout_hash'] if body['stage']=='layout' else (old['layout_hash'] if old and old['body_hash']==v['body_hash'] else ''),db.now()))
    return workflow(did)

@router.post('/api/drafts/{did}/final-copy')
def save_final_copy(did: int, body: dict):
    if body.get('confirm') is not True:
        raise HTTPException(400, '请确认将正文存入成稿箱')
    with db.tx() as c:
        d = draft(did)
        v = review(did)
        if body.get('hash') != v['body_hash'] or body.get('expected_updated_at') != d['updated_at']:
            raise HTTPException(409, '正文已改变，请重新检查')
        if v['blocked']:
            raise HTTPException(409, '暂不能存入成稿箱：'+'；'.join(v['blockers']))
        old = c.execute('SELECT body_hash,layout_hash FROM draft_reviews WHERE draft_id=?',(did,)).fetchone()
        saved = c.execute('SELECT * FROM final_copies WHERE draft_id=?',(did,)).fetchone()
        now = db.now()
        layout_hash = old['layout_hash'] if old and old['body_hash'] == v['body_hash'] else ''
        c.execute('INSERT OR REPLACE INTO draft_reviews VALUES(?,?,?,?)',(did,v['body_hash'],layout_hash,now))
        version=latest_version(did)
        if saved and saved['body'] and saved['body_hash'] == v['body_hash']:
            return {'draft_id':did,'saved_at':saved['saved_at'],'version':version}
        if saved:
            pin_final_images(c,did,saved['body'])
            c.execute('INSERT INTO final_copy_history SELECT draft_id,?,platform,title,body,body_hash,saved_at FROM final_copies WHERE draft_id=?',(version,did))
            version+=1
        pin_final_images(c,did,d['body'])
        c.execute('INSERT OR REPLACE INTO final_copies(draft_id,platform,title,body,body_hash,saved_at) VALUES(?,?,?,?,?,?)',
                  (did,d['platform'],d['title'],d['body'],v['body_hash'],now))
    return {'draft_id':did,'saved_at':now,'version':version}


@router.get('/api/final-drafts')
def final_copies(q: str = '', platform: str = ''):
    q = q.strip()
    if len(q) > 300 or (platform and platform not in PLATFORMS):
        raise HTTPException(400, '标题关键词或平台无效')
    return db.rows("""SELECT draft_id,platform,title,substr(body,1,140) preview,saved_at FROM final_copies
        WHERE (?='' OR instr(lower(title),lower(?))>0) AND (?='' OR platform=?)
        ORDER BY saved_at DESC,draft_id DESC LIMIT 300""",(q,q,platform,platform))


@router.get('/api/final-drafts/{did}')
def final_copy(did: int, version: int | None = None):
    with db.tx():
        return confirmed_snapshot(did,version)


def confirmed_snapshot(did,version):
    row = db.row('''SELECT f.draft_id,f.platform,f.title,f.body,f.saved_at,d.id IS NOT NULL AS has_original
        FROM final_copies f LEFT JOIN drafts d ON d.id=f.draft_id WHERE f.draft_id=?''',(did,))
    if not row:
        raise HTTPException(404, '这篇还没有存入成稿箱')
    current=latest_version(did)
    if version is not None and version!=current:
        previous=db.row('SELECT draft_id,platform,title,body,saved_at FROM final_copy_history WHERE draft_id=? AND version=?',(did,version))
        if not previous:raise HTTPException(404,'找不到该确认版本')
        row={**previous,'has_original':row['has_original']}
    row['version']=current if version is None else version
    row['versions']=[{'version':current,'saved_at':db.row('SELECT saved_at FROM final_copies WHERE draft_id=?',(did,))['saved_at']},
                     *db.rows('SELECT version,saved_at FROM final_copy_history WHERE draft_id=? ORDER BY version DESC',(did,))]
    pinned={r['image_id'] for r in db.rows('SELECT image_id FROM final_copy_images WHERE draft_id=?',(did,))}
    row['image_ids']=sorted(final_image_ids(row['body']) & pinned)
    return row


@router.get('/api/final-drafts/{did}/images/{iid}')
def final_image(did: int,iid: int):
    row=db.row('SELECT mime,data FROM final_copy_images WHERE draft_id=? AND image_id=?',(did,iid))
    if not row:raise HTTPException(404,'确认版本的图片未保存')
    return Response(row['data'],media_type=row['mime'],headers={'Cache-Control':'no-store'})


@contextmanager
def copied_files():
    paths=[]
    try:
        yield paths
    except BaseException:
        for path in paths:path.unlink(missing_ok=True)
        raise


def key(body):
    try:return str(uuid.UUID(body.get('request_key','')))
    except (ValueError,TypeError,AttributeError):raise HTTPException(400,'请求编号无效') from None

@router.post('/api/drafts/compose')
def compose(body:dict):
    request_key=key(body)
    kind=body.get('kind')
    ids=body.get('source_ids')
    if kind not in KINDS or not isinstance(ids,list) or not 1<=len(ids)<=8 or any(type(x) is not int for x in ids) or len(set(ids))!=len(ids):
        raise HTTPException(400,'请选择1至8篇来源稿件')
    if kind=='long' and len(ids)<2:raise HTTPException(400,'合成长文至少选两篇')
    if kind!='long' and len(ids)!=1:raise HTTPException(400,'平台版本请选择一篇来源')
    angle=body.get('requirements','')
    if not isinstance(angle,str) or len(angle)>4000:raise HTTPException(400,'本篇要求最多4000字')
    mode=body.get('mode','copy')
    if mode not in ('copy','ai'):raise HTTPException(400,'生成方式无效')
    model=body.get('writing_model') or Settings.load().writing_model
    if mode=='ai' and model not in llm.WRITING_MODELS:raise HTTPException(400,'请选择已接通的写作型号')
    signature=hashlib.sha256(db.dumps([kind,ids,angle,mode,model if mode=='ai' else None]).encode()).hexdigest()
    with copied_files() as created_files, db.tx() as c:
        old=c.execute('SELECT * FROM draft_lineage WHERE request_key=?',(request_key,)).fetchone()
        if old:
            if db.loads(old['snapshot'],{}).get('signature')!=signature:raise HTTPException(409,'该请求编号已用于其他内容')
            return {'id':old['draft_id'],'existing':True}
        sources=[]
        total=0
        for did in ids:
            require_review(did)
            d=draft(did);total+=len(d['body'])
            if total>65000:raise HTTPException(400,'来源正文合计过长，请减少本次篇数')
            sources.append({k:d[k] for k in ('id','title','body','updated_at','writing_context','cover_wechat','cover_x','cover_crop')})
        style=idea_cards.style_context()
        snapshot={'sources':sources,'requirements':angle,'mode':mode,'style':style,'signature':signature}
        title=sources[0]['title'] if kind!='long' else (angle.splitlines()[0][:150] if angle.strip() else '合稿：'+sources[0]['title'])
        body_text='\n\n'.join(('## '+s['title']+'\n\n' if kind=='long' else '')+s['body'] for s in sources)
        did=c.execute("INSERT INTO drafts(platform,title,body,notes,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (KINDS[kind],title,'' if mode=='ai' else body_text,angle+('\n约3分钟口播，以试读时长为准。' if kind=='video' else ''),
             'generating' if mode=='ai' else 'draft',db.now(),db.now())).lastrowid
        # Each image record belongs to its own draft; keep the source image immutable.
        mapping={}
        for src in sources:
            for iid in dict.fromkeys([*images.referenced(src['body']),*[src[k] for k in ('cover_wechat','cover_x') if src[k]]]):
                if iid in mapping:continue
                old_image=images.get(iid)
                if not old_image or old_image['draft_id']!=src['id']:raise HTTPException(409,'来源图片无法确认归属，请先核对原稿')
                try:raw=images.path_of(old_image).read_bytes()
                except OSError:raise HTTPException(409,'来源图片文件不可读取，请先核对原稿；未建立新稿') from None
                path=images.images_dir()/(uuid.uuid4().hex+images.path_of(old_image).suffix)
                created_files.append(path);path.write_bytes(raw);path.chmod(0o600)
                mapping[iid]=c.execute('INSERT INTO images(draft_id,name,file,mime,width,height,bytes,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    (did,old_image['name'],path.name,old_image['mime'],old_image['width'],old_image['height'],len(raw),db.now())).lastrowid
            c.execute('INSERT OR IGNORE INTO draft_benchmarks(draft_id,benchmark_id,analysis_id,created_at) SELECT ?,benchmark_id,analysis_id,? FROM draft_benchmarks WHERE draft_id=?',(did,db.now(),src['id']))
        first=sources[0]
        c.execute('UPDATE drafts SET cover_wechat=?,cover_x=?,cover_crop=? WHERE id=?',
            (mapping.get(first['cover_wechat']),mapping.get(first['cover_x']),first['cover_crop'],did))
        body_text=images.REF_RE.sub(lambda m:'/api/images/'+str(mapping[int(m[1])]),body_text)
        snapshot['image_map']=mapping
        c.execute('INSERT INTO draft_lineage VALUES(?,?,?,?,?,?)',(did,request_key,kind,db.dumps(snapshot),model if mode=='ai' else None,db.now()))
        if mode=='copy':
            c.execute('UPDATE drafts SET body=? WHERE id=?',(body_text,did))
            c.execute("INSERT INTO draft_versions(draft_id,title,body,created_at,origin) VALUES(?,?,?,?,'edit')",(did,title,body_text,db.now()))
        else:
            c.execute('INSERT INTO jobs(kind,payload,created_at,updated_at) VALUES(?,?,?,?)',
                ('derive_draft',db.dumps({'draft_id':did}),db.now(),db.now()))
    if mode=='ai':jobs.wake()
    return {'id':did}

def derive_job(did):
    from dataclasses import replace
    d=draft(did);line=db.row('SELECT * FROM draft_lineage WHERE draft_id=?',(did,))
    snapshot=db.loads(line['snapshot'],{})
    if d['status']!='generating':return
    material='\n\n'.join('## '+s['title']+'\n\n'+s['body'] for s in snapshot['sources'])
    purpose={'xiaohongshu':'小红书短笔记','video':'约3分钟口播，以试读时长为准','long':'整合来源的连贯长文章','wechat':'公众号文章','x_article':'X Articles长文章'}[line['kind']]
    angle='本次用途：'+purpose+'。本篇要求：'+snapshot['requirements']
    prompt=ai_rules.apply(writing_prompt(d['platform'],material,angle,style=snapshot['style']),snapshot['style']['working_rules'])
    ai_rules.record('writing',did,'derive',line['model'],prompt)
    reply=llm.extract_json(llm.run(prompt,configuration=replace(Settings.load(),writing_model=line['model']),task='write',json_output=True,timeout=1200))
    if not isinstance(reply,dict) or any(not isinstance(reply.get(k),str) or not reply[k].strip() for k in ('title','body')):
        raise ValueError('模型没有返回有效完整稿件，来源原稿未修改')
    output=reply['body']
    if len(output)>100000:raise ValueError('返回正文过长，未保存')
    mapping=snapshot['image_map']
    for m in images.REF_RE.finditer(output):
        if m[1] not in mapping:raise ValueError('模型引用了不属于来源的图片，未保存')
    output=images.REF_RE.sub(lambda m:'/api/images/'+str(mapping[m[1]]),output)
    notes=reply.get('notes') if isinstance(reply.get('notes'),str) else ''
    with db.tx() as c:
        current=c.execute('SELECT body,status FROM drafts WHERE id=?',(did,)).fetchone()
        if current['body'] or current['status']!='generating':raise RuntimeError('当前稿件已修改，未覆盖')
        c.execute("UPDATE drafts SET title=?,body=?,notes=?,status='draft',updated_at=? WHERE id=?",
                  (reply['title'][:300],output,notes,db.now(),did))
        c.execute("INSERT INTO draft_versions(draft_id,title,body,created_at,origin) VALUES(?,?,?,?,'generated')",(did,reply['title'][:300],output,db.now()))


@router.post('/api/drafts/{did}/habit-candidates')
def habit_candidate(did:int,body:dict):
    draft(did)
    comment=db.row('SELECT * FROM comments WHERE id=? AND draft_id=? AND author=?',(body.get('comment_id'),did,'gary'))
    rule=body.get('rule','')
    if not comment or not isinstance(rule,str) or not rule.strip() or len(rule)>300:
        raise HTTPException(400,'请填写这条批注可以长期复用的规则，最多300字')
    state=learning.load()
    existing=next((h for h in state['lessons'] if h['rule']==rule.strip()),None)
    if existing:return {'id':existing['id'],'existing':True}
    h={'id':uuid.uuid4().hex[:8],'rule':rule.strip(),'source':'auto','enabled':False,'reviewed':False,
       'draft_id':did,'comment_id':comment['id'],'examples':[],'evidence':1}
    state['lessons'].append(h);learning.save(state)
    return {'id':h['id'],'notice':'已存待确认习惯；确认启用后才用于后续写作'}


@router.post('/api/drafts/{did}/retry-derived')
def retry_derived(did:int,body:dict):
    with db.tx() as c:
        d=draft(did)
        line=c.execute('SELECT * FROM draft_lineage WHERE draft_id=?',(did,)).fetchone()
        if not line or not line['model'] or body.get('confirm') is not True:raise HTTPException(400,'请确认重试原派生稿模型')
        if d['status']!='failed' or d['body'].strip():raise HTTPException(409,'仅可重试失败的空白派生稿，已有正文不会覆盖')
        if c.execute("SELECT 1 FROM jobs WHERE kind='derive_draft' AND state IN ('queued','running') AND json_extract(payload,'$.draft_id')=?",(did,)).fetchone():raise HTTPException(409,'该稿正在生成')
        c.execute("UPDATE drafts SET status='generating',error=NULL,updated_at=? WHERE id=?",(db.now(),did))
        c.execute("INSERT INTO jobs(kind,payload,created_at,updated_at) VALUES('derive_draft',?,?,?)",(db.dumps({'draft_id':did}),db.now(),db.now()))
    jobs.wake()
    return {'id':did,'model':line['model']}

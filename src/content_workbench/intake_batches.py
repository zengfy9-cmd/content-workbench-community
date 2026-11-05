"""Batch execution records linking existing originals, media checkpoints and research jobs."""
from contextlib import contextmanager
import hashlib
import re
import threading
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from fastapi import APIRouter, Body, File, HTTPException, UploadFile, Query
from . import benchmarks, db, jobs, media_imports as media, references, research_protocol

router=APIRouter()
_locks={}
_guard=threading.Lock()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_url(value):
    value=references.article_url(value.rstrip('，。；、)]}）】〉》’\''))
    p=urlsplit(value)
    query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith('utm_')]
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path,urlencode(query),''))


def platform(url):
    host=(urlsplit(url).hostname or '').lower()
    return next((name for domain,name in media.HOSTS.items() if host==domain or host.endswith('.'+domain)), '网页')


def is_media(item):
    return bool(item.get('media_id')) or item['input_kind']=='file' or (item['input_kind']=='url' and platform(item['input_value']) not in ('公众号','网页') and 'scys.com' not in (urlsplit(item['input_value']).hostname or ''))


@router.post('/api/intake-batches/preview')
def preview(body:dict=Body(...)):
    text=media.text(body.get('text',''))
    urls=re.findall(r'https?://[^\s<>"\u3000]+',text,re.I)
    if not urls:raise HTTPException(400,'没有识别到链接；已有正文请用“添加正文 / 我的记录”')
    items=[]
    for url in urls:
        try:
            value=normalize_url(url)
            items.append({'input_kind':'url','input_value':value,'title':'','platform':platform(value)})
        except ValueError:
            items.append({'input_kind':'url','input_value':url,'title':'','error':'链接格式无效，可移除此项后继续'})
    return {'items':items}


@router.post('/api/intake-batches/upload')
async def upload(file:UploadFile=File(...)):
    item=await media.accept_upload(file,staged=True)
    return {'input_kind':'file','input_value':str(item['id']),'title':item['title'],'platform':'本地文件','name':item['upload_name']}


def effective(item,seen=None):
    seen=set() if seen is None else seen
    if item['id'] in seen:return {**item,'state':'failed','error':'重复引用异常，请重新提交'}
    seen.add(item['id'])
    if item['state']=='paused':return item
    if item['duplicate_of']:
        other=db.row('SELECT * FROM intake_items WHERE id=?',(item['duplicate_of'],))
        if other:
            resolved=effective(other,seen)
            return {**item,**{k:resolved.get(k) for k in ('state','error','media_id','benchmark_id','material_id','analysis_id','analysis_job_id')},'title':item['title'] or resolved['title'],'reused':True}
    if item['analysis_job_id']:
        job=db.row('SELECT state,error FROM jobs WHERE id=?',(item['analysis_job_id'],))
        if job and job['state']=='done':
            analysis=db.row("SELECT id FROM benchmark_analyses WHERE benchmark_id=? AND json_extract(result,'$.metadata.job_id')=? ORDER BY id DESC LIMIT 1",(item['benchmark_id'],item['analysis_job_id']))
            return {**item,'state':'done' if analysis else 'failed','analysis_id':analysis['id'] if analysis else None,'error':'' if analysis else '原文变化或任务被替换，本次研究未写入；原文保留'}
        if job and job['state'] in ('failed','cancelled'):
            return {**item,'state':'failed','error':'研究未完成；原文已保留，可仅重试研究。'+(job.get('error') or '')[:180]}
    return item


def bundle(batch_id):
    batch=db.row('SELECT * FROM intake_batches WHERE id=?',(batch_id,))
    if not batch:raise HTTPException(404,'批次不存在')
    items=[]
    for row in db.rows('SELECT * FROM intake_items WHERE batch_id=? ORDER BY id',(batch_id,)):
        item=effective(row)
        route=db.loads(row['route'],{})
        item['model']=route.get('model','不调用研究模型')
        item['rule_version']=route.get('rule_version','仅归档')
        item.pop('route',None)
        item.pop('supplement_body',None)
        # Full originals stay in their existing source views, not list polling.
        if item['input_kind']=='text':item['input_value']=item['input_value'][:100]
        items.append(item)
    return {'id':batch_id,'created_at':batch['created_at'],'items':items,'active':any(i['state'] in ('queued','acquiring','researching') for i in items)}


@router.get('/api/intake-batches')
def listing(offset:int=Query(0,ge=0)):
    return [bundle(b['id']) for b in db.rows('SELECT id FROM intake_batches ORDER BY id DESC LIMIT 30 OFFSET ?',(offset,))]


@router.get('/api/intake-batches/{batch_id}')
def detail(batch_id:int):return bundle(batch_id)


def prepare(value,default):
    if not isinstance(value,dict):raise HTTPException(400,'素材清单格式无效')
    kind=value.get('input_kind');raw=media.text(value.get('input_value',''))
    if kind not in ('url','text','file') or not raw:raise HTTPException(400,'每条素材需要有效链接、正文或已上传文件')
    title=media.text(value.get('title',''),300)
    dest=value.get('destination','benchmark');depth=value.get('depth',default.get('depth','basic'))
    if dest not in ('benchmark','material') or depth not in ('archive','basic','deep'):raise HTTPException(400,'保存去向或研究深度无效')
    if dest=='material' and depth!='archive':raise HTTPException(400,'自己的记录请先归档到素材；研究对标请选择对标库')
    asr=default.get('asr_model',value.get('asr_model','base'))
    if asr not in ('base','small','medium'):raise HTTPException(400,'转写型号无效')
    route=research_protocol.resolve(value.get('model',default.get('model','current'))) if depth!='archive' else {}
    if kind=='url':
        try:raw=normalize_url(raw)
        except references.ReferenceError as exc:raise HTTPException(400,str(exc)) from None
        source='url:'+raw
    elif kind=='file':
        if not raw.isdigit():raise HTTPException(400,'上传文件标识无效')
        row=media.require(int(raw))
        if not row.get('extraction_only') or not row.get('file_hash'):raise HTTPException(400,'请选择批量入口上传的文件')
        source='file:'+row['file_hash']
    else:source='text:'+digest(raw)
    fingerprint=digest(db.dumps({'source':source,'depth':depth,'destination':dest,'route':route,'asr':asr}))
    return {'input_kind':kind,'input_value':raw,'title':title,'depth':depth,'destination':dest,'route':db.dumps(route),'fingerprint':fingerprint,'asr_model':asr}


def enqueue(c,item,depends_on=None):
    if depends_on is not None and c.execute('SELECT state FROM intake_items WHERE id=?',(item['id'],)).fetchone()[0]=='paused':return
    kind='intake_media' if is_media(item) else 'intake_item'
    jid=c.execute('INSERT INTO jobs(kind,payload,depends_on,created_at,updated_at) VALUES(?,?,?,?,?)',(kind,'{}',depends_on,db.now(),db.now())).lastrowid
    c.execute('UPDATE jobs SET payload=? WHERE id=?',(db.dumps({'item_id':item['id'],'job_id':jid}),jid))
    c.execute("UPDATE intake_items SET state='queued',error='',job_id=?,analysis_job_id=NULL,duplicate_of=NULL,updated_at=? WHERE id=?",(jid,db.now(),item['id']))


@router.post('/api/intake-batches')
def create(body:dict=Body(...)):
    key=media.text(body.get('request_key',''),120)
    values=body.get('items');default=body.get('defaults',{})
    if not key or not isinstance(values,list) or not values or not isinstance(default,dict):raise HTTPException(400,'请先建立素材清单')
    signature=digest(db.dumps({'items':values,'defaults':default}))
    old=db.row('SELECT * FROM intake_batches WHERE request_key=?',(key,))
    if old:
        if old['signature']!=signature:raise HTTPException(409,'提交标识已用于另一批内容，请重新识别清单')
        return bundle(old['id'])
    prepared=[prepare(v,default) for v in values]
    with db.tx() as c:
        old=c.execute('SELECT * FROM intake_batches WHERE request_key=?',(key,)).fetchone()
        if old:
            if old['signature']!=signature:raise HTTPException(409,'提交标识已用于另一批内容，请重新识别清单')
            return bundle(old['id'])
        bid=c.execute('INSERT INTO intake_batches(request_key,signature,created_at) VALUES(?,?,?)',(key,signature,db.now())).lastrowid
        for value in prepared:
            # Failed attempts remain retryable; only working or completed requests are reused.
            old=None
            for candidate in c.execute('SELECT * FROM intake_items WHERE fingerprint=? ORDER BY id DESC',(value['fingerprint'],)).fetchall():
                if effective(dict(candidate))['state'] in ('queued','acquiring','researching','done'):
                    old=candidate;break
            iid=c.execute('INSERT INTO intake_items(batch_id,input_kind,input_value,fingerprint,title,depth,destination,route,asr_model,duplicate_of,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(bid,*(value[k] for k in ('input_kind','input_value','fingerprint','title','depth','destination','route','asr_model')),old['id'] if old else None,db.now(),db.now())).lastrowid
            if not old:enqueue(c,{**value,'id':iid})
    jobs.wake()
    return bundle(bid)


@contextmanager
def source_lock(key):
    with _guard:
        lock,count=_locks.get(key,(threading.Lock(),0));_locks[key]=(lock,count+1)
    try:
        with lock:yield
    finally:
        with _guard:
            _,count=_locks[key]
            if count==1:del _locks[key]
            else:_locks[key]=(lock,count-1)


def update(iid,**fields):
    with db.tx() as c:c.execute('UPDATE intake_items SET '+','.join(k+'=?' for k in fields)+',updated_at=? WHERE id=?',(*fields.values(),db.now(),iid))


def original(item):
    if item['benchmark_id']:
        return benchmarks.require(item['benchmark_id'])
    if item['supplement_body']:
        return {'body':item['supplement_body'],'title':item['title'] or '补充原文','url':item['input_value'] if item['input_kind']=='url' else '', 'author':''}
    kind,raw=item['input_kind'],item['input_value']
    if kind=='url':
        known=db.row('SELECT id FROM benchmarks WHERE url=? ORDER BY id LIMIT 1',(raw,))
        if known:return benchmarks.require(known['id'])
    if kind=='text':return {'body':raw,'title':item['title'] or '导入正文','url':'','author':''}
    if is_media(item):
        mid=item['media_id'] or (int(raw) if kind=='file' else None)
        if kind=='file' and mid:
            uploaded=media.require(mid)
            existing=db.row("SELECT id FROM media_imports WHERE file_hash=? AND raw<>'' ORDER BY id LIMIT 1",(uploaded['file_hash'],))
            if existing:mid=existing['id']
        if not mid:
            old=db.row('SELECT id FROM media_imports WHERE url=? AND raw<>? ORDER BY id LIMIT 1',(raw,''))
            mid=old['id'] if old else media.new(item['title'] or '待识别标题','视频',raw,platform(raw))
        update(item['id'],media_id=mid)
        row=media.require(mid)
        if not row['raw']:
            if row['url']:
                parsed=urlsplit(row['url'])
                references._addresses(parsed.hostname,443 if parsed.scheme=='https' else 80)
            media.write(mid,extraction_only=1,asr_model=item['asr_model'],job_id=item['job_id'],state='queued')
            media.run({'media_id':mid})
            row=media.require(mid)
        if item['depth']!='archive' and row.get('extraction_only'):
            media.workbench_distill(mid,route=db.loads(item['route'],{}))
            base=media.folder(mid)
            media.write(mid,clean=(base/'clean.txt').read_text(encoding='utf-8'),article=(base/'article.md').read_text(encoding='utf-8'),extraction_only=0)
            row=media.require(mid)
        return {'body':row['raw'] if item['depth']=='archive' else row['article'],'title':row['original_title'] or row['title'],'url':raw if kind=='url' else row['url'],'author':row['author'],'media_id':mid}
    return benchmarks.read_source(raw)


def register(item,article):
    with db.tx() as c:
        c.execute("UPDATE intake_items SET title=CASE WHEN title='' THEN ? ELSE title END WHERE id=?",(article['title'],item['id']))
    if item['destination']=='material':
        key='batch:'+digest(article['body'])
        with db.tx() as c:
            old=c.execute('SELECT id FROM materials WHERE source_ref=?',(key,)).fetchone()
            mid=old[0] if old else c.execute("INSERT INTO materials(source,source_ref,title,body,status,media_id,created_at) VALUES('paste',?,?,?,'ready',?,?)",(key,item['title'] or article['title'],article['body'],article.get('media_id'),db.now())).lastrowid
            c.execute("UPDATE intake_items SET material_id=?,state=CASE WHEN state='paused' THEN state ELSE 'done' END,updated_at=? WHERE id=?",(mid,db.now(),item['id']))
        return None
    known=article.get('id') or (db.row('SELECT id FROM benchmarks WHERE body_hash=? AND url=? ORDER BY id LIMIT 1',(benchmarks.body_hash(article['body']),article.get('url',''))) or {}).get('id')
    source=benchmarks.require(known) if known else benchmarks.add_benchmark({**article,'tags':article.get('tags') or media.suggest_tags(article['body']),'title':item['title'] or article['title'],'source':'web' if article.get('url') else 'paste'})
    with db.tx() as c:
        if article.get('media_id'):
            c.execute('UPDATE benchmarks SET media_id=? WHERE id=? AND media_id IS NULL',(article['media_id'],source['id']))
            c.execute('UPDATE media_imports SET benchmark_id=? WHERE id=?',(source['id'],article['media_id']))
        c.execute('UPDATE intake_items SET benchmark_id=?,updated_at=? WHERE id=?',(source['id'],db.now(),item['id']))
    return source


def run(payload):
    item=db.row('SELECT * FROM intake_items WHERE id=?',(payload['item_id'],))
    if not item or item['job_id']!=payload.get('job_id',item['job_id']) or item['duplicate_of'] or item['state']=='done' or item['analysis_job_id']:return
    if cancelled(item):return
    update(item['id'],state='acquiring',error='')
    with source_lock(item['input_kind']+':'+item['input_value']):
        article=original(item)
        if cancelled(item):return
        # An earlier archive may still contain the untouched ASR export. Upgrade
        # its reading layer before research, retaining raw in media + old analyses.
        if item['depth']!='archive' and article.get('media_id'):
            row=media.require(article['media_id'])
            if article['body']==row['raw']:
                if row.get('extraction_only'):
                    media.workbench_distill(row['id'],route=db.loads(item['route'],{}))
                    base=media.folder(row['id'])
                    media.write(row['id'],clean=(base/'clean.txt').read_text(encoding='utf-8'),article=(base/'article.md').read_text(encoding='utf-8'),extraction_only=0)
                    row=media.require(row['id'])
                if cancelled(item):return
                if article.get('id'):
                    (media.folder(row['id'])/('source-before-reading-'+str(article['id'])+'-'+article['body_hash']+'.json')).write_text(db.dumps(article),encoding='utf-8')
                    with db.tx() as c:
                        changed=c.execute("UPDATE benchmarks SET body=?,body_hash=?,original_html='',updated_at=? WHERE id=? AND body_hash=? AND analysis_state NOT IN ('queued','running')",(row['article'],benchmarks.body_hash(row['article']),db.now(),article['id'],article['body_hash'])).rowcount
                        if not changed:raise RuntimeError('来源正在修改或研究，请稍后重试')
                article={**article,'body':row['article']}
        if not article.get('body','').strip():raise RuntimeError('未取得有效原文；请补充文件或正文')
        source=register(item,article)
        if source is None:return
        if cancelled(item):return
        if item['depth']=='archive':update(item['id'],state='done');return
        route=db.loads(item['route'],{})
        for analysis in db.rows('SELECT id,result FROM benchmark_analyses WHERE benchmark_id=? AND body_hash=? ORDER BY id DESC',(source['id'],source['body_hash'])):
            meta=db.loads(analysis['result'],{}).get('metadata',{})
            if all(meta.get(k)==route.get(k) for k in ('model','backend','rule_hash')) and meta.get('depth')==item['depth']:
                update(item['id'],state='done',analysis_id=analysis['id']);return
        current=benchmarks.require(source['id'])
        if current['analysis_state'] in ('queued','running') and current['pending_job_id']:
            with db.tx() as c:enqueue(c,item,depends_on=current['pending_job_id'])
            return
        benchmarks.start_analysis(source['id'],{'depth':item['depth']},route=route,intake_item=item['id'])


def failed(payload,exc):
    # Never store raw HTTP responses / signed download URLs in public job errors.
    message=str(exc.detail) if isinstance(exc,HTTPException) else str(exc) if isinstance(exc,references.ReferenceError) else '此项处理未完成，已取得的原文和文件保留；可重试或补充正文'
    update(payload['item_id'],state='failed',error=message[:400])


@router.post('/api/intake-batches/{batch_id}/retry')
def retry(batch_id:int,body:dict=Body(default={})):
    batch=bundle(batch_id);iid=body.get('item_id')
    if iid is not None and (type(iid) is not int or not any(i['id']==iid for i in batch['items'])):raise HTTPException(400,'请选择本批次素材')
    with db.tx() as c:
        for item in batch['items']:
            if item['state'] in ('failed','paused') and (iid is None or iid==item['id']):
                if item['duplicate_of']:
                    owner=db.row('SELECT * FROM intake_items WHERE id=?',(item['duplicate_of'],))
                    if owner and effective(owner)['state'] in ('queued','acquiring','researching','done'):continue
                for field in ('media_id','benchmark_id','material_id'):
                    if item.get(field):c.execute('UPDATE intake_items SET '+field+'=? WHERE id=?',(item[field],item['id']))
                enqueue(c,item)
    jobs.wake();return bundle(batch_id)


@router.post('/api/intake-batches/{batch_id}/items/{iid}/supplement')
def supplement(batch_id:int,iid:int,body:dict=Body(...)):
    batch=bundle(batch_id);item=next((i for i in batch['items'] if i['id']==iid),None)
    if not item or item['state']!='failed':raise HTTPException(409,'只有失败项可以补正文；原始链接仍会保留')
    raw=media.text(body.get('text',''))
    mid=body.get('media_id')
    if bool(raw)==bool(mid):raise HTTPException(400,'请提供完整正文，或一个补充上传文件')
    if item['benchmark_id']:raise HTTPException(409,'原文已入库，请在对标库核对修订；研究失败只需重试研究')
    if mid:
        if type(mid) is not int:raise HTTPException(400,'补充文件标识无效')
        uploaded=media.require(mid)
        if not uploaded.get('extraction_only') or uploaded['state']!='staged':raise HTTPException(400,'请从批量入口上传补充文件')
        update(iid,media_id=mid,supplement_body='',duplicate_of=None)
    else:update(iid,supplement_body=raw,duplicate_of=None)
    return retry(batch_id,{'item_id':iid})


def cancelled(item):
    job=db.row('SELECT state FROM jobs WHERE id=?',(item['job_id'],))
    return not job or job['state']=='cancelled'


@router.post('/api/intake-batches/{batch_id}/items/{iid}/pause')
def pause(batch_id:int,iid:int):
    item=db.row('SELECT * FROM intake_items WHERE id=? AND batch_id=?',(iid,batch_id))
    if not item:raise HTTPException(404,'素材不在本批次')
    if item['duplicate_of']:raise HTTPException(409,'复用项请到原批次暂停，不影响其他批次')
    if effective(item)['state'] not in ('queued','acquiring','researching'):raise HTTPException(409,'当前素材已停止或完成')
    if item['analysis_job_id']:benchmarks.pause_analysis(item['benchmark_id'])
    else:jobs._set(item['job_id'],'cancelled')
    update(iid,state='paused',error='已暂停，原件和已完成步骤保留')
    if item['media_id']:
        source=media.require(item['media_id'])
        if source['state'] in ('queued','running'):media.stop(item['media_id'])
    return bundle(batch_id)

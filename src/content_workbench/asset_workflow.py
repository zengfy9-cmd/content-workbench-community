"""Links and frozen evidence for writing and publication reviews, not a new library."""
import hashlib
import math
import re
from fastapi import APIRouter, HTTPException
from . import db

router = APIRouter()


def setup():
    with db.tx() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS method_sources (
          tip_id INTEGER PRIMARY KEY REFERENCES content_tips(id),
          benchmark_id INTEGER REFERENCES benchmarks(id), analysis_id INTEGER REFERENCES benchmark_analyses(id),
          snapshot TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS publication_evidence (
          publication_id INTEGER PRIMARY KEY REFERENCES publications(id), snapshot TEXT NOT NULL,
          captured_at REAL NOT NULL, timing TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS publication_observations (
          id INTEGER PRIMARY KEY, publication_id INTEGER NOT NULL REFERENCES publications(id),
          observed_at REAL NOT NULL, metrics TEXT NOT NULL, note TEXT NOT NULL,
          next_test TEXT NOT NULL, created_at REAL NOT NULL);
        ''')


def text(body, key, limit=6000):
    value=body.get(key,'')
    if not isinstance(value,str) or len(value)>limit:raise HTTPException(400,'文字格式或长度无效')
    return value.strip()


def tokens(query):
    words=set(re.findall(r'[a-z0-9]{2,}|[\u3400-\u9fff]{2,}',query.lower()))
    for word in list(words):
        if re.fullmatch(r'[\u3400-\u9fff]+',word):words.update(word[i:i+2] for i in range(len(word)-1))
    groups=('转写 字幕 口播 语音','教程 实操 操作 流程 步骤','选题 主题 灵感','审核 核对 事实 证据','课程 教学 学习','发布 复盘 表现 数据')
    for group in groups:
        if any(w in query for w in group.split()):words.update(group.split())
    return words


@router.get('/api/assets/search')
def search(q: str=''):
    words=tokens(q[:1000]);found=[]
    if not words:return []
    for kind,table,column in [('materials','materials','body'),('benchmarks','benchmarks','body'),('tips','content_tips','content')]:
        for item in db.rows(f'SELECT * FROM {table}'):
            if item.get('archived') or (kind=='materials' and item.get('status')!='ready'):continue
            hay=(item['title']+' '+item[column]).lower()
            matches=sorted(w for w in words if w in hay)
            if not matches:continue
            score=sum(3 if w in item['title'].lower() else 1 for w in matches)
            found.append({'kind':kind,'id':item['id'],'title':item['title'],'preview':item[column][:180],
                          'reason':'内容相关：'+'、'.join(matches[:5]),'score':score})
    return sorted(found,key=lambda x:-x['score'])[:30]


@router.post('/api/benchmarks/{bid}/methods')
def capture_method(bid:int,body:dict):
    source=db.row('SELECT * FROM benchmarks WHERE id=?',(bid,))
    analysis=db.row('SELECT * FROM benchmark_analyses WHERE id=? AND benchmark_id=?',(body.get('analysis_id'),bid))
    if not source or not analysis:raise HTTPException(400,'请选择已有研究版本')
    if analysis['body_hash']!=source['body_hash']:raise HTTPException(409,'原文已变化，请在当前版本提取方法')
    content=text(body,'content');title=text(body,'title',200)
    if not title or not content:raise HTTPException(400,'请填写方法与适用范围')
    # This explicit save is a candidate, never a personal rule or validated experience.
    prior=db.row('SELECT t.id FROM content_tips t JOIN method_sources s ON s.tip_id=t.id WHERE s.analysis_id=? AND t.content=?',(analysis['id'],content))
    if prior:return prior
    with db.tx() as c:
        tid=c.execute("INSERT INTO content_tips(title,content,origin,validation,source_url,created_at,updated_at) VALUES(?,?,'AI建议','待验证',?,?,?)",(title,content,source['url'],db.now(),db.now())).lastrowid
        c.execute('INSERT INTO method_sources VALUES(?,?,?,?,?)',(tid,bid,analysis['id'],db.dumps({'title':source['title'],'body_hash':source['body_hash'],'analysis':db.loads(analysis['result'],{})}),db.now()))
    return {'id':tid}


def methods(ids):
    if not isinstance(ids,list) or any(type(i)!=int for i in ids):raise HTTPException(400,'请选择有效方法')
    result=[]
    for tid in dict.fromkeys(ids):
        tip=db.row('SELECT * FROM content_tips WHERE id=?',(tid,))
        if not tip or tip['archived']:raise HTTPException(400,'方法已归档或不存在')
        source=db.row('SELECT benchmark_id,analysis_id FROM method_sources WHERE tip_id=?',(tid,))
        result.append({**tip,'source_link':source,'adoption':'本篇试用，不代表验证有效'})
    return result


def capture_publication(c,pid,draft,timing='at-publication'):
    links=[dict(r) for r in c.execute('SELECT benchmark_id,analysis_id FROM draft_benchmarks WHERE draft_id=?',(draft['id'],)).fetchall()]
    snapshot={'draft_id':draft['id'],'idea_id':draft.get('idea_id'),'title':draft['title'],'body':draft['body'],
              'body_hash':hashlib.sha256(draft['body'].encode()).hexdigest(),
              'writing_context':db.loads(draft.get('writing_context'),{}),'research':links,
              'edit_round_ids':[r[0] for r in c.execute('SELECT id FROM edit_rounds WHERE draft_id=?',(draft['id'],)).fetchall()]}
    c.execute('INSERT OR IGNORE INTO publication_evidence VALUES(?,?,?,?)',(pid,db.dumps(snapshot),db.now(),timing))


@router.get('/api/publications/{pid}/review')
def publication_review(pid:int):
    pub=db.row('SELECT * FROM publications WHERE id=?',(pid,))
    if not pub:raise HTTPException(404,'发布记录不存在')
    evidence=db.row('SELECT * FROM publication_evidence WHERE publication_id=?',(pid,))
    rounds=db.rows('SELECT id,changes,updated_at FROM edit_rounds WHERE draft_id=? ORDER BY id DESC',(pub['draft_id'],))
    return {'publication':pub,'evidence':{**evidence,'snapshot':db.loads(evidence['snapshot'],{})} if evidence else None,
            'rounds':[{**r,'changes':db.loads(r['changes'],[])} for r in rounds],
            'observations':[{**r,'metrics':db.loads(r['metrics'],{})} for r in db.rows('SELECT * FROM publication_observations WHERE publication_id=? ORDER BY observed_at DESC,id DESC',(pid,))]}


@router.post('/api/publications/{pid}/observations')
def observation(pid:int,body:dict):
    pub=db.row('SELECT * FROM publications WHERE id=?',(pid,))
    if not pub:raise HTTPException(404,'发布记录不存在')
    if pub['state']!='manual_published' and not (pub['state']=='sent' and pub['platform']=='x'):
        raise HTTPException(400,'复制、导出、草稿箱记录不能当作实际发布效果')
    when=body.get('observed_at',db.now())
    if type(when) not in (int,float) or not math.isfinite(when) or when<=0 or when>db.now()+300:raise HTTPException(400,'请填写有效的采集时间')
    metrics=body.get('metrics',{})
    if not isinstance(metrics,dict) or set(metrics)-{'views','likes','comments','shares','saves','followers'}:raise HTTPException(400,'指标格式无效')
    if any(v is not None and (type(v)!=int or not 0<=v<=10**12) for v in metrics.values()):raise HTTPException(400,'指标只能填写非负整数，未知留空')
    note=text(body,'note');next_test=text(body,'next_test')
    with db.tx() as c:
        oid=c.execute('INSERT INTO publication_observations(publication_id,observed_at,metrics,note,next_test,created_at) VALUES(?,?,?,?,?,?)',(pid,when,db.dumps(metrics),note,next_test,db.now())).lastrowid
    return {'id':oid}


@router.post('/api/publications/{pid}/freeze-current')
def freeze_legacy(pid:int,body:dict):
    if body.get('confirm') is not True:raise HTTPException(400,'请确认这是补录时的稿件，不冒充发布时原稿')
    pub=db.row('SELECT * FROM publications WHERE id=?',(pid,))
    draft=db.row('SELECT * FROM drafts WHERE id=?',(pub['draft_id'],)) if pub else None
    if not draft:raise HTTPException(404,'原稿不存在')
    with db.tx() as c:capture_publication(c,pid,draft,'backfilled-current-not-original')
    return {'ok':True}


@router.post('/api/publications/{pid}/method-candidate')
def feedback_candidate(pid:int,body:dict):
    observation=db.row('SELECT * FROM publication_observations WHERE id=? AND publication_id=?',(body.get('observation_id'),pid))
    if not observation:raise HTTPException(400,'请选择真实复盘记录')
    content=text(body,'content');title=text(body,'title',200)
    if not content or not title:raise HTTPException(400,'请写出待验证方法及验证动作')
    for prior in db.rows('SELECT t.id,t.content,s.snapshot FROM content_tips t JOIN method_sources s ON s.tip_id=t.id WHERE s.benchmark_id IS NULL'):
        snapshot=db.loads(prior['snapshot'],{})
        if snapshot.get('publication_id')==pid and snapshot.get('observation',{}).get('id')==observation['id'] and prior['content']==content:return {'id':prior['id']}
    with db.tx() as c:
        tid=c.execute("INSERT INTO content_tips(title,content,origin,validation,feedback,created_at,updated_at) VALUES(?,?,'我的心得','待验证',?,?,?)",(title,content,'来自发布复盘 '+str(pid)+'；单次表现不证明因果。',db.now(),db.now())).lastrowid
        c.execute('INSERT INTO method_sources VALUES(?,NULL,NULL,?,?)',(tid,db.dumps({'publication_id':pid,'observation':observation}),db.now()))
    return {'id':tid}

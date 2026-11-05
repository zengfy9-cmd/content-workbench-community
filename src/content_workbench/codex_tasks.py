"""Durable workbench-owned tasks. Restart recovery never creates a new inference turn."""
import threading
import uuid
from fastapi import HTTPException, Body
from . import db, codex_native

MODEL = 'gpt-6.1-sol'


def get(tid):
    row=db.row('SELECT * FROM codex_tasks WHERE id=?',(tid,))
    if not row: raise HTTPException(404,'找不到任务')
    row['usage']=db.loads(row['usage'],None)
    return row


def finish(tid, state, **fields):
    allowed={'result','error','thread_id','turn_id','usage','partial'}
    values={k:db.dumps(v) if k=='usage' else v for k,v in fields.items() if k in allowed}
    with db.tx() as conn:
        conn.execute('UPDATE codex_tasks SET state=?,updated_at=?'+''.join(f',{k}=?' for k in values)+' WHERE id=?',
            (state,db.now(),*values.values(),tid))


def execute(tid):
    try:
        row=get(tid);buffer='';usage=None
        def cancelled(): return get(tid)['state']=='cancelled'
        def event(value):
            nonlocal buffer,usage
            if 'delta' in value: buffer+=value['delta']
            if 'usage' in value: usage=value['usage']
            # Keep partial text and session IDs, including across a server interruption.
            with db.tx() as conn:
                conn.execute('UPDATE codex_tasks SET partial=?,thread_id=COALESCE(?,thread_id),turn_id=COALESCE(?,turn_id),usage=?,updated_at=? WHERE id=?',
                    (buffer,value.get('thread_id'),value.get('turn_id'),db.dumps(usage),db.now(),tid))
        text=codex_native.run(row['prompt'],model=row['model'],effort=row['effort'],cancelled=cancelled,on_event=event)
        if not cancelled(): finish(tid,'completed',result=text,error='',usage=usage)
    except Exception as exc:
        if get(tid)['state']!='cancelled':
            finish(tid,'failed',error=str(exc) if isinstance(exc,codex_native.CodexError) else 'Codex 接入发生本地错误；保留已有记录，未自动重试')


def launch(tid):
    threading.Thread(target=execute,args=(tid,),daemon=True,name=f'cw-codex-{tid}').start()


def register(app):
    with db.tx() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS codex_tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_key TEXT UNIQUE NOT NULL, model TEXT NOT NULL,
            title TEXT NOT NULL, prompt TEXT NOT NULL, project TEXT NOT NULL, platform TEXT NOT NULL,
            state TEXT NOT NULL, result TEXT NOT NULL DEFAULT '',partial TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '', thread_id TEXT,turn_id TEXT, usage TEXT, draft_id INTEGER,
            created_at REAL NOT NULL,updated_at REAL NOT NULL)''')
        if 'effort' not in {r['name'] for r in conn.execute('PRAGMA table_info(codex_tasks)')}:
            conn.execute("ALTER TABLE codex_tasks ADD COLUMN effort TEXT NOT NULL DEFAULT 'high'")
        conn.execute("UPDATE codex_tasks SET state='interrupted',error='服务已重启，请先读取原任务结果；没有重新生成' WHERE state='running'")

    @app.get('/api/codex/status')
    def status(): return codex_native.status()

    @app.get('/api/codex/tasks')
    def tasks():
        return db.rows('SELECT id,title,project,platform,model,effort,state,created_at,updated_at,draft_id FROM codex_tasks ORDER BY id DESC LIMIT 100')

    @app.get('/api/codex/tasks/{tid}')
    def task(tid:int): return get(tid)

    @app.post('/api/codex/tasks')
    def create(body:dict=Body(...)):
        from .config import Settings
        if Settings.load().llm_backend=='disabled':
            raise HTTPException(409,'Codex 文字任务已停用，请使用正文配图入口')
        from .platforms import PLATFORMS
        for name,limit in [('title',200),('prompt',100000),('project',100)]:
            if not isinstance(body.get(name),str) or not body[name].strip() or len(body[name])>limit:
                raise HTTPException(400,'请填写有效的'+name)
        if body.get('platform') not in PLATFORMS: raise HTTPException(400,'请选择目标平台')
        key=body.get('request_key')
        try: uuid.UUID(key)
        except (ValueError,TypeError,AttributeError): raise HTTPException(400,'请求编号无效，请刷新页面') from None
        with db.tx() as conn:
            existing=conn.execute('SELECT * FROM codex_tasks WHERE request_key=?',(key,)).fetchone()
            if existing:
                if any(existing[k]!=body[k] for k in ['title','prompt','project','platform']): raise HTTPException(409,'同一请求编号不能改变输入')
                return {'id':existing['id'],'reused':True}
            if conn.execute("SELECT 1 FROM codex_tasks WHERE state='running'").fetchone(): raise HTTPException(409,'已有一个 Codex 任务执行中，请等待或先停止')
            stamp=db.now()
            tid=conn.execute('INSERT INTO codex_tasks(request_key,model,title,prompt,project,platform,state,created_at,updated_at,effort) VALUES(?,?,?,?,?,?,\'running\',?,?,\'xhigh\')',
                (key,MODEL,body['title'],body['prompt'],body['project'],body['platform'],stamp,stamp)).lastrowid
        launch(tid)
        return {'id':tid,'reused':False}

    @app.post('/api/codex/tasks/{tid}/cancel')
    def cancel(tid:int):
        get(tid)
        with db.tx() as conn:
            conn.execute("UPDATE codex_tasks SET state='cancelled',updated_at=? WHERE id=? AND state='running'",(db.now(),tid))
        return get(tid)

    @app.post('/api/codex/tasks/{tid}/recover')
    def recover(tid:int):
        row=get(tid)
        if row['state']=='running': raise HTTPException(409,'任务正在执行，请先等待或停止')
        if not row['thread_id']: raise HTTPException(409,'尚未取得原会话编号，无法读取远端结果')
        client=None
        try:
            client=codex_native.Client();client.account()
            thread=client.request('thread/read',{'threadId':row['thread_id'],'includeTurns':True})['thread']
            turn=next((t for t in thread.get('turns',[]) if t['id']==row['turn_id']),None)
            if not turn: raise HTTPException(409,'没有找到原轮次完成记录；未发起新生成')
            if turn['status']!='completed':
                message=codex_native.safe_error(turn.get('error') or {})
                finish(tid,'failed',error=message);return get(tid)
            result='\n\n'.join(i.get('text','') for i in turn.get('items',[]) if i.get('type')=='agentMessage').strip()
            if not result: raise HTTPException(409,'原轮次没有文字结果；未发起新生成')
            finish(tid,'completed',result=result,error='');return get(tid)
        except codex_native.CodexError as exc: raise HTTPException(409,str(exc)) from None
        finally:
            if client: client.close()

    @app.post('/api/codex/tasks/{tid}/adopt')
    def adopt(tid:int):
        with db.tx() as conn:
            row=conn.execute('SELECT * FROM codex_tasks WHERE id=?',(tid,)).fetchone()
            if not row or row['state']!='completed' or not row['result']: raise HTTPException(409,'任务尚未成功完成')
            if row['draft_id']:
                if not conn.execute('SELECT 1 FROM drafts WHERE id=?',(row['draft_id'],)).fetchone(): raise HTTPException(409,'已采用的稿件已删除，请手动核对原结果')
                return {'id':row['draft_id']}
            stamp=db.now()
            conn.execute('INSERT OR IGNORE INTO content_groups(name,created_at) VALUES(?,?)',(row['project'],stamp))
            group=conn.execute('SELECT id FROM content_groups WHERE name=?',(row['project'],)).fetchone()['id']
            did=conn.execute('INSERT INTO drafts(platform,title,body,notes,group_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                (row['platform'],row['title'],row['result'],f"Codex 订阅任务 #{tid} · {row['model']} · 待人工审稿",group,stamp,stamp)).lastrowid
            conn.execute("INSERT INTO draft_versions(draft_id,title,body,created_at,origin) VALUES(?,?,?,?,'codex-task')",(did,row['title'],row['result'],stamp))
            conn.execute('UPDATE codex_tasks SET draft_id=? WHERE id=?',(did,tid))
        return {'id':did}

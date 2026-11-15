"""Owner-editable working rules and immutable prompts actually sent to models."""
import hashlib
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from . import db

router = APIRouter()
DEFAULTS = {
    'writing': '# 写作工作准则\n\n以本篇选题、已选素材和已确认观点为依据。保留来源与经历归属，不编造事实、数据、实测结果或个人判断。缺少的材料留在备注中询问。遵守平台要求；可复制的 Agent 指令用 prompt 围栏块。',
    'images': '# 配图工作准则\n\n沿用已选的自选角色角色身份，按题材调整场景、动作与画风。每张表达一个核心意思，文字少而清晰。真实截图、数据和证据不得生成伪造。参考正文只用于理解，不改写原稿。正文配图先编号预览，再确认独立大图；入稿先预览后保存。',
}

def setup():
    with db.tx() as c:
        c.execute('CREATE TABLE IF NOT EXISTS ai_rule_versions(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,content TEXT NOT NULL,created_at REAL NOT NULL)')
        c.execute('''CREATE TABLE IF NOT EXISTS ai_prompt_runs(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,target_id INTEGER NOT NULL,
            phase TEXT NOT NULL,model TEXT NOT NULL,prompt TEXT NOT NULL,sha256 TEXT NOT NULL,created_at REAL NOT NULL)''')

def rule(kind):
    if kind not in DEFAULTS: raise HTTPException(404)
    row=db.row('SELECT * FROM ai_rule_versions WHERE kind=? ORDER BY id DESC LIMIT 1',(kind,))
    return row or {'id':0,'kind':kind,'content':DEFAULTS[kind],'created_at':None}

def apply(prompt, content):
    return prompt+'\n\n<owner_working_rules>\n'+content+'\n</owner_working_rules>'

def record(kind, target_id, phase, model, prompt):
    with db.tx() as c:
        c.execute('INSERT INTO ai_prompt_runs(kind,target_id,phase,model,prompt,sha256,created_at) VALUES(?,?,?,?,?,?,?)',
                  (kind,target_id,phase,model,prompt,hashlib.sha256(prompt.encode()).hexdigest(),db.now()))

@router.get('/api/ai-rules')
def rules():
    from . import prompts, article_images, idea_cards
    from .config import Settings
    writing=rule('writing'); image=rule('images')
    return {'writing':writing,'images':image,'writing_context':idea_cards.style_context(Settings.load()),
            'writing_builtin':prompts.DRAFT_WECHAT,
            'image_builtin':article_images.STYLE,
            'flow':['选题与本篇说明','已选原文、来源与个人材料','长期准则和平台要求','模型收到的完整提示词','生成结果与人工确认']}

@router.put('/api/ai-rules/{kind}')
def save_rule(kind:str, body:dict):
    current=rule(kind)
    text=body.get('content')
    if not isinstance(text,str) or not text.strip() or len(text)>20000: raise HTTPException(400,'准则请填写1至20000字')
    with db.tx() as c:
        latest=c.execute('SELECT id FROM ai_rule_versions WHERE kind=? ORDER BY id DESC LIMIT 1',(kind,)).fetchone()
        if body.get('expected_id') != (latest['id'] if latest else 0):raise HTTPException(409,'准则已被其他窗口修改，请重新载入')
        c.execute('INSERT INTO ai_rule_versions(kind,content,created_at) VALUES(?,?,?)',(kind,text.strip(),db.now()))
    return rule(kind)

@router.get('/api/ai-rules/{kind}/document')
def document(kind:str):
    r=rule(kind)
    return PlainTextResponse(r['content'],headers={'Content-Disposition':f'attachment; filename="{kind}-working-rules.md"'})

@router.get('/api/ai-prompts/{kind}/{target_id}')
def trace(kind:str,target_id:int):
    if kind not in ('writing','images'):raise HTTPException(404)
    return db.rows('SELECT * FROM ai_prompt_runs WHERE kind=? AND target_id=? ORDER BY id',(kind,target_id))

@router.post('/api/ideas/{iid}/prompt-preview')
def writing_preview(iid:int, body:dict):
    from . import idea_cards, writing_sources
    from .config import Settings
    platform=body.get('platform','wechat')
    if platform not in ('wechat','x','xiaohongshu','douyin'):raise HTTPException(400,'平台无效')
    context=idea_cards.snapshot(iid,body,Settings.load())
    if isinstance(body.get('title'),str) and body['title'].strip():
        context['title']=body['title'].strip()[:300]
        context.pop('auto_sources',None)
        writing_sources.attach(context)
    prompt=idea_cards.context_prompt(context,platform)
    return {'prompt':prompt,'context':context,'notice':'预览不调用模型；起草时会保存实际发送的版本。'}

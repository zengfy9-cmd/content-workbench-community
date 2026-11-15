"""Bind generated assets to local drafts, propose placement, save after review only."""
from __future__ import annotations

import hashlib
import re
import threading
import uuid

from fastapi import HTTPException
from . import db, images
from .textutil import markdown_parser

LOCK = threading.RLock()
MARKER = re.compile(r'[（(][\s\u200b]*配图(?:\s*[一二三四五六七八九十\d]+)?(?:\s*[:：][^）)\n]{0,160})?[\s\u200b]*[）)]')

_PUNCT = str.maketrans('，。：；！？（）【】“”‘’、', ',.:;!?()[]""\'\',')


def _norm(text: str) -> str:
    """排版定位用：只留文字和数字，去掉 Markdown 符号、空格、标点和全半角差异。"""
    return re.sub(r'[^\w\u4e00-\u9fff]+', '', text or '')


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def markers(body):
    lines = body.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    blocked = set()
    for token in markdown_parser().parse(body):
        if token.type in ('fence','code_block') and token.map:
            blocked.update(range(*token.map))
    result, section = [], ''
    for index, line in enumerate(lines):
        if index in blocked:
            continue
        heading = re.match(r'^\s{0,3}#{1,6}\s+(.+)', line)
        if heading:
            section = heading.group(1).strip()
        inline_code = [m.span() for m in re.finditer(r'(`+).*?\1', line)]
        for match in MARKER.finditer(line):
            if any(a <= match.start() < b for a,b in inline_code):
                continue
            before = next((v.strip() for v in reversed(lines[max(0,index-5):index]) if v.strip() and not MARKER.fullmatch(v.strip()) and not v.lstrip().startswith('![')), '')
            after = next((v.strip() for v in lines[index+1:index+6] if v.strip() and not MARKER.fullmatch(v.strip()) and not v.lstrip().startswith('![')), '')
            context = ' / '.join(v for v in [section,before[-100:],after[:100]] if v)
            result.append({'slot':len(result)+1,'start':offsets[index]+match.start(),'end':offsets[index]+match.end(),
                           'marker':match.group(),'section':section,'context':context[:260] or '配图位置',
                           'signature':digest(context)})
    return result


def setup(conn):
    have = {row['name'] for row in conn.execute('PRAGMA table_info(article_image_tasks)')}
    for name, declaration in [('draft_id','INTEGER'),('source_hash',"TEXT NOT NULL DEFAULT ''"),('placement_plan',"TEXT NOT NULL DEFAULT '[]'")]:
        if name not in have:
            conn.execute(f'ALTER TABLE article_image_tasks ADD COLUMN {name} {declaration}')
    conn.execute('CREATE TABLE IF NOT EXISTS article_image_attachments(task_id INTEGER,draft_id INTEGER,image_id INTEGER,PRIMARY KEY(task_id,draft_id))')
    conn.execute('''CREATE TABLE IF NOT EXISTS article_image_layout_saves(
        request_key TEXT PRIMARY KEY,draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
        signature TEXT NOT NULL,result TEXT NOT NULL,created_at REAL NOT NULL)''')


def binding(did, article, *, cover=False):
    if did is None:
        return {'draft_id':None,'source_hash':digest(article),'placement_plan':db.dumps(markers(article))}
    if type(did) is not int:
        raise HTTPException(400,'请选择工作台里的原稿')
    draft = db.row('SELECT * FROM drafts WHERE id=?',(did,))
    if not draft:
        raise HTTPException(404,'原稿不存在')
    if not cover and article != draft['body']:
        raise HTTPException(409,'配图正文与已保存原稿不同，请先保存原稿并重新载入；也可取消绑定，仅生成图片')
    return {'draft_id':did,'source_hash':digest(draft['body']), 'placement_plan':db.dumps(markers(draft['body']))}


def suggested_draft(article):
    if not article.strip():
        return None
    found = db.rows('SELECT id FROM drafts WHERE body=? LIMIT 2',(article,))
    return found[0]['id'] if len(found)==1 else None


def attach(tid, did):
    from . import article_images
    task = article_images.get(tid)
    if task['kind'] not in ('final','cover') or task['state']!='completed' or not task['artifacts']:
        raise HTTPException(409,'请先完成独立大图或封面')
    if type(did) is not int or not db.row('SELECT 1 FROM drafts WHERE id=?',(did,)):
        raise HTTPException(400,'请选择已有工作台稿件')
    if task.get('draft_id') and task['draft_id']!=did:
        raise HTTPException(409,'此图片已绑定另一篇原稿，请使用对应稿件')
    with LOCK:
        old = db.row('SELECT image_id FROM article_image_attachments WHERE task_id=? AND draft_id=?',(tid,did))
        if old:
            if not images.get(old['image_id']):
                raise HTTPException(409,'这张附件已被删除，请核对后再操作')
            return old['image_id']
        name = f'封面-{tid}' if task['kind']=='cover' else f'配图{task["panel"]}-{tid}'
        image = images.save(did,name,article_images.generated_path(task['artifacts'][0]).read_bytes())
        with db.tx() as conn:
            conn.execute('INSERT INTO article_image_attachments VALUES(?,?,?)',(tid,did,image['id']))
        return image['id']


def attach_bound(tid):
    from . import article_images
    task = article_images.get(tid)
    if task.get('draft_id') and task['kind'] in ('final','cover'):
        try:
            attach(tid,task['draft_id'])
        except (HTTPException,images.ImageError,OSError):
            # Generation is still successful; the existing asset can be retried without regeneration.
            return


def markdown(image):
    name = re.sub(r'[\[\]\\\n\r]', ' ', image['name'])
    return f'![{name}](/api/images/{image["id"]})'


def actual_images(body):
    found = []
    for token in markdown_parser().parse(body):
        for child in token.children or []:
            if child.type=='image':
                src = child.attrGet('src') or ''
                if re.fullmatch(r'/api/images/\d+',src):
                    found.append(int(src.rsplit('/',1)[-1]))
    return found


def _remove_image(body, iid):
    if iid not in actual_images(body):
        return body
    pattern = re.compile(r'!\[(?:\\.|[^\]])*\]\(/api/images/'+str(iid)+r'(?:\s+"[^"]*")?\)')
    matches = list(pattern.finditer(body))
    if len(matches)!=1:
        raise HTTPException(409,'同一图片有多个位置，请直接在编辑区拖动或删除多余图片')
    return body[:matches[0].start()]+body[matches[0].end():]


def propose(tid, did=None, *, current_only=False):
    from . import article_images
    task = article_images.get(tid)
    if task['kind'] not in ('final','cover') or task['state']!='completed':
        raise HTTPException(409,'请先确认并生成独立大图，再预览入稿排版')
    bound = task.get('draft_id')
    if bound and did is not None and bound!=did:
        raise HTTPException(409,'此任务已绑定另一篇原稿，请使用对应稿件')
    did = bound or did or suggested_draft(task['article'])
    draft = db.row('SELECT * FROM drafts WHERE id=?',(did,)) if type(did) is int else None
    if not draft:
        raise HTTPException(400,'请先选择要存回的工作台稿件')
    group = task['parent_id'] if task['kind']=='final' else None
    if group is None and task['kind']=='cover':
        latest = db.row("SELECT id FROM article_image_tasks WHERE kind='preview' AND draft_id=? ORDER BY id DESC LIMIT 1",(did,))
        group = latest['id'] if latest else None
    siblings = db.rows("SELECT id FROM article_image_tasks WHERE kind='final' AND parent_id=? AND state='completed' ORDER BY panel,id",(group,)) if group else []
    parent = article_images.get(group) if group else None
    source = parent['article'] if parent else draft['body']
    # Only task-time placement plans establish the meaning of a panel number.
    # Legacy previews did not promise that panel N illustrates marker N.
    original_slots = parent['placement_plan'] if parent else []
    assets = []
    for sibling in siblings:
        row = article_images.get(sibling['id'])
        if row.get('draft_id') not in (None,did):
            continue
        iid = attach(row['id'],did)
        assets.append({'task_id':row['id'],'image_id':iid,'panel':row['panel'],'name':images.get(iid)['name']})
    body = draft['body']; present = set(actual_images(body))
    normalized = body
    # A confirmed cover can have been inserted since the numbered preview.
    # Ignore only these additions when comparing to the original source.
    source_images=set(actual_images(source))
    covers=db.rows("SELECT a.image_id FROM article_image_attachments a JOIN article_image_tasks t ON t.id=a.task_id WHERE a.draft_id=? AND t.kind='cover'",(did,))
    for cover_asset in covers:
        iid=cover_asset['image_id']
        if iid not in source_images:
            pattern=re.compile(r'!\[(?:\\.|[^\]])*\]\(/api/images/'+str(iid)+r'(?:\s+"[^"]*")?\)')
            normalized=pattern.sub('',normalized)
    for asset in assets:
        slot = (asset['panel'] or 0)-1
        if asset['image_id'] in present and 0<=slot<len(original_slots):
            reference = re.compile(r'!\[(?:\\.|[^\]])*\]\(/api/images/'+str(asset['image_id'])+r'(?:\s+"[^"]*")?\)')
            normalized = reference.sub(lambda _:original_slots[slot]['marker'],normalized,count=1)
    unchanged = re.sub(r'\s+','',normalized)==re.sub(r'\s+','',source)
    current_slots = markers(body)
    occupied = {asset['panel'] for asset in assets if asset['image_id'] in present}
    free_slots = [slot for slot in original_slots if slot['slot'] not in occupied]
    mapping = {old['slot']:new for old,new in zip(free_slots,current_slots)} if unchanged and len(free_slots)==len(current_slots) else {}
    if not mapping:
        for old in free_slots:
            same = [new for new in current_slots if new['signature']==old['signature']]
            if len(same)==1 and sum(s['signature']==old['signature'] for s in free_slots)==1:
                mapping[old['slot']]=same[0]
    replacements = []; warnings = []
    placement_assets=[a for a in assets if not current_only or a['task_id']==tid]
    for asset in placement_assets:
        if asset['image_id'] in present:
            continue
        slot = mapping.get(asset['panel'])
        if slot:
            replacements.append((slot['start'],slot['end'],'\n\n'+markdown(images.get(asset['image_id']))+'\n\n'))
        else:
            warnings.append(f'第 {asset["panel"]} 张已存为附件，未找到明确的（配图）位置；可拖入正文或输入位置。')
    for start,end,value in sorted(replacements,reverse=True):
        body = body[:start]+value+body[end:]
    cover = None
    if task['kind']=='cover':
        iid = attach(tid,did)
        cover = {'image_id':iid,'field':'cover_x' if draft['platform']=='x' else 'cover_wechat'}
        if iid not in actual_images(body):
            body=markdown(images.get(iid))+'\n\n'+body
    return {'draft_id':did,'title':draft['title'],'platform':draft['platform'],'wechat_theme':draft.get('wechat_theme',''),
            'body':body,'expected_updated_at':draft['updated_at'],'assets':placement_assets,'cover':cover,
            'current_image_id':cover['image_id'] if cover else next((a['image_id'] for a in assets if a['task_id']==tid),None),
            'warnings':warnings,'auto_placed':len(replacements)}


def move(did, body, iid, target):
    image = images.get(iid)
    if not image or image['draft_id']!=did:
        raise HTTPException(400,'图片不属于这篇稿件')
    body = _remove_image(body,iid)
    marker = re.fullmatch(r'(?:第\s*)?(\d+)\s*(?:处|个)?\s*配图(?:位置)?',target.strip())
    if marker:
        slots = markers(body); index = int(marker.group(1))-1
        if not 0<=index<len(slots):
            raise HTTPException(400,'没有这个配图标记，请改用“第3段后”或输入正文中的一句话')
        slot=slots[index]
        return body[:slot['start']]+'\n\n'+markdown(image)+'\n\n'+body[slot['end']:]
    tokens = markdown_parser().parse(body); lines=body.splitlines(keepends=True)
    paragraphs=[]
    for index,token in enumerate(tokens):
        if token.type=='paragraph_open' and token.level==0 and token.map:
            inline=tokens[index+1]
            if inline.type=='inline' and not MARKER.fullmatch(inline.content.strip(' \t\r\n\u200b')) and any(c.type in ('text','code_inline') and c.content.strip() for c in inline.children or []):
                paragraphs.append((token.map[1],inline.content))
    ordinal=re.fullmatch(r'(?:第\s*)?(\d+)\s*段(?:后|之后|后面)?',target.strip())
    if ordinal:
        index=int(ordinal.group(1))-1
        matches=paragraphs[index:index+1] if index>=0 else []
    else:
        quote=target.strip().strip('“”「」"')
        q=_norm(quote)
        matches=[p for p in paragraphs if q and q in _norm(p[1])]
        if not matches and quote:
            # 退一步：按引用里最长的连续片段再找一次，避免一个字打错就全废
            chunk=max(re.findall(r'[^\s，。：；！？、]{6,}', quote) or [''], key=len)
            if chunk:
                c=_norm(chunk)
                matches=[p for p in paragraphs if c and c in _norm(p[1])]
    if len(matches)!=1:
        if len(matches)>1:
            raise HTTPException(400,f'这句话在正文里出现了 {len(matches)} 次，请多带几个字，或改用“第N段后”')
        hint=''
        if paragraphs and quote:
            q=_norm(quote)
            best=max(paragraphs,key=lambda p:sum(1 for ch in set(q) if ch in _norm(p[1])))
            hint='。最接近的一段是：「'+best[1][:20].strip()+'…」'
        raise HTTPException(400,'无法唯一定位，请输入“第3段后”或正文中更完整的一句话'+hint)
    position=sum(len(line) for line in lines[:matches[0][0]])
    return body[:position]+'\n\n'+markdown(image)+'\n\n'+body[position:]


def confirm_image(tid, did, request_key):
    """Apply one explicitly confirmed image to the latest saved article."""
    try:uuid.UUID(request_key)
    except (ValueError,TypeError,AttributeError):raise HTTPException(400,'确认编号无效，请刷新后重试') from None
    with LOCK:
        old=db.row('SELECT * FROM article_image_layout_saves WHERE request_key=?',(request_key,))
        if old:
            result=db.loads(old['result'],{})
            if result.get('task_id')!=tid or (did is not None and old['draft_id']!=did):raise HTTPException(409,'同一确认编号不能更换图片或稿件')
            return result
        plan=propose(tid,did,current_only=True)
        selected=plan['cover']['image_id'] if plan['cover'] else next((a['image_id'] for a in plan['assets'] if a['task_id']==tid),None)
        if not selected or selected not in actual_images(plan['body']):
            return {'ok':False,'requires_position':True,'draft_id':plan['draft_id'],'warnings':plan['warnings'],
                    'detail':'图片已保留，请在预览里选择插入位置后确认保存。'}
        application={'task_id':tid,'image_id':selected,'requires_position':False}
        return save(plan['draft_id'],{'body':plan['body'],'cover':plan['cover'],'expected_updated_at':plan['expected_updated_at'],'request_key':request_key},application=application)


def save(did, values, *, application=None):
    key=values.get('request_key')
    try:uuid.UUID(key)
    except (ValueError,TypeError,AttributeError):raise HTTPException(400,'保存编号无效，请重新打开预览') from None
    body=values.get('body')
    if not isinstance(body,str) or len(body)>120000:
        raise HTTPException(400,'排版正文格式无效或过长')
    signing={'draft_id':did,'body':body,'cover':values.get('cover')}
    if application:signing['application']=application
    if 'required_image_id' in values:signing['required_image_id']=values['required_image_id']
    signature=digest(db.dumps(signing))
    with db.tx() as conn:
        old=conn.execute('SELECT * FROM article_image_layout_saves WHERE request_key=?',(key,)).fetchone()
        if old:
            if old['signature']!=signature:raise HTTPException(409,'这次保存编号已用于不同排版')
            return db.loads(old['result'],{})
        draft=conn.execute('SELECT * FROM drafts WHERE id=?',(did,)).fetchone()
        if not draft:raise HTTPException(404,'原稿不存在')
        if draft['updated_at']!=values.get('expected_updated_at') or draft['status'] in ('generating','revising'):
            raise HTTPException(409,'原稿已有新修改，请重新载入排版预览；当前预览内容未覆盖原稿')
        cover=values.get('cover'); ids=set(actual_images(body))
        required=values.get('required_image_id')
        if required is not None and (type(required) is not int or required not in ids):
            raise HTTPException(400,'本次图片还没有放入正文，请拖入图片或选择插入位置后再确认')
        if application:
            task=conn.execute('SELECT draft_id FROM article_image_tasks WHERE id=?',(application['task_id'],)).fetchone()
            if not task or task['draft_id'] not in (None,did):raise HTTPException(409,'图片对应的原稿已变化，请重新载入')
        if cover:
            if not isinstance(cover,dict) or cover.get('field') not in ('cover_wechat','cover_x') or type(cover.get('image_id')) is not int:
                raise HTTPException(400,'封面选项无效')
            ids.add(cover['image_id'])
        if any(not conn.execute('SELECT 1 FROM images WHERE id=? AND draft_id=?',(iid,did)).fetchone() for iid in ids):
            raise HTTPException(400,'正文或封面含有不属于本稿的图片')
        stamp=db.now()
        if body!=draft['body']:
            conn.execute("INSERT INTO draft_versions(draft_id,title,body,created_at,origin) VALUES(?,?,?,?,'before-image-layout')",(did,draft['title'],draft['body'],stamp))
            conn.execute("UPDATE drafts SET body=?,updated_at=? WHERE id=?",(body,stamp,did))
            conn.execute("INSERT INTO draft_versions(draft_id,title,body,created_at,origin) VALUES(?,?,?,?,'image-layout')",(did,draft['title'],body,stamp))
        else:
            conn.execute('UPDATE drafts SET updated_at=? WHERE id=?',(stamp,did))
        if cover:
            conn.execute(f"UPDATE drafts SET {cover['field']}=? WHERE id=?",(cover['image_id'],did))
        if application:
            conn.execute('UPDATE article_image_tasks SET draft_id=? WHERE id=? AND draft_id IS NULL',(did,application['task_id']))
        result={'ok':True,'draft_id':did,'updated_at':stamp,'href':f'/#/drafts/{did}'}
        if application:result.update(application)
        conn.execute('INSERT INTO article_image_layout_saves VALUES(?,?,?,?,?)',(key,did,signature,db.dumps(result),stamp))
    return result

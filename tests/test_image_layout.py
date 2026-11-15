import uuid

from content_workbench import article_images, db, image_layout
from test_article_images import setup, png


def draft(c,h,body,platform='wechat'):
    result=c.post('/api/drafts',json={'title':'排版测试','platform':platform,'body':body},headers=h)
    assert result.status_code==200,result.text
    return result.json()['id']


def preview(c,h,did,body):
    result=c.post('/api/article-images',json={'request_key':str(uuid.uuid4()),'title':'排版测试','article':body,'draft_id':did,'count':3},headers=h)
    assert result.status_code==200,result.text
    return result.json()['id']


def final(c,h,tid,panel=1):
    result=c.post(f'/api/article-images/{tid}/final',json={'request_key':str(uuid.uuid4()),'confirm':True,'panel':panel},headers=h)
    assert result.status_code==200,result.text
    return result.json()['id']


def proposal(c,h,tid,did):
    result=c.post('/api/article-images/layout-preview',json={'task_id':tid,'draft_id':did},headers=h)
    assert result.status_code==200,result.text
    return result.json()


def save_payload(plan,**extra):
    return {'draft_id':plan['draft_id'],'body':plan['body'],'expected_updated_at':plan['expected_updated_at'],
            'cover':plan['cover'],'confirm':True,'request_key':str(uuid.uuid4()),**extra}


def test_marker_context_ignores_instruction_examples_and_inline_code():
    body='# 引言\n\n第一段。\n\n（配图）\u200b\n\n第二段。\n\n```prompt\n（配图）\n```\n\n`(配图)` 是示例。\n\n## 结尾\n\n结尾段。\n\n(配图2: 总结)\n'
    slots=image_layout.markers(body)
    assert len(slots)==2
    assert slots[0]['context'].startswith('引言 / 第一段。')
    assert slots[1]['section']=='结尾' and slots[1]['slot']==2
    assert all(body[s['start']:s['end']]==s['marker'] for s in slots)


def test_bound_generation_attaches_but_saves_only_after_review(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    body='第一段讲输入。\n\n（配图）\n\n第二段讲判断。\n\n（配图）\n\n最后一段。'
    did=draft(c,h,body);tid=preview(c,h,did,body)
    row=c.get(f'/api/article-images/{tid}').json()
    assert row['draft_id']==did and len(row['placement_plan'])==2
    assert '前面的编号必须依次围绕' in row['artifacts'][0]['prompt'] and '第一段讲输入' in row['artifacts'][0]['prompt']
    fid=final(c,h,tid,2)
    image_id=c.get(f'/api/article-images/{fid}').json()['attached_image_id']
    original=c.get(f'/api/drafts/{did}').json()
    assert original['body']==body and len(original['images'])==1 and image_id
    plan=proposal(c,h,fid,did)
    assert plan['auto_placed']==1 and plan['warnings']==[]
    assert plan['body'].index(f'/api/images/{image_id}')>plan['body'].index('第二段讲判断')
    assert c.get(f'/api/drafts/{did}').json()['body']==body
    payload=save_payload(plan)
    assert c.post('/api/article-images/layout-save',json={**payload,'confirm':False},headers=h).status_code==400
    result=c.post('/api/article-images/layout-save',json=payload,headers=h)
    assert result.status_code==200,result.text
    assert c.post('/api/article-images/layout-save',json=payload,headers=h).json()==result.json()
    saved=c.get(f'/api/drafts/{did}').json()
    assert saved['body']==plan['body'] and saved['cover_wechat'] is None
    versions=db.rows("SELECT body,origin FROM draft_versions WHERE draft_id=? AND origin LIKE '%image-layout' ORDER BY id",(did,))
    assert [v['origin'] for v in versions]==['before-image-layout','image-layout'] and versions[0]['body']==body
    # Generate another numbered image after saving panel 2. The remaining marker
    # still represents panel 1, rather than shifting all panel assignments.
    first=final(c,h,tid,1);combined=proposal(c,h,first,did)
    assert combined['auto_placed']==1 and len(image_layout.actual_images(combined['body']))==2
    assert len(set(image_layout.actual_images(combined['body'])))==2
    assert c.post('/api/article-images/layout-save',json=save_payload(combined),headers=h).status_code==200
    assert proposal(c,h,first,did)['auto_placed']==0
    monkeypatch.setattr(article_images,'launch',lambda *_:(_ for _ in ()).throw(AssertionError('must reuse original image')))
    assert final(c,h,tid,1)==first
    assert db.row('SELECT COUNT(*) n FROM publications')['n']==0


def test_missing_markers_keep_attachment_and_typed_move_is_staged(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    body='第一段讲素材。\n\n第二段讲判断。\n\n第三段讲复盘。'
    did=draft(c,h,body);fid=final(c,h,preview(c,h,did,body))
    plan=proposal(c,h,fid,did);iid=plan['assets'][0]['image_id']
    assert plan['body']==body and plan['auto_placed']==0 and '未找到明确' in plan['warnings'][0]
    def move(text,target):
        result=c.post('/api/article-images/layout-move',json={'draft_id':did,'body':text,'image_id':iid,'target':target},headers=h)
        assert result.status_code==200,result.text
        return result.json()['body']
    staged=move(body,'第2段后')
    assert staged.index('/api/images/')>staged.index('第二段讲判断') and staged.index('/api/images/')<staged.index('第三段讲复盘')
    staged=move(staged,'第三段讲复盘')
    assert staged.index('/api/images/')>staged.index('第三段讲复盘') and len(image_layout.actual_images(staged))==1
    assert c.get(f'/api/drafts/{did}').json()['body']==body
    assert c.post('/api/article-images/layout-save',json=save_payload(plan,body=staged),headers=h).status_code==200
    assert c.get(f'/api/drafts/{did}').json()['body']==staged


def test_stale_text_and_foreign_draft_images_cannot_overwrite(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    body='已确认正文。\n\n（配图）'
    did=draft(c,h,body);other=draft(c,h,'另一篇原稿')
    tid=preview(c,h,did,body);fid=final(c,h,tid)
    assert c.post('/api/article-images',json={'request_key':str(uuid.uuid4()),'title':'标题','article':'未保存的新文字','draft_id':did,'count':2},headers=h).status_code==409
    assert c.post(f'/api/article-images/{fid}/attach',json={'draft_id':other},headers=h).status_code==409
    assert c.post('/api/article-images/layout-preview',json={'task_id':fid,'draft_id':other},headers=h).status_code==409
    assert c.post(f'/api/article-images/{tid}/final',json={'request_key':str(uuid.uuid4()),'panel':2,'confirm':True,'draft_id':other},headers=h).status_code==409
    plan=proposal(c,h,fid,did)
    other_row=c.get(f'/api/drafts/{other}').json()
    assert c.post('/api/article-images/layout-save',json=save_payload(plan,draft_id=other,expected_updated_at=other_row['updated_at']),headers=h).status_code==400
    assert c.put(f'/api/drafts/{did}',json={'body':'另一个窗口的新修改'},headers=h).status_code==200
    assert c.post('/api/article-images/layout-save',json=save_payload(plan),headers=h).status_code==409
    assert c.get(f'/api/drafts/{did}').json()['body']=='另一个窗口的新修改'
    assert db.row("SELECT COUNT(*) n FROM draft_versions WHERE origin='image-layout'")['n']==0


def test_ambiguous_changed_marker_context_is_not_guessed(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    body='段落一。\n\n（配图）\n\n段落二。'
    did=draft(c,h,body);fid=final(c,h,preview(c,h,did,body))
    changed='完全不同的新段落。\n\n（配图）\n\n另一段新正文。'
    c.put(f'/api/drafts/{did}',json={'body':changed},headers=h).raise_for_status()
    plan=proposal(c,h,fid,did)
    assert plan['body']==changed and plan['auto_placed']==0 and plan['warnings']


def test_bound_cover_becomes_cover_only_when_layout_confirmed(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    did=draft(c,h,'原稿正文。')
    result=c.post('/api/article-images/cover',json={'title':'封面标题','draft_id':did,'request_key':str(uuid.uuid4())},headers=h)
    assert result.status_code==200,result.text
    tid=result.json()['id'];plan=proposal(c,h,tid,did)
    assert plan['cover']['field']=='cover_wechat' and plan['body'].startswith('![封面-') and plan['body'].endswith('原稿正文。')
    assert c.get(f'/api/drafts/{did}').json()['cover_wechat'] is None
    assert c.post('/api/article-images/layout-save',json=save_payload(plan),headers=h).status_code==200
    saved=c.get(f'/api/drafts/{did}').json()
    assert saved['cover_wechat']==plan['cover']['image_id'] and saved['body']==plan['body']
    assert len(saved['images'])==1 and c.get(f'/api/images/{saved["cover_wechat"]}').content==png()


def test_legacy_preview_cannot_invent_panel_to_marker_mapping(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    body='旧预览没有约定编号位置。\n\n（配图）'
    did=draft(c,h,body);tid=preview(c,h,did,body)
    with db.tx() as conn:
        conn.execute("UPDATE article_image_tasks SET draft_id=NULL,source_hash='',placement_plan='[]' WHERE id=?",(tid,))
    response=c.post(f'/api/article-images/{tid}/final',json={'draft_id':did,'panel':1,'confirm':True,'request_key':str(uuid.uuid4())},headers=h)
    assert response.status_code==200,response.text
    plan=proposal(c,h,response.json()['id'],did)
    assert plan['body']==body and plan['auto_placed']==0 and plan['warnings']


def use(c,h,tid,did,key=None,**extra):
    return c.post(f'/api/article-images/{tid}/confirm-use',json={'draft_id':did,'confirm':True,'request_key':key or str(uuid.uuid4()),**extra},headers=h)


def test_confirm_existing_cover_inserts_into_body_and_retries_do_not_duplicate(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path);body='原文第一段。\n\n保留正文。';did=draft(c,h,body)
    tid=c.post('/api/article-images/cover',json={'title':'封面标题','draft_id':did,'request_key':str(uuid.uuid4())},headers=h).json()['id']
    iid=c.get(f'/api/article-images/{tid}').json()['attached_image_id']
    # Reproduce the real report: cover metadata was saved, body still has no cover.
    with db.tx() as conn:conn.execute('UPDATE drafts SET cover_wechat=? WHERE id=?',(iid,did))
    monkeypatch.setattr(article_images,'launch',lambda *_:(_ for _ in ()).throw(AssertionError('No regeneration')))
    key=str(uuid.uuid4());r=use(c,h,tid,did,key);assert r.status_code==200,r.text
    saved=c.get(f'/api/drafts/{did}').json();assert saved['body'].endswith(body)
    assert image_layout.actual_images(saved['body'])==[iid] and saved['cover_wechat']==iid
    assert use(c,h,tid,did,key).json()==r.json()
    assert use(c,h,tid,did).status_code==200
    assert c.get(f'/api/drafts/{did}').json()['body']==saved['body']
    task=c.get(f'/api/article-images/{tid}').json();assert task['in_draft_body'] and task['is_draft_cover']
    assert db.row("SELECT COUNT(*) n FROM draft_versions WHERE draft_id=? AND origin='before-image-layout'",(did,))['n']==1
    assert db.row('SELECT COUNT(*) n FROM publications')['n']==0


def test_confirm_numbered_image_applies_only_selected_and_keeps_slot_order_after_cover(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    body='第一段。\n\n（配图）\n\n第二段。\n\n（配图）\n\n最后一段。';did=draft(c,h,body);pid=preview(c,h,did,body)
    one=final(c,h,pid,1);two=final(c,h,pid,2)
    cover=c.post('/api/article-images/cover',json={'title':'封面','draft_id':did,'request_key':str(uuid.uuid4())},headers=h).json()['id']
    cover_id=c.get(f'/api/article-images/{cover}').json()['attached_image_id']
    assert use(c,h,cover,did).status_code==200
    assert image_layout.actual_images(c.get(f'/api/drafts/{did}').json()['body'])==[cover_id]
    assert use(c,h,two,did).status_code==200
    current=c.get(f'/api/drafts/{did}').json()['body'];two_id=c.get(f'/api/article-images/{two}').json()['attached_image_id']
    assert image_layout.actual_images(current)==[cover_id,two_id] and current.count('（配图）')==1
    assert current.index(f'/api/images/{two_id}')>current.index('第二段。')
    assert use(c,h,one,did).status_code==200
    current=c.get(f'/api/drafts/{did}').json()['body'];one_id=c.get(f'/api/article-images/{one}').json()['attached_image_id']
    assert image_layout.actual_images(current)==[cover_id,one_id,two_id] and '（配图）' not in current


def test_confirm_without_marker_requires_position_and_never_reports_saved(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path);body='没有标记的原文。';did=draft(c,h,body);fid=final(c,h,preview(c,h,did,body))
    r=use(c,h,fid,did);assert r.status_code==200,r.text
    assert not r.json()['ok'] and r.json()['requires_position'] and c.get(f'/api/drafts/{did}').json()['body']==body
    assert db.row("SELECT COUNT(*) n FROM draft_versions WHERE origin='image-layout'")['n']==0


def test_direct_confirmation_rejects_concurrent_edit_wrong_draft_and_preview_grid(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path);body='段落。\n\n（配图）';did=draft(c,h,body);other=draft(c,h,'其他文章。');pid=preview(c,h,did,body);fid=final(c,h,pid)
    assert use(c,h,pid,did).status_code==409
    assert use(c,h,fid,other).status_code==409
    assert use(c,h,fid,did,confirm=False).status_code==400
    original=image_layout.save
    def concurrent(ident,values,**kw):
        c.put(f'/api/drafts/{ident}',json={'body':'另一个窗口的新正文'},headers=h).raise_for_status()
        return original(ident,values,**kw)
    monkeypatch.setattr(image_layout,'save',concurrent)
    r=use(c,h,fid,did);assert r.status_code==409,r.text
    assert c.get(f'/api/drafts/{did}').json()['body']=='另一个窗口的新正文'


def test_confirm_unbound_cover_uses_explicit_destination_and_remembers_it(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path);did=draft(c,h,'选择的文章。')
    tid=c.post('/api/article-images/cover',json={'title':'独立封面','request_key':str(uuid.uuid4())},headers=h).json()['id']
    r=use(c,h,tid,did);assert r.status_code==200,r.text
    row=c.get(f'/api/article-images/{tid}').json();assert row['draft_id']==did and row['in_draft_body']


def test_layout_save_requires_the_selected_image_in_body_when_requested(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path);body='无标记的原文。';did=draft(c,h,body);fid=final(c,h,preview(c,h,did,body));plan=proposal(c,h,fid,did)
    assert plan['current_image_id'] and not image_layout.actual_images(plan['body'])
    r=c.post('/api/article-images/layout-save',json=save_payload(plan,required_image_id=plan['current_image_id']),headers=h)
    assert r.status_code==400 and '还没有放入正文' in r.json()['detail']
    assert c.get(f'/api/drafts/{did}').json()['body']==body

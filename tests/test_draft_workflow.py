import uuid
import pytest
from test_app import client, login, ORIGIN
from content_workbench import db, comments, draft_workflow as wf
H={'Origin':ORIGIN}
def create(c,body='我的真实正文'):
 r=c.post('/api/drafts',json={'platform':'wechat','title':'原稿','body':body},headers=H);r.raise_for_status();return r.json()['id']
def approve(c,did,layout=False):
 for stage in (['body','layout'] if layout else ['body']):
  value=c.get(f'/api/drafts/{did}/workflow').json()
  r=c.post(f'/api/drafts/{did}/approve',json={'stage':stage,'hash':value['body_hash' if stage=='body' else 'layout_hash'],'confirm':True},headers=H)
  r.raise_for_status()
def test_review_gates_and_stale_review():
 c=client();login(c);did=create(c)
 assert c.post(f'/api/drafts/{did}/publish',json={'confirm':True},headers=H).status_code==409
 cid=comments.add(did,'gary','这里再核实一次')
 assert c.post(f'/api/drafts/{did}/approve',json={'stage':'body','confirm':True,'hash':wf.review(did)['body_hash']},headers=H).status_code==409
 c.patch(f'/api/comments/{cid}',json={'status':'resolved'},headers=H).raise_for_status()
 approve(c,did,True);assert wf.review(did)['layout_ok']
 c.put(f'/api/drafts/{did}',json={'body':'后来又改了一句'},headers=H).raise_for_status()
 assert not wf.review(did)['body_ok']
 assert c.post(f'/api/drafts/{did}/publish',json={'confirm':True},headers=H).status_code==409
def test_compose_is_independent_ordered_idempotent_and_requires_review():
 c=client();login(c);a=create(c);b=create(c,'另一篇正文')
 payload={'source_ids':[b,a],'kind':'long','mode':'copy','request_key':str(uuid.uuid4())}
 assert c.post('/api/drafts/compose',json=payload,headers=H).status_code==409
 approve(c,a);approve(c,b)
 one=c.post('/api/drafts/compose',json=payload,headers=H);one.raise_for_status();did=one.json()['id']
 assert c.post('/api/drafts/compose',json=payload,headers=H).json()['id']==did
 assert c.get(f'/api/drafts/{did}').json()['body'].index('另一篇正文') < c.get(f'/api/drafts/{did}').json()['body'].index('我的真实正文')
 c.put(f'/api/drafts/{did}',json={'body':'合稿独立修改'},headers=H).raise_for_status()
 assert c.get(f'/api/drafts/{a}').json()['body']=='我的真实正文'
 assert not wf.review(did)['body_ok']
def test_habit_requires_confirmation_before_use():
 c=client();login(c);a=create(c);cid=comments.add(a,'gary','不要堆工具名称')
 r=c.post(f'/api/drafts/{a}/habit-candidates',json={'rule':'每次只解释和当前任务相关的工具','comment_id':cid},headers=H)
 r.raise_for_status()
 assert wf.workflow(a)['habits']==[]
 assert c.get('/api/learning').json()['lessons'][-1]['enabled'] is False


def test_images_and_cover_owned_by_independent_variant_and_rollback(monkeypatch):
 from content_workbench import images
 from test_article_images import png
 c=client();login(c);a=create(c)
 image=images.save(a,'原图.png',png())
 c.put(f'/api/drafts/{a}',json={'body':f'正文\n\n![](/api/images/{image["id"]})','cover_wechat':image['id']},headers=H).raise_for_status()
 # Cover is normally set by the attachment endpoint; use the same persisted relation here.
 with db.tx() as cn:cn.execute('UPDATE drafts SET cover_wechat=? WHERE id=?',(image['id'],a))
 approve(c,a)
 payload={'source_ids':[a],'kind':'xiaohongshu','mode':'copy','request_key':str(uuid.uuid4())}
 r=c.post('/api/drafts/compose',json=payload,headers=H);r.raise_for_status();did=r.json()['id']
 d=wf.draft(did);new=images.get(d['cover_wechat'])
 assert new['id']!=image['id'] and new['draft_id']==did
 assert images.path_of(new).read_bytes()==images.path_of(image).read_bytes()
 assert f'/api/images/{new["id"]}' in d['body']
 images.delete(new['id']);assert images.path_of(image).exists()
 before=db.row('SELECT COUNT(*) n FROM drafts')['n']
 # A broken source image must roll back the draft, rather than leave a partial variant.
 images.path_of(image).unlink()
 assert c.post('/api/drafts/compose',json={**payload,'request_key':str(uuid.uuid4())},headers=H).status_code==409
 assert db.row('SELECT COUNT(*) n FROM drafts')['n']==before


def test_ai_variant_records_actual_model_and_never_overwrites_source(monkeypatch):
 from content_workbench import llm,jobs,ai_rules
 c=client();login(c);a=create(c);approve(c,a)
 payload={'source_ids':[a],'kind':'video','mode':'ai','writing_model':'deepseek-v4-pro','request_key':str(uuid.uuid4()),'requirements':'给新手看的口播'}
 r=c.post('/api/drafts/compose',json=payload,headers=H);r.raise_for_status();did=r.json()['id']
 calls=[]
 def fake(prompt,**kw):
  calls.append(kw);assert '我的真实正文' in prompt
  return db.dumps({'title':'口播新稿','body':'这是独立生成的口播正文。','notes':'需要试读'})
 monkeypatch.setattr(llm,'run',fake)
 wf.derive_job(did)
 assert calls[0]['configuration'].writing_model=='deepseek-v4-pro' and calls[0]['task']=='write'
 assert wf.draft(a)['body']=='我的真实正文' and wf.draft(did)['body']=='这是独立生成的口播正文。'
 assert db.row("SELECT model FROM ai_prompt_runs WHERE target_id=? AND phase='derive'",(did,))['model']=='deepseek-v4-pro'
 assert not wf.review(did)['body_ok']
 # A stale approval hash and changed platform layout cannot be accepted.
 v=wf.review(did)
 c.put(f'/api/drafts/{did}',json={'body':'修改之后'},headers=H).raise_for_status()
 assert c.post(f'/api/drafts/{did}/approve',json={'stage':'body','confirm':True,'hash':v['body_hash']},headers=H).status_code==409
 approve(c,did,True)
 with db.tx() as cn:cn.execute("UPDATE drafts SET xhs=? WHERE id=?",(db.dumps({'body':'小红书版本又改了'}),did))
 assert not wf.review(did)['layout_ok']


def test_failed_derived_empty_retry_does_not_replay_finished_body(monkeypatch):
 c=client();login(c);a=create(c);approve(c,a)
 r=c.post('/api/drafts/compose',json={'source_ids':[a],'kind':'video','mode':'ai','writing_model':'deepseek-v4-pro','request_key':str(uuid.uuid4())},headers=H)
 did=r.json()['id']
 with db.tx() as cn:
  cn.execute("UPDATE jobs SET state='failed' WHERE kind='derive_draft'")
  cn.execute("UPDATE drafts SET status='failed' WHERE id=?",(did,))
 assert c.post(f'/api/drafts/{did}/retry-derived',json={},headers=H).status_code==400
 r=c.post(f'/api/drafts/{did}/retry-derived',json={'confirm':True},headers=H);r.raise_for_status()
 assert r.json()['model']=='deepseek-v4-pro'
 assert c.post(f'/api/drafts/{did}/retry-derived',json={'confirm':True},headers=H).status_code==409


@pytest.mark.parametrize('platform', ['douyin','xiaohongshu','wechat','x'])
def test_final_copy_stays_separate_from_working_revision_and_publication(platform):
 c=client();login(c)
 did=c.post('/api/drafts',json={'platform':platform,'title':'成稿标题','body':'已确认的文字。'},headers=H).json()['id']
 url=f'/api/drafts/{did}/final-copy'
 def payload():
  draft=c.get(f'/api/drafts/{did}').json()
  wf_state=c.get(f'/api/drafts/{did}/workflow').json()
  return {'confirm':True,'hash':wf_state['body_hash'],'expected_updated_at':draft['updated_at']}
 comment=comments.add(did,'gary','未解决的问题')
 assert c.post(url,json=payload(),headers=H).status_code==409
 c.patch(f'/api/comments/{comment}',json={'status':'resolved'},headers=H).raise_for_status()
 initial=payload()
 first=c.post(url,json=initial,headers=H)
 first.raise_for_status()
 assert c.post(url,json=initial,headers=H).json()==first.json()
 state=c.get(f'/api/drafts/{did}/workflow').json()
 assert state['body_ok'] and not state['layout_ok'] and state['final_copy']['current']
 assert c.get(f'/api/drafts/{did}').json()['status']=='draft'
 assert not db.rows('SELECT * FROM publications')
 assert [r['draft_id'] for r in c.get('/api/final-drafts',params={'q':'成稿','platform':platform}).json()]==[did]
 assert c.get('/api/final-drafts',params={'q':'不存在'}).json()==[]
 c.put(f'/api/drafts/{did}',json={'body':'还在改的第二版。'},headers=H).raise_for_status()
 assert c.post(url,json=initial,headers=H).status_code==409
 assert c.get(f'/api/final-drafts/{did}').json()['body']=='已确认的文字。'
 assert not c.get(f'/api/drafts/{did}/workflow').json()['final_copy']['current']
 c.post(url,json=payload(),headers=H).raise_for_status()
 assert c.get(f'/api/final-drafts/{did}').json()['body']=='还在改的第二版。'
 assert c.get(f'/api/final-drafts/{did}?version=1').json()['body']=='已确认的文字。'
 c.delete(f'/api/drafts/{did}',headers=H).raise_for_status()
 final=c.get(f'/api/final-drafts/{did}').json()
 assert final['body']=='还在改的第二版。' and not final['has_original']
 assert [x['version'] for x in final['versions']]==[2,1]
 r=c.post('/api/control',json={'request_key':str(uuid.uuid4()),'message':'在成稿箱找《成稿标题》'},headers=H)
 r.raise_for_status()
 turn=c.get('/api/control').json()['turns'][-1]
 assert turn['state']=='done' and turn['context']['mode']=='final'
 assert turn['context']['related'][0]['id']==did
 assert not db.rows("SELECT 1 FROM jobs WHERE kind='control_chat'")


def test_legacy_final_copy_with_missing_body_does_not_use_live_original():
 c=client();login(c);did=create(c)
 with db.tx() as conn:
  conn.execute('INSERT INTO final_copies VALUES(?,?,?,?,?,?)',(did,'wechat','旧成稿','', 'missing',db.now()))
 wf.setup()
 row=c.get(f'/api/final-drafts/{did}').json()
 assert row['body']=='' and row['title']=='旧成稿' and row['has_original'] and row['version']==1


def test_review_differentiates_drafting_records_from_unresolved_feedback():
 c=client();login(c);did=create(c)
 info=comments.add(did,'ai','写稿模型：示例\n已选对标：无\n已选素材：无')
 other=comments.add(did,'ai','起草自动检索素材（候选，不代表全部采用）：\n素材 #3：标题\n工作台：/#/materials/3\n原文字位 0–10\n正文')
 action=comments.add(did,'ai','核实第一个数字',quote='真实正文')
 state=wf.review(did)
 assert state['blockers']==['1 条批注未解决']
 assert [t['id'] for t in state['open_threads']]==[action]
 assert not state['open_threads'][0]['waiting_ai']
 records=c.get(f'/api/drafts/{did}/comments').json()
 assert [t['id'] for t in records if t['draft_record']]==[info,other]
 c.patch(f'/api/comments/{action}',json={'status':'resolved'},headers=H).raise_for_status()
 approve(c,did)
 comments.add(did,'gary','请把这个来源和数据再核对一次',parent_id=info)
 assert [t['id'] for t in wf.review(did)['open_threads']]==[info]

import pytest
from content_workbench import db, asset_workflow, video_research, hard_subtitles, media_imports, llm, research_protocol
from test_app import client,login,ORIGIN
H={'Origin':ORIGIN}

def setup():
 c=client();login(c);return c

def test_method_candidate_has_frozen_source_and_no_rule_activation(monkeypatch):
 from test_benchmarks import setup as source_setup,run
 c,b=source_setup();b=run(c,b['id'],monkeypatch);aid=b['analysis']['id']
 body={'analysis_id':aid,'title':'从实际流程写起','content':'展示过程；适用教程，效果待验证'}
 r=c.post(f"/api/benchmarks/{b['id']}/methods",json=body,headers=H);assert r.status_code==200,r.text
 tid=r.json()['id'];assert c.post(f"/api/benchmarks/{b['id']}/methods",json=body,headers=H).json()['id']==tid
 tip=db.row('SELECT * FROM content_tips WHERE id=?',(tid,));assert tip['validation']=='待验证' and tip['origin']=='AI建议'
 assert asset_workflow.methods([tid])[0]['source_link']['analysis_id']==aid
 with db.tx() as cn:cn.execute("UPDATE benchmarks SET body_hash='changed' WHERE id=?",(b['id'],))
 assert c.post(f"/api/benchmarks/{b['id']}/methods",json=body,headers=H).status_code==409
 assert db.row('SELECT snapshot FROM method_sources WHERE tip_id=?',(tid,))

def test_search_expands_related_terms_without_calling_model(monkeypatch):
 c=setup();monkeypatch.setattr(llm,'run',lambda *a,**kw:pytest.fail('local search must not call model'))
 c.post('/api/materials',json={'title':'视频字幕','body':'口播中的专有名词要核对','extract':False},headers=H)
 assert any(r['title']=='视频字幕' for r in c.get('/api/assets/search?q=语音').json())

def test_publication_snapshot_immutable_and_observations_append_only():
 c=setup();did=c.post('/api/drafts',json={'platform':'wechat','title':'原题','body':'发布时正文'},headers=H).json()['id']
 r=c.post(f'/api/drafts/{did}/delivery-record',json={'kind':'manual_published','confirm':True,'url':'https://example.com/article'},headers=H);assert r.status_code==200,r.text
 pid=db.row('SELECT id FROM publications')['id']
 c.put(f'/api/drafts/{did}',json={'body':'发布后修改'},headers=H)
 assert c.get(f'/api/publications/{pid}/review').json()['evidence']['snapshot']['body']=='发布时正文'
 for metrics in [{'views':None,'likes':0},{'views':50}]:
  r=c.post(f'/api/publications/{pid}/observations',json={'metrics':metrics,'next_test':'比较同主题开头'},headers=H);assert r.status_code==200,r.text
 rows=c.get(f'/api/publications/{pid}/review').json()['observations'];assert len(rows)==2 and rows[1]['metrics']=={'views':None,'likes':0}
 candidate={'observation_id':rows[0]['id'],'title':'比较开头','content':'下次同主题验证，不推断因果'}
 a=c.post(f'/api/publications/{pid}/method-candidate',json=candidate,headers=H).json()
 assert c.post(f'/api/publications/{pid}/method-candidate',json=candidate,headers=H).json()==a
 assert db.row('SELECT validation FROM content_tips WHERE id=?',(a['id'],))['validation']=='待验证'
 for metrics in [{'views':-1},{'likes':True},{'views':1.2}]:assert c.post(f'/api/publications/{pid}/observations',json={'metrics':metrics},headers=H).status_code==400
 c.post(f'/api/drafts/{did}/delivery-record',json={'kind':'copied'},headers=H)
 copied=db.row('SELECT id FROM publications ORDER BY id DESC')['id']
 assert c.post(f'/api/publications/{copied}/observations',json={},headers=H).status_code==400

def test_legacy_publication_backfill_never_claims_original():
 c=setup();did=c.post('/api/drafts',json={'platform':'wechat','body':'当前版本'},headers=H).json()['id']
 with db.tx() as cn:pid=cn.execute("INSERT INTO publications(draft_id,platform,state,created_at) VALUES(?,'wechat','manual_published',?)",(did,db.now())).lastrowid
 assert c.post(f'/api/publications/{pid}/freeze-current',json={},headers=H).status_code==400
 assert c.post(f'/api/publications/{pid}/freeze-current',json={'confirm':True},headers=H).status_code==200
 assert c.get(f'/api/publications/{pid}/review').json()['evidence']['timing']=='backfilled-current-not-original'

def test_subtitle_time_alignment_preserves_multiline_cues(tmp_path,monkeypatch):
 monkeypatch.setattr(media_imports,'folder',lambda mid:tmp_path)
 (tmp_path/'raw.txt').write_text('WEBVTT\n\n00:00:01.200 --> 00:00:03.400\n第一行\n第二行\n\n00:00:04.000 --> 00:00:05.000\n继续\n',encoding='utf-8')
 result=video_research.cues(1);assert len(result)==2 and result[0]['start']==1.2 and result[0]['end']==3.4 and '第二行' in result[0]['text']

def test_local_video_frames_do_not_call_model_and_keep_timestamps(tmp_path,monkeypatch):
 c=setup();bid=c.post('/api/benchmarks',json={'title':'视频','body':'原始正文'},headers=H).json()['id']
 with db.tx() as cn:
  mid=cn.execute("INSERT INTO media_imports(title,source_kind,created_at,updated_at) VALUES('视频','视频',?,?)",(db.now(),db.now())).lastrowid
  cn.execute('UPDATE benchmarks SET media_id=? WHERE id=?',(mid,bid))
 source=tmp_path/'source.mp4';source.write_bytes(b'fake')
 monkeypatch.setattr(media_imports,'video_source',lambda p:source)
 monkeypatch.setattr(hard_subtitles,'probe',lambda p:(960,540,60))
 monkeypatch.setattr(llm,'run',lambda *a,**kw:pytest.fail('local frames must not call model'))
 def frame(args,**kw):
  from pathlib import Path
  Path(args[-1]).write_bytes(b'frame')
 monkeypatch.setattr(hard_subtitles,'run_process',frame)
 assert c.post(f'/api/benchmarks/{bid}/video-research',json={'start':20,'end':10},headers=H).status_code==400
 r=c.post(f'/api/benchmarks/{bid}/video-research',json={'start':0,'end':30},headers=H);assert r.status_code==200,r.text
 rid=r.json()['id'];video_research.run({'run_id':rid})
 listing=c.get(f'/api/benchmarks/{bid}/video-research').json();frames=listing['runs'][0]['frames'];assert len(frames)==12 and frames[-1]['time']==27.5
 assert c.get(f'/api/video-research/{rid}/frames/12').status_code==404
 assert c.post(f'/api/video-research/{rid}/notes',json={'note':'画面与口播需要核对'},headers=H).status_code==200
 assert len(c.get(f'/api/benchmarks/{bid}/video-research').json()['runs'][0]['notes'])==1

def test_method_selection_frozen_and_unselected_excluded():
 from content_workbench import idea_cards
 from content_workbench.config import Settings
 c=setup()
 ids=[c.post('/api/tips',json={'title':title,'content':title,'origin':'AI建议','validation':'待验证'},headers=H).json()['id'] for title in ['选择的方法','未选的方法']]
 iid=c.post('/api/ideas',json={'title':'从想法开始','method_ids':[ids[0]]},headers=H).json()['id']
 context=idea_cards.snapshot(iid,{},Settings())
 c.patch(f'/api/tips/{ids[0]}',json={'content':'事后修改'},headers=H)
 prompt=idea_cards.context_prompt(context,'wechat')
 assert '选择的方法' in prompt and '未选的方法' not in prompt and '事后修改' not in prompt
 assert context['methods'][0]['validation']=='待验证'
 assert idea_cards.snapshot(iid,{'method_ids':[]},Settings())['methods']==[]

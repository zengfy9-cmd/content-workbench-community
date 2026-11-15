import json,uuid,base64
import pytest,httpx
from content_workbench import db,ai_rules,article_images,image_api,mobile_inbox,benchmarks,jobs,media_imports
from content_workbench.config import Settings,SecretStore
from test_app import client,login,ORIGIN
from test_article_images import png,setup as image_setup
H={'Origin':ORIGIN}

def authed():
 c=client();login(c);return c

def test_idea_multiple_groups_survive_restart_and_legacy_client():
 c=authed();iid=c.post('/api/ideas',json={'title':'多组选题'},headers=H).json()['id']
 groups=[c.post('/api/content-groups',json={'name':n},headers=H).json()['id'] for n in ['新手','AI工具','实战']]
 assert c.patch(f'/api/ideas/{iid}',json={'group_ids':groups[:2]},headers=H).status_code==200
 db.reset_for_tests();row=c.get('/api/ideas').json()[0]
 assert row['group_ids']==groups[:2] and len(row['groups'])==2
 for bad in [[99999],[True],['1'],None]:assert c.patch(f'/api/ideas/{iid}',json={'group_ids':bad},headers=H).status_code==400
 assert c.get('/api/ideas').json()[0]['group_ids']==groups[:2]
 c.patch(f'/api/ideas/{iid}',json={'group_id':groups[2]},headers=H)
 assert c.get('/api/ideas').json()[0]['group_ids']==groups[2:]
 c.patch(f'/api/ideas/{iid}',json={'group_ids':[]},headers=H)
 db.reset_for_tests();assert c.get('/api/ideas').json()[0]['group_ids']==[]

def test_rules_actual_writing_prompt_is_frozen_not_reconstructed(monkeypatch):
 c=authed();r=c.put('/api/ai-rules/writing',json={'expected_id':0,'content':'必须解释操作的具体原因。'},headers=H).json()
 iid=c.post('/api/ideas',json={'title':'有来源的任务'},headers=H).json()['id']
 preview=c.post(f'/api/ideas/{iid}/prompt-preview',json={'platform':'wechat','title':'预览新标题'},headers=H)
 assert preview.status_code==200 and '预览新标题' in preview.json()['prompt'] and '必须解释操作的具体原因' in preview.json()['prompt']
 context=preview.json()['context']
 c.put('/api/ai-rules/writing',json={'expected_id':r['id'],'content':'改成新的工作规则'},headers=H)
 with db.tx() as conn:
  did=conn.execute("INSERT INTO drafts(idea_id,platform,writing_context,created_at,updated_at) VALUES(?,'wechat',?,?,?)",(iid,db.dumps(context),db.now(),db.now())).lastrowid
 calls=[]
 monkeypatch.setattr(jobs.llm,'run',lambda prompt,**kw:(calls.append(prompt) or json.dumps({'title':'成稿','body':'真实的正文'})))
 monkeypatch.setattr(jobs.prompts,'voice_flags',lambda *a:[])
 jobs._draft(Settings(writing_model='deepseek-v4-pro'),{'draft_id':did,'writing_model':'deepseek-v4-pro'})
 trace=c.get(f'/api/ai-prompts/writing/{did}').json()
 assert trace[0]['prompt']==calls[0] and '必须解释操作的具体原因' in calls[0] and '改成新的工作规则' not in calls[0]
 assert trace[0]['model']=='deepseek-v4-pro'
 assert c.put('/api/ai-rules/writing',json={'expected_id':0,'content':'stale'},headers=H).status_code==409
 assert c.get('/api/ai-rules/images/document').status_code==200

def test_explicit_image_api_uses_same_references_freezes_route_and_never_native(monkeypatch,tmp_path):
 c,h=image_setup(monkeypatch,tmp_path);s=Settings.load();s.image_api_base='https://example.org/v1';s.image_api_model='gpt-image-2';s.save();SecretStore().set('image_api_key','test-image-only-key')
 calls=[]
 def generate(prompt,refs,**kw):
  calls.append((prompt,refs,kw));assert len(refs)==3;return {'images':[{'bytes':png(),'extension':'.png','prompt':prompt}]}
 monkeypatch.setattr(image_api,'run',generate)
 payload={'title':'图','article':'原文','count':2,'provider':'image-api','request_key':str(uuid.uuid4())}
 assert c.post('/api/article-images',json=payload,headers=h).status_code==400
 payload['confirm_api']=True;tid=c.post('/api/article-images',json=payload,headers=h).json()['id']
 row=c.get(f'/api/article-images/{tid}').json();assert row['state']=='completed' and row['provider']=='image-api'
 assert calls[0][2]['model']=='gpt-image-2' and 'image_gen.imagegen' not in calls[0][0]
 assert c.get(f'/api/article-images/{tid}/image').content==png()
 trace=c.get(f'/api/ai-prompts/images/{tid}').json();assert trace[0]['prompt']==calls[0][0]
 assert c.post(f'/api/article-images/{tid}/recover',headers=h).status_code==409
 assert c.post('/api/article-images',json=payload,headers=h).json()['id']==tid and len(calls)==1

def test_images_api_multipart_and_error_no_retry(monkeypatch,tmp_path):
 c=authed();SecretStore().set('image_api_key','test-key-not-a-real-key');ref=tmp_path/'cat.png';ref.write_bytes(png())
 monkeypatch.setattr(image_api,'validate_base',lambda x:x)
 requests=[]
 def handler(req):
  requests.append(req);assert b'filename="cat.png"' in req.content and b'image[]' in req.content
  return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(png()).decode()}]})
 real=httpx.Client
 monkeypatch.setattr(image_api.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
 result=image_api.run('提示',[ref],base='https://example.org/v1',model='gpt-image-2',cancelled=lambda:False,on_event=lambda x:None)
 assert result['images'][0]['bytes']==png() and len(requests)==1 and requests[0].url.path=='/v1/images/edits'
 def reject(req):return httpx.Response(401,json={'error':'Invalid key'})
 monkeypatch.setattr(image_api.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(reject),**kw))
 with pytest.raises(Exception,match='HTTP 401'):image_api.run('提示',[ref],base='https://example.org/v1',model='gpt-image-2',cancelled=lambda:False,on_event=lambda x:None)

def test_feishu_share_not_judgment_plain_notes_pending_and_replies_link(monkeypatch):
 c=authed();queued=[];monkeypatch.setattr(media_imports,'queue',lambda mid:queued.append(mid));monkeypatch.setattr(mobile_inbox.reading_metrics,'ensure',lambda *a,**kw:None)
 share={'message_id':'video','msg_type':'text','content':'8.20 复制打开抖音，看看作品 https://v.douyin.com/test/ :7pm'}
 mobile_inbox.ingest(share);bid=db.row('SELECT * FROM benchmarks')['id']
 assert not c.get(f'/api/benchmarks/{bid}').json()['mobile_notes'] and len(queued)==1
 mobile_inbox.ingest(share);assert len(queued)==1
 note={'message_id':'opinion','msg_type':'text','content':'这个开头值得借鉴。'}
 mobile_inbox.ingest(note);mobile_inbox.ingest(note)
 assert len(c.get('/api/benchmarks').json())==1 and len(c.get('/api/mobile-inbox/pending').json())==1
 r=c.post('/api/mobile-inbox/pending/opinion/link',json={'benchmark_id':bid},headers=H);assert r.status_code==200
 assert c.get(f'/api/benchmarks/{bid}').json()['mobile_notes'][0]['note']==note['content']
 mobile_inbox.ingest({'message_id':'reply','parent_id':'video','content':'保留原作者归属。','msg_type':'text'})
 assert len(c.get(f'/api/benchmarks/{bid}').json()['mobile_notes'])==2

def test_repair_archives_only_untouched_notes_and_keeps_edited_content(monkeypatch):
 c=authed();monkeypatch.setattr(mobile_inbox.reading_metrics,'ensure',lambda *a,**kw:None)
 bid=benchmarks.add_benchmark({'title':'原文','url':'https://example.org/a','body':'原作者正文'})['id']
 notes=[{'message_id':'a','content':'我的原始评价','msg_type':'text'},{'message_id':'b','content':'另一个想法','msg_type':'text'}]
 with db.tx() as conn:
  conn.execute("INSERT INTO mobile_captures VALUES('source',?,'','')",(bid,))
  for m in notes:
   old=benchmarks.add_benchmark({'title':m['content'],'body':m['content']})['id'];conn.execute('INSERT INTO mobile_captures VALUES(?,?,?,?)',(m['message_id'],old,'',''))
  conn.execute("UPDATE benchmarks SET body='后来编辑过的正文' WHERE title='另一个想法'")
 messages=[{'message_id':'source','content':'https://example.org/a','msg_type':'text'},*notes]
 r=c.post('/api/mobile-inbox/reconcile',json={'confirm':True,'messages':messages,'links':{'a':'source','b':'source'}},headers=H)
 assert r.status_code==200,r.text
 assert [x['state'] for x in r.json()['results']]==['linked','kept_modified']
 assert c.get(f'/api/benchmarks/{bid}').json()['mobile_notes'][0]['note']=='我的原始评价'
 assert any(i['title']=='另一个想法' for i in c.get('/api/benchmarks').json())

def test_video_extraction_writes_back_original_benchmark_without_distill(monkeypatch,tmp_path):
 c=authed();monkeypatch.setattr(media_imports,'queue',lambda mid:None)
 bid=benchmarks.add_benchmark({'title':'待补正文 · url','url':'https://v.douyin.com/x/','body':'尚未取得原文。'})['id']
 mobile_inbox.queue_video(bid,'https://v.douyin.com/x/')
 mid=benchmarks.require(bid)['media_id'];base=media_imports.folder(mid)
 raw='原视频转写的完整内容，尚未经人工核对。';(base/'raw.txt').write_text(raw,encoding='utf-8')
 media_imports.write(mid,stage=3,auto_xiaod=0)
 media_imports.run({'media_id':mid})
 assert benchmarks.require(bid)['body']==raw and media_imports.require(mid)['state']=='done'
 assert len(c.get('/api/benchmarks').json())==1


def test_locked_transcription_dependency_can_decode_real_pcm_wave(tmp_path):
 import wave,math,struct
 decode_audio=pytest.importorskip('faster_whisper.audio',reason='optional transcribe extra').decode_audio
 path=tmp_path/'test.wav'
 with wave.open(str(path),'wb') as w:
  w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
  w.writeframes(b''.join(struct.pack('<h',int(12000*math.sin(2*math.pi*440*n/16000))) for n in range(8000)))
 audio=decode_audio(str(path),sampling_rate=16000)
 assert len(audio)==8000 and .3<float(max(audio))<.5


def test_explicit_archive_of_referenced_import_note_keeps_existing_idea_link(monkeypatch):
 c=authed();monkeypatch.setattr(mobile_inbox.reading_metrics,'ensure',lambda *a,**kw:None)
 src=benchmarks.add_benchmark({'title':'真实来源','url':'https://example.org/video','body':'原文'})['id']
 note=benchmarks.add_benchmark({'title':'个人评价','body':'这是我的个人评价。'})['id']
 with db.tx() as conn:
  conn.execute("INSERT INTO mobile_captures VALUES('src',?,'','')",(src,));conn.execute("INSERT INTO mobile_captures VALUES('note',?,'','')",(note,))
  iid=conn.execute("INSERT INTO ideas(title,benchmark_id,created_at) VALUES('已选方向',?,?)",(note,db.now())).lastrowid
 payload={'confirm':True,'messages':[{'message_id':'src','content':'https://example.org/video'},{'message_id':'note','content':'这是我的个人评价。'}],'links':{'note':'src'}}
 c.post('/api/mobile-inbox/reconcile',json=payload,headers=H).raise_for_status()
 assert not benchmarks.require(note)['archived']
 payload['archive_referenced_notes']=True
 r=c.post('/api/mobile-inbox/reconcile',json=payload,headers=H);r.raise_for_status()
 assert benchmarks.require(note)['archived'] and db.row('SELECT benchmark_id FROM ideas WHERE id=?',(iid,))['benchmark_id']==note
 assert benchmarks.bundle(src)['mobile_notes'][0]['note']=='这是我的个人评价。'

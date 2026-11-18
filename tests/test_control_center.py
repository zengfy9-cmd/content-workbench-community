import uuid
import pytest
from test_app import client,login,ORIGIN
from content_workbench import db,control_center as cc,codex_native,local_executor,native_gateway
from content_workbench.config import SecretStore
from fastapi.testclient import TestClient
H={'Origin':ORIGIN}


def test_control_context_is_real_idempotent_readonly_and_subscription_only(monkeypatch):
 c=client();assert c.get('/api/control').status_code==401;login(c)
 with db.tx() as cn:
  bid=cn.execute("INSERT INTO benchmarks(title,body,body_hash,created_at,updated_at) VALUES('AI工作台测试','AI工作台的真实资料','hash',?,?)",(db.now(),db.now())).lastrowid
  cn.execute("INSERT INTO mobile_captures VALUES('message',?,'我认为这个流程值得研究','2026-11-14')",(bid,))
 payload={'request_key':str(uuid.uuid4()),'message':'AI工作台有什么可研究的'}
 r=c.post('/api/control',json=payload,headers=H);r.raise_for_status();tid=r.json()['id']
 assert c.post('/api/control',json=payload,headers=H).json()['id']==tid
 assert c.post('/api/control',json={**payload,'message':'换一句话'},headers=H).status_code==409
 calls=[]
 def fake(prompt,**kw):
  calls.append(kw);assert '真实资料' in prompt and '我认为这个流程值得研究' in prompt
  return '可先研究对标的具体流程，等你确认。'
 monkeypatch.setattr(codex_native,'run',fake);monkeypatch.setattr(native_gateway,'enabled',lambda:False)
 cc.run(tid);cc.run(tid)
 v=c.get('/api/control').json();assert v['turns'][0]['answer'].startswith('可先研究')
 assert len(calls)==1 and calls[0]['model']=='gpt-6.1-sol' and calls[0]['effort']=='high'
 assert not db.rows('SELECT * FROM drafts') and not db.rows('SELECT * FROM writing_plans')
 assert db.row('SELECT body FROM benchmarks WHERE id=?',(bid,))['body']=='AI工作台的真实资料'
 assert v['turns'][0]['context']['related'][0]['id'] == bid
 assert 'prompt' not in v['turns'][0]
 assert '我的评价' not in v['turns'][0]['context']['related'][0]
 assert 'personal' not in v['overview']['sources'][0]


def test_control_failure_and_restart_are_explicit_without_model_replay(monkeypatch):
 c=client();login(c)
 tid=c.post('/api/control',json={'message':'检查真实任务','request_key':str(uuid.uuid4())},headers=H).json()['id']
 monkeypatch.setattr(native_gateway,'enabled',lambda:True)
 def offline(*a,**kw):raise RuntimeError('本机离线，未转用API')
 monkeypatch.setattr(native_gateway,'rpc',offline)
 monkeypatch.setattr(codex_native,'run',lambda *a,**kw:pytest.fail('gateway failure must not fallback'))
 with pytest.raises(RuntimeError,match='离线'):cc.run(tid)
 assert cc.listing()['turns'][0]['error']=='本机离线，未转用API'
 c.post('/api/control',json={'message':'再次检查','request_key':str(uuid.uuid4())},headers=H).raise_for_status()
 cc.stop_interrupted();assert all(t['state']=='failed' for t in cc.listing()['turns'])


def _send(c, message):
 r = c.post('/api/control', json={'request_key':str(uuid.uuid4()),'message':message},headers=H)
 r.raise_for_status()
 return db.row('SELECT * FROM control_turns WHERE id=?',(r.json()['id'],))


def test_screenshot_style_quote_search_reads_actual_hit_and_skips_model():
 c=client();login(c)
 with db.tx() as cn:
  mid=cn.execute("INSERT INTO materials(source,title,body,created_at) VALUES('note','通用笔记',?,?)",
      ('前段完全无关。'*500+'上下文就是打通数据，模型更新迭代，你也能更新。不要把下一句混进去。',db.now())).lastrowid
  cn.execute("INSERT INTO benchmarks(title,body,body_hash,created_at,updated_at) VALUES('外部对标','这是外部作者说的上下文','hash',?,?)",(db.now(),db.now()))
 row=_send(c,'上下文。我的素材里面有这个词的句子给我调出来')
 assert row['state']=='done' and row['prompt']==''
 assert '找到 1 条' in row['answer']
 source=db.loads(row['context'],{})['related'][0]
 assert source['id']==mid and source['title']=='通用笔记' and source['kind']=='materials'
 assert source['excerpt']=='上下文就是打通数据，模型更新迭代，你也能更新。'
 assert source['location']=='手记' and source['collected_at']>0 and not source['judgment']
 assert '没找到' in _send(c,'查素材里含「没出现过的词」的原话')['answer']
 assert not db.rows("SELECT 1 FROM jobs WHERE kind='control_chat'")


def test_direct_today_and_topics_use_saved_data_and_do_not_queue_model(monkeypatch):
 c=client();login(c)
 monkeypatch.setattr(cc,'_today',lambda:'2026-11-14')
 with db.tx() as cn:
  for day,title,state in [('2026-11-14','现在该做的','todo'),('2026-11-13','昨天没完成的','todo'),
                          ('2026-11-15','明天才开始','todo'),('2026-11-14','已经完成','done')]:
   cn.execute("INSERT INTO writing_plans(day,title,state,created_at) VALUES(?,?,?,?)",(day,title,state,db.now()))
  cn.execute("INSERT INTO ideas(title,created_at) VALUES('已有待挑选题',?)",(db.now(),))
 row=_send(c,'帮我看看今日任务')
 assert row['state']=='done' and '现在该做的' in row['answer'] and '昨天没完成的' in row['answer']
 assert '明天才开始' not in row['answer'] and '已经完成' not in row['answer']
 row=_send(c,'我有哪些选题？')
 assert row['state']=='done' and '已有待挑选题' in row['answer']
 assert not db.rows("SELECT 1 FROM jobs WHERE kind='control_chat'")


def test_scheduling_and_topics_use_targeted_inputs_no_recent_library_or_history(monkeypatch):
 c=client();login(c)
 monkeypatch.setattr(cc,'_today',lambda:'2026-11-14')
 with db.tx() as cn:
  for day,title in [('2026-11-14','今天的任务'),('2026-11-15','明天的任务')]:
   cn.execute("INSERT INTO writing_plans(day,title,created_at) VALUES(?,?,?)",(day,title,db.now()))
  cn.execute("INSERT INTO ideas(title,angle,created_at) VALUES('真实选题','一个切入点',?)",(db.now(),))
  cn.execute("INSERT INTO benchmarks(title,body,body_hash,created_at,updated_at) VALUES('无关的对标','无关正文','hash',?,?)",(db.now(),db.now()))
  cn.execute("INSERT INTO control_turns(request_key,message,answer,state,context,prompt,created_at,updated_at) VALUES(?,?,?,'done','{}','',?,?)",
             (str(uuid.uuid4()),'前一轮无关对话','无关历史回答',db.now(),db.now()))
 scheduling=_send(c,'帮我判断接下来先做什么')
 assert '今天的任务' in scheduling['prompt'] and '明天的任务' in scheduling['prompt']
 assert all(value not in scheduling['prompt'] for value in ('无关的对标','无关历史回答','真实选题'))
 with db.tx() as cn:
  cn.execute("UPDATE control_turns SET state='done' WHERE id=?",(scheduling['id'],))
 topics=_send(c,'帮我比较一下选题，哪个值得写？')
 assert '真实选题' in topics['prompt'] and '一个切入点' in topics['prompt']
 assert all(value not in topics['prompt'] for value in ('今天的任务','无关的对标','无关历史回答'))
 assert len(topics['prompt']) < 900


def test_executor_text_auth_exact_model_and_cached_success_failure(monkeypatch):
 SecretStore().set('cloud_gateway_key','test-private-key')
 c=TestClient(local_executor.app());h={'Authorization':'Bearer test-private-key'}
 payload={'execution_id':str(uuid.uuid4()),'model':'gpt-6.1-sol','effort':'high','prompt':'短测试'}
 assert c.post('/text',json=payload).status_code==401
 assert c.post('/text',json={**payload,'effort':'xhigh'},headers=h).status_code==400
 calls=[]
 monkeypatch.setattr(codex_native,'run',lambda *a,**kw:calls.append(kw) or '成功')
 assert c.post('/text',json=payload,headers=h).json()['text']=='成功'
 assert c.post('/text',json=payload,headers=h).json()['text']=='成功'
 assert len(calls)==1 and calls[0]['model']=='gpt-6.1-sol'
 assert c.post('/text',json={**payload,'prompt':'另一个请求'},headers=h).status_code==409
 def failed(*a,**kw):raise codex_native.CodexError('额度不足')
 monkeypatch.setattr(codex_native,'run',failed)
 failed_request={**payload,'execution_id':str(uuid.uuid4())}
 assert c.post('/text',json=failed_request,headers=h).json()['error']=='额度不足'
 monkeypatch.setattr(codex_native,'run',lambda *a,**kw:pytest.fail('must not replay failed request'))
 assert c.post('/text',json=failed_request,headers=h).json()['error']=='额度不足'


def test_control_screenshot_is_private_validated_and_passed_to_vision(monkeypatch):
 import base64
 from test_article_images import png
 c=client();login(c)
 raw=png()
 key=str(uuid.uuid4())
 req={'request_key':key,'message':'','images':[base64.b64encode(raw).decode()]}
 invalid={**req,'images':[base64.b64encode(b'<svg/>').decode()]}
 assert c.post('/api/control',json=invalid,headers=H).status_code==400
 r=c.post('/api/control',json=req,headers=H);r.raise_for_status();tid=r.json()['id']
 assert c.post('/api/control',json=req,headers=H).json()['existing']
 assert c.post('/api/control',json={**req,'message':'换个问题'},headers=H).status_code==409
 path=f'/api/control/{tid}/images/1'
 assert client().get(path).status_code==401
 assert c.get(path).content==raw and c.get(path).headers['content-type']=='image/png'
 assert c.get('/api/control').json()['turns'][-1]['images']==[1]
 monkeypatch.setattr(native_gateway,'enabled',lambda:True)
 monkeypatch.setattr(native_gateway,'health',lambda:{'vision':True})
 def rpc(kind,payload,**kwargs):
  assert kind=='vision' and base64.b64decode(payload['images'][0])==raw
  assert payload['model']=='gpt-6.1-sol' and payload['effort']=='high'
  return {'text':'截图验收回复'}
 monkeypatch.setattr(native_gateway,'rpc',rpc)
 cc.run(tid)
 assert c.get('/api/control').json()['turns'][-1]['answer']=='截图验收回复'


def test_executor_vision_passes_temporary_image_and_cleans_up(monkeypatch):
 import base64
 from test_article_images import png
 SecretStore().set('cloud_gateway_key','test-private-key')
 c=TestClient(local_executor.app())
 h={'Authorization':'Bearer test-private-key'}
 request={'execution_id':str(uuid.uuid4()),'model':'gpt-6.1-sol','effort':'high',
          'prompt':'请查看截图','images':[base64.b64encode(png()).decode()]}
 seen=[]
 def fake(prompt,*,attachments,**kw):
  seen.extend(attachments);assert attachments[0].read_bytes()==png()
  return '识别成功'
 monkeypatch.setattr(codex_native,'run',fake)
 assert c.post('/vision',json=request,headers=h).json()['text']=='识别成功'
 assert len(seen)==1 and not seen[0].exists()
 assert c.post('/vision',json=request,headers=h).json()['text']=='识别成功'
 assert len(seen)==1

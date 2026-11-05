import uuid
from content_workbench import db,codex_tasks,codex_native
from test_app import client,login,ORIGIN


def test_task_lifecycle_idempotency_adoption_and_original_recovery(monkeypatch):
    def run(prompt,**kwargs):
        assert kwargs['effort']=='xhigh'
        kwargs['on_event']({'thread_id':'original','turn_id':'turn'})
        kwargs['on_event']({'delta':'生成中'})
        return '完成的测试稿'
    monkeypatch.setattr(codex_native,'run',run)
    monkeypatch.setattr(codex_tasks,'launch',codex_tasks.execute)
    c=client();login(c);h={'Origin':ORIGIN}
    p={'request_key':str(uuid.uuid4()),'title':'测试成果','project':'测试项目','platform':'wechat','prompt':'真实材料测试'}
    r=c.post('/api/codex/tasks',headers=h,json=p);assert r.status_code==200,r.text
    tid=r.json()['id'];task=c.get(f'/api/codex/tasks/{tid}').json()
    assert task['state']=='completed' and task['result']=='完成的测试稿' and task['model']=='gpt-6.1-sol'
    assert task['effort']=='xhigh'
    assert c.post('/api/codex/tasks',headers=h,json=p).json()['id']==tid
    assert c.post('/api/codex/tasks',headers=h,json={**p,'prompt':'changed'}).status_code==409
    did=c.post(f'/api/codex/tasks/{tid}/adopt',headers=h).json()['id']
    assert c.post(f'/api/codex/tasks/{tid}/adopt',headers=h).json()['id']==did
    draft=c.get(f'/api/drafts/{did}').json();assert draft['body']==task['result'] and draft['status']=='draft'
    assert db.row('SELECT COUNT(*) n FROM publications')['n']==0
    assert c.post(f'/api/codex/tasks/{tid}/cancel',headers=h).json()['state']=='completed'
    class Recovery:
        def account(self):pass
        def request(self,method,params):
            assert method=='thread/read' and params['threadId']=='original'
            return {'thread':{'turns':[{'id':'turn','status':'completed','items':[{'type':'agentMessage','text':'原任务结果'}]}]}}
        def close(self):pass
    monkeypatch.setattr(codex_native,'Client',Recovery)
    assert c.post(f'/api/codex/tasks/{tid}/recover',headers=h).json()['result']=='原任务结果'


def test_task_auth_and_failed_result_not_adopted():
    c=client();assert c.get('/api/codex/tasks').status_code==401
    login(c)
    assert c.post('/api/codex/tasks',json={}).status_code==403
    assert c.post('/api/codex/tasks/999/adopt',headers={'Origin':ORIGIN}).status_code==409


def test_restart_does_not_resubmit_and_busy_requests_do_not_duplicate(monkeypatch):
    launched=[];monkeypatch.setattr(codex_tasks,'launch',launched.append)
    c=client();login(c);h={'Origin':ORIGIN}
    p={'request_key':str(uuid.uuid4()),'title':'测试','project':'项目','platform':'wechat','prompt':'任务'}
    tid=c.post('/api/codex/tasks',json=p,headers=h).json()['id']
    assert c.post('/api/codex/tasks',json={**p,'request_key':str(uuid.uuid4())},headers=h).status_code==409
    c=client();login(c)
    assert c.get(f'/api/codex/tasks/{tid}').json()['state']=='interrupted'
    assert launched==[tid]
    assert c.post(f'/api/codex/tasks/{tid}/recover',headers=h).status_code==409


def test_cancelled_worker_result_is_not_adopted(monkeypatch):
    launched=[];monkeypatch.setattr(codex_tasks,'launch',launched.append)
    c=client();login(c);h={'Origin':ORIGIN}
    p={'request_key':str(uuid.uuid4()),'title':'测试','project':'项目','platform':'wechat','prompt':'任务'}
    tid=c.post('/api/codex/tasks',json=p,headers=h).json()['id']
    assert c.post(f'/api/codex/tasks/{tid}/cancel',headers=h).json()['state']=='cancelled'
    monkeypatch.setattr(codex_native,'run',lambda *a,**kw:'不采用的迟到结果')
    codex_tasks.execute(tid)
    assert c.get(f'/api/codex/tasks/{tid}').json()['state']=='cancelled'
    assert c.post(f'/api/codex/tasks/{tid}/adopt',headers=h).status_code==409

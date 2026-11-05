import json
import queue
from collections import deque
import pytest
from content_workbench import codex_native, llm
from content_workbench.config import Settings


class FakeClient:
    events=[]
    requests=[]
    workdir='private'
    closed=False
    def __init__(self): self.index=0
    def account(self):return {'type':'chatgpt'}
    def request(self,method,params):
        self.requests.append((method,params))
        return {'thread':{'id':'original-thread'}} if method=='thread/start' else {'turn':{'id':'original-turn'}}
    def receive(self):
        value=self.events[self.index];self.index+=1;return value
    def send(self,value):self.requests.append(('send',value))
    def close(self):self.closed=True


def test_run_returns_final_text_and_preserves_requested_model(monkeypatch):
    FakeClient.events=[{'method':'item/completed','params':{'item':{'type':'agentMessage','text':'完整结果'}}},{'method':'turn/completed','params':{'turn':{'status':'completed'}}}]
    FakeClient.requests=[]
    monkeypatch.setattr(codex_native,'Client',FakeClient)
    events=[]
    assert codex_native.run('材料',on_event=events.append)=='完整结果'
    assert all(p.get('model','gpt-6.1-sol')=='gpt-6.1-sol' for m,p in FakeClient.requests)
    assert events[0]['thread_id']=='original-thread'
    assert next(p for m,p in FakeClient.requests if m=='turn/start')['effort']=='xhigh'


def test_failure_no_model_or_api_fallback_and_cancellation(monkeypatch):
    FakeClient.events=[{'method':'turn/completed','params':{'turn':{'status':'failed','error':{'message':json.dumps({'detail':'model not supported'})}}}}]
    FakeClient.requests=[]
    monkeypatch.setattr(codex_native,'Client',FakeClient)
    with pytest.raises(codex_native.CodexError,match='model not supported'):codex_native.run('材料')
    assert len([r for r in FakeClient.requests if r[0]=='turn/start'])==1
    with pytest.raises(codex_native.CodexError,match='停止'):codex_native.run('材料',cancelled=lambda:True)


def test_api_key_identity_rejected():
    client=codex_native.Client.__new__(codex_native.Client)
    client.request=lambda *a,**kw:{'account':{'type':'apiKey'}}
    with pytest.raises(codex_native.CodexError,match='API Key'):client.account()


def test_notifications_before_rpc_reply_preserved():
    client=codex_native.Client.__new__(codex_native.Client)
    client.seq=0;client.events=queue.Queue();client.pending=deque();client.send=lambda v:None
    event={'method':'item/completed','params':{'item':{'text':'early result'}}}
    client.events.put(event);client.events.put({'id':1,'result':{'ok':True}})
    assert client.request('test',{})=={'ok':True}
    assert client.receive()==event


def test_existing_llm_routes_to_codex_and_rejects_unsupported_tools(monkeypatch):
    settings=Settings(llm_backend='codex-cli')
    monkeypatch.setattr(codex_native,'run',lambda prompt,**kw:'来自订阅')
    assert llm.run('test',configuration=settings)=='来自订阅'
    with pytest.raises(llm.LlmUnavailable,match='文字'):llm.run('test',configuration=settings,tools=['Read'])

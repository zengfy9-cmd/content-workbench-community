import base64
import uuid

from fastapi.testclient import TestClient
from content_workbench import local_executor,codex_native,native_gateway
from content_workbench.config import SecretStore
from test_article_images import png


def test_image_executor_authentication_single_run_and_original_recovery(monkeypatch):
    SecretStore().set('cloud_gateway_key','test-private-executor-key')
    client=TestClient(local_executor.app());h={'Authorization':'Bearer test-private-executor-key'}
    assert client.post('/status').status_code==401
    assert client.post('/status',headers=h).json()['ok'] is True
    import hashlib
    digest=hashlib.sha256(png()).hexdigest()
    assert client.post('/references',json={'hashes':[digest]},headers=h).json()['missing']==[digest]
    calls=[]
    def generate(prompt,refs,**kw):
        calls.append(1);assert refs[0].read_bytes()==png()
        kw['on_event']({'thread_id':'thread','turn_id':'turn'})
        return {'images':[{'bytes':png(),'extension':'.png','prompt':'合成测试图'}]}
    monkeypatch.setattr(codex_native,'run_images',generate)
    local_executor.reference_cache(png())
    assert client.post('/references',json={'hashes':[digest]},headers=h).json()['missing']==[]
    payload={'execution_id':str(uuid.uuid4()),'prompt':'合成测试','references':[{'sha256':digest}]}
    response=client.post('/images',json=payload,headers=h)
    assert response.status_code==200 and 'result' in response.text
    assert client.post('/images',json=payload,headers=h).json()['error'].startswith('已生成')
    recovered=client.post('/recover',json={'execution_id':payload['execution_id']},headers=h).json()
    assert base64.b64decode(recovered['images'][0]['base64'])==png() and calls==[1]
    assert client.post('/images',json={**payload,'prompt':'另一个任务'},headers=h).status_code==409


def test_offline_gateway_does_not_fall_back_to_any_other_service(monkeypatch):
    import httpx
    monkeypatch.setenv('CW_GATEWAY_UDS','/missing/socket')
    SecretStore().set('cloud_gateway_key','test-private-executor-key')
    def offline(*a,**k):raise httpx.ConnectError('offline')
    monkeypatch.setattr(native_gateway,'client',offline)
    from pytest import raises
    with raises(RuntimeError,match='执行器离线'):native_gateway.rpc('status')

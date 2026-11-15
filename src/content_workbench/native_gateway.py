"""Cloud -> owner's local executor over a private SSH-forwarded Unix socket."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path

import httpx
from .config import SecretStore

OFFLINE='本机执行器离线或连接中断；云端文章和图片仍可查看、编辑。没有改用其他 API。'


def enabled():return bool(os.environ.get('CW_GATEWAY_UDS'))


def client(timeout=20):
    key=SecretStore().get('cloud_gateway_key')
    if not key:raise RuntimeError(OFFLINE)
    return httpx.Client(transport=httpx.HTTPTransport(uds=os.environ['CW_GATEWAY_UDS']),
                        base_url='http://executor',headers={'Authorization':'Bearer '+key},
                        timeout=httpx.Timeout(timeout,connect=4),trust_env=False)


def rpc(kind,payload=None,timeout=600):
    try:
        with client(timeout) as c:
            response=c.post('/'+kind,json=payload or {})
            if response.status_code!=200:raise RuntimeError(OFFLINE)
            value=response.json()
            if value.get('error'):raise RuntimeError(value['error'])
            return value
    except (httpx.HTTPError,OSError,ValueError):raise RuntimeError(OFFLINE) from None


_health=(0,None)
def health():
    global _health
    if time.monotonic()-_health[0]<5 and _health[1] is not None:return _health[1]
    try:result=rpc('status',timeout=8)
    except RuntimeError:result={'ok':False,'detail':OFFLINE}
    _health=(time.monotonic(),result)
    return result


def run_images(prompt,references,*,execution_id,cancelled=None,on_event=None):
    raw=[Path(p).read_bytes() for p in references]
    hashes=[hashlib.sha256(data).hexdigest() for data in raw]
    missing=set(rpc('references',{'hashes':hashes},timeout=10)['missing'])
    payload={'execution_id':execution_id,'prompt':prompt,'references':[
        {'sha256':digest,**({'base64':base64.b64encode(data).decode()} if digest in missing else {})}
        for digest,data in zip(hashes,raw)]}
    try:
        with client(12) as c,c.stream('POST','/images',json=payload) as response:
            if response.status_code!=200:raise RuntimeError(OFFLINE)
            for line in response.iter_lines():
                if cancelled and cancelled():raise RuntimeError('配图任务已停止；没有重新生成')
                if not line:continue
                item=json.loads(line)
                if item.get('error'):raise RuntimeError(item['error'])
                if 'event' in item and on_event:on_event(item['event'])
                if 'result' in item:
                    result=item['result']
                    for image in result['images']:image['bytes']=base64.b64decode(image.pop('base64'),validate=True)
                    return result
            raise RuntimeError(OFFLINE)
    except (httpx.HTTPError,OSError,ValueError):raise RuntimeError(OFFLINE) from None


def recover(row):
    result=rpc('recover',{'execution_id':row['request_key'],'thread_id':row['thread_id'],'turn_id':row['turn_id']},timeout=60)
    image=result['images'][0]
    return base64.b64decode(image['base64'],validate=True),image['extension'],image.get('prompt','')

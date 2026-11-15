"""Keep trusted localhost integrations on the one cloud database after migration."""
import re
import json
import asyncio

import httpx
from starlette.responses import JSONResponse,Response
from .config import SecretStore,data_dir


async def forward(request,settings):
    base=settings.cloud_url
    key=SecretStore().get('cloud_machine_key')
    if not re.fullmatch(r'https://[a-z0-9.-]+',base) or not key:
        return JSONResponse({'detail':'云端连接尚未配置完整；内容没有另存到本机'},status_code=503)
    # The optional loopback forward is encrypted/authenticated by SSH on the
    # network. It avoids routing localhost integrations through public HTTPS.
    local=data_dir()/'cloud-link.json'
    transport=json.loads(local.read_text(encoding='utf-8')).get('local_api_url',base) if local.is_file() else base
    if transport!=base and transport!='http://127.0.0.1:18768':return JSONResponse({'detail':'本机隧道地址无效'},status_code=503)
    # Public HTTPS handles large image downloads more efficiently than Win32
    # OpenSSH local forwarding; both transports address the same private store.
    if request.method in ('GET','HEAD'):transport=base
    url=transport+request.url.path+('?' + request.url.query if request.url.query else '')
    headers={'Origin':base,'Host':base.removeprefix('https://'),'X-Workbench-Machine':key}
    if request.headers.get('content-type'):headers['Content-Type']=request.headers['content-type']
    try:
        body=await request.body()
        def send():
            with httpx.Client(timeout=httpx.Timeout(180,connect=10),follow_redirects=False,trust_env=transport==base) as client:
                return client.request(request.method,url,headers=headers,content=body)
        response=await asyncio.to_thread(send)
        allowed={k:v for k,v in response.headers.items() if k in ('content-type','content-disposition','cache-control')}
        allowed['Cache-Control']='no-store'
        return Response(response.content,status_code=response.status_code,headers=allowed)
    except httpx.HTTPError:
        return JSONResponse({'detail':'暂时连不上云端，当前修改尚未保存；请保留编辑内容后重试'},status_code=503)

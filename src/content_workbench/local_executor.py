"""Authenticated loopback-only executor. No account tokens are exported to the cloud."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import queue
import re
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from PIL import Image

from . import codex_native, control_attachments
from .config import SecretStore, data_dir, _write_private

IMAGE_LOCK=threading.Lock()
TEXT_LOCK=threading.Lock()


def root():
    p=data_dir()/'cloud-executor';p.mkdir(exist_ok=True);return p


def run_path(value):
    try:key=str(uuid.UUID(value))
    except (ValueError,TypeError,AttributeError):raise HTTPException(400,'请求编号无效') from None
    return root()/(key+'.json')


def reference_cache(raw):
    digest=hashlib.sha256(raw).hexdigest();directory=root()/'references';directory.mkdir(exist_ok=True)
    path=directory/(digest+'.png')
    if not path.exists():path.write_bytes(raw);path.chmod(0o600)
    return path


def seed_references():
    directory=data_dir()/'article-images'
    for path in [*directory.glob('*.png'),*(directory/'references').glob('*')]:
        if path.is_file() and path.suffix.lower() in ('.png','.jpg','.jpeg','.webp'):reference_cache(path.read_bytes())


def app():
    service=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    @service.middleware('http')
    async def guard(request,call_next):
        from starlette.responses import JSONResponse
        expected=SecretStore().get('cloud_gateway_key') or ''
        given=request.headers.get('authorization','').removeprefix('Bearer ')
        if not expected or not hmac.compare_digest(given,expected):return JSONResponse({'error':'unauthorized'},status_code=401)
        if int(request.headers.get('content-length','0'))>100*1024*1024:return JSONResponse({'error':'request too large'},status_code=413)
        return await call_next(request)

    @service.post('/status')
    def status():
        return {'ok':True,'detail':'本机 Codex 执行器在线','text':True,'text_model':'gpt-6.1-sol','text_effort':'high','vision':True}

    def text_job(body:dict, attachments):
        path=run_path(body.get('execution_id')).with_suffix('.text.json')
        prompt=body.get('prompt')
        if body.get('model')!='gpt-6.1-sol' or body.get('effort')!='high' or not isinstance(prompt,str) or not 1<=len(prompt)<=50000:
            raise HTTPException(400,'中控仅接受GPT-6.1 Sol high及有效文字')
        digest=hashlib.sha256(prompt.encode())
        for raw, _, _ in attachments:digest.update(hashlib.sha256(raw).digest())
        signature=digest.hexdigest()
        if not TEXT_LOCK.acquire(blocking=False):raise HTTPException(409,'本机已有文字任务，请等待')
        try:
            if path.exists():
                old=json.loads(path.read_text(encoding='utf8'))
                if old.get('signature')!=signature:raise HTTPException(409,'请求内容不同')
                if old.get('text'):return {'text':old['text'],'model':'gpt-6.1-sol','effort':'high'}
                return {'error':old.get('error') or '原文字任务未确认完成，没有重新调用'}
            _write_private(path,json.dumps({'signature':signature,'state':'running'}))
            try:
                answer=control_attachments.run(codex_native.run,prompt,attachments,model='gpt-6.1-sol',effort='high',timeout=1100)
                _write_private(path,json.dumps({'signature':signature,'state':'done','text':answer},ensure_ascii=False))
                return {'text':answer,'model':'gpt-6.1-sol','effort':'high'}
            except Exception as exc:
                error=str(exc) if isinstance(exc,codex_native.CodexError) else '本机文字调用失败，没有转用其他API'
                _write_private(path,json.dumps({'signature':signature,'state':'failed','error':error},ensure_ascii=False))
                return {'error':error}
        finally:TEXT_LOCK.release()

    @service.post('/text')
    def text(body:dict):
        return text_job(body, [])

    @service.post('/vision')
    def vision(body:dict):
        try:
            attachments=control_attachments.decode(body.get('images'))
            if not attachments:raise ValueError('请附上截图')
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from None
        return text_job(body, attachments)

    @service.post('/references')
    async def references(request:Request):
        body=await request.json();hashes=body.get('hashes')
        if not isinstance(hashes,list) or len(hashes)>6 or any(not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h) for h in hashes):raise HTTPException(400,'参考图编号无效')
        seed_references()
        return {'missing':[h for h in hashes if not (root()/'references'/(h+'.png')).is_file()]}

    @service.post('/images')
    async def images(request:Request):
        body=await request.json();path=run_path(body.get('execution_id'));prompt=body.get('prompt');refs=body.get('references')
        if not isinstance(prompt,str) or not 1<=len(prompt)<=100000 or not isinstance(refs,list) or not 1<=len(refs)<=6:raise HTTPException(400,'图片参数无效')
        signature=hashlib.sha256(json.dumps({'prompt':prompt,'refs':refs},sort_keys=True).encode()).hexdigest()
        if not IMAGE_LOCK.acquire(blocking=False):raise HTTPException(409,'本机已有图片任务，请等待')
        if path.exists():
            old=json.loads(path.read_text(encoding='utf-8'));IMAGE_LOCK.release()
            if old['signature']!=signature:raise HTTPException(409,'请求内容与原任务不同')
            if old.get('result'):return {'error':'已生成，请使用读取原任务图片取回，不会重新生成'}
            return {'error':'原任务未确认完成，请先读取原任务图片；没有自动重生'}
        _write_private(path,json.dumps({'signature':signature,'state':'starting'}))
        events=queue.Queue();stop=threading.Event()
        def work():
            try:
                folder=root()/str(uuid.UUID(body['execution_id']));folder.mkdir(exist_ok=True)
                references=[]
                for index,encoded in enumerate(refs):
                    if isinstance(encoded,dict):
                        digest=encoded.get('sha256')
                        if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('参考图编号无效')
                        cached=root()/'references'/(digest+'.png')
                        raw=base64.b64decode(encoded['base64'],validate=True) if 'base64' in encoded else cached.read_bytes()
                        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('参考图校验不一致')
                    else:raw=base64.b64decode(encoded,validate=True)
                    if len(raw)>20*1024*1024:raise ValueError('参考图片过大')
                    Image.open(io.BytesIO(raw)).verify()
                    file=reference_cache(raw);references.append(file)
                def event(value):
                    current=json.loads(path.read_text(encoding='utf-8'));current.update({k:value[k] for k in ('thread_id','turn_id') if k in value})
                    _write_private(path,json.dumps(current));events.put({'event':value})
                result=codex_native.run_images(prompt,references,cancelled=stop.is_set,on_event=event)
                for image in result['images']:
                    reference_cache(image['bytes']);image['base64']=base64.b64encode(image.pop('bytes')).decode()
                current=json.loads(path.read_text(encoding='utf-8'));current['result']=result;current['state']='completed'
                _write_private(path,json.dumps(current));events.put({'result':result})
            except Exception as exc:
                error=str(exc) if isinstance(exc,codex_native.CodexError) else '本机图片处理失败，请读取原任务；没有转用图片 API'
                events.put({'error':error})
            finally:events.put(None);IMAGE_LOCK.release()
        threading.Thread(target=work,daemon=True).start()
        async def stream():
            try:
                while True:
                    if await request.is_disconnected():stop.set();break
                    try:value=events.get_nowait()
                    except queue.Empty:await asyncio.sleep(.2);yield '{"heartbeat":true}\n';continue
                    if value is None:break
                    yield json.dumps(value,ensure_ascii=False)+'\n'
            finally:stop.set()
        return StreamingResponse(stream(),media_type='application/x-ndjson')

    @service.post('/recover')
    async def recover(request:Request):
        body=await request.json();path=run_path(body.get('execution_id'))
        if not path.exists():return {'error':'本机没有此原任务记录'}
        row=json.loads(path.read_text(encoding='utf-8'))
        if row.get('result'):return row['result']
        thread=row.get('thread_id');turn=row.get('turn_id')
        if not thread or not turn:return {'error':'原任务还没有可核对的轮次，没有重新生成'}
        client=None
        try:
            client=codex_native.Client(image_mode=True);client.account()
            history=client.request('thread/read',{'threadId':thread,'includeTurns':True})['thread']
            selected=next((t for t in history.get('turns',[]) if t['id']==turn),None)
            if not selected or selected['status']!='completed':return {'error':'原任务尚未成功完成，没有重新生成'}
            items=[i for i in selected.get('items',[]) if i.get('type')=='imageGeneration']
            if len(items)!=1:return {'error':'原任务没有唯一图片，没有重新生成'}
            raw,ext=codex_native.read_generated_image(items[0],thread)
            result={'images':[{'base64':base64.b64encode(raw).decode(),'extension':ext,'prompt':items[0].get('revisedPrompt','')}]}
            row['result']=result;_write_private(path,json.dumps(row));return result
        except codex_native.CodexError as exc:return {'error':str(exc)}
        finally:
            if client:client.close()

    return service


def main():
    import uvicorn
    uvicorn.run(app(),host='127.0.0.1',port=8789,log_level='warning',access_log=False)

if __name__=='__main__':main()

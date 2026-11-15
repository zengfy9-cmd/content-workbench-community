"""Explicit, reference-preserving Images API route. Never an automatic fallback."""
import base64
import io
import re
from urllib.parse import urlsplit
import httpx
from PIL import Image
from fastapi import APIRouter, HTTPException
from .config import Settings, SecretStore
from . import references

router=APIRouter()

def validate_base(base):
    if not isinstance(base,str):raise HTTPException(400,'接口地址格式无效')
    parts=urlsplit(base)
    if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise HTTPException(400,'请填写不含密码或参数的 HTTPS 接口地址')
    try:references._addresses(parts.hostname,parts.port or 443)
    except ValueError:raise HTTPException(400,'接口地址必须为公开 HTTPS 服务') from None
    return base.rstrip('/')

@router.get('/api/image-api/settings')
def settings():
    s=Settings.load()
    return {'base':s.image_api_base,'model':s.image_api_model,'has_key':bool(SecretStore().get('image_api_key'))}

@router.put('/api/image-api/settings')
def save(body:dict):
    s=Settings.load();base=validate_base(body.get('base',s.image_api_base));model=body.get('model',s.image_api_model)
    if not isinstance(model,str) or not re.fullmatch(r'[\w./:@+-]{1,160}',model):raise HTTPException(400,'模型名称格式无效')
    key=body.get('key')
    if key is not None and (not isinstance(key,str) or not 10<=len(key.strip())<=512 or not key.strip().isascii() or any(c.isspace() for c in key.strip())):raise HTTPException(400,'密钥格式无效，请填写完整 Key')
    if key:SecretStore().set('image_api_key',key.strip())
    s.image_api_base=base;s.image_api_model=model;s.save()
    return settings()

@router.get('/api/image-api/models')
def models():
    s=Settings.load();key=SecretStore().get('image_api_key')
    if not key:raise HTTPException(409,'请先保存生图 Key')
    base=validate_base(s.image_api_base)
    try:
        r=httpx.get(base+'/models',headers={'Authorization':'Bearer '+key},timeout=30,follow_redirects=False)
        if r.status_code!=200:raise HTTPException(502,f'模型列表查询失败（HTTP {r.status_code}），请检查接口及生图 Key 分组')
        ids=[x['id'] for x in r.json().get('data',[]) if isinstance(x,dict) and isinstance(x.get('id'),str)]
        return {'models':ids,'configured_model':s.image_api_model,'listed':s.image_api_model in ids,'notice':'列出模型不等于已验证生图能力；未发起图片生成。'}
    except (httpx.HTTPError,ValueError):raise HTTPException(502,'模型列表读取失败，未发起生图') from None

def run(prompt,refs,*,base,model,cancelled,on_event):
    from .codex_native import CodexError
    key=SecretStore().get('image_api_key')
    if not key:raise CodexError('生图 API Key 尚未配置；没有改用其他接口')
    base=validate_base(base)
    if cancelled():raise CodexError('已停止生成')
    on_event({'phase':'image-api'})
    # Always attach the actual identity/style references. Text-only generation
    # would silently discard the user's approved character.
    files=[('image[]',(p.name,p.read_bytes(),'image/png' if p.suffix.lower()=='.png' else 'image/jpeg')) for p in refs]
    try:
        with httpx.Client(timeout=1200,follow_redirects=False) as client:
            r=client.post(base+'/images/edits',headers={'Authorization':'Bearer '+key},
                data={'model':model,'prompt':prompt,'n':'1'},files=files)
            if r.status_code!=200:raise CodexError(f'生图 API 返回 HTTP {r.status_code}；请核对 Key 分组、模型和参考图编辑接口。没有重试或改用其他模型。')
            data=r.json().get('data',[])
            if len(data)!=1:raise CodexError('图片接口没有返回唯一成图；原任务已保留，不自动重生')
            item=data[0]
            if item.get('b64_json'):
                if len(item['b64_json'])>50_000_000:raise CodexError('图片结果过大')
                raw=base64.b64decode(item['b64_json'],validate=True)
            elif item.get('url'):
                from .tikhub_media import download_file
                from tempfile import TemporaryDirectory
                from pathlib import Path
                with TemporaryDirectory() as tmp:
                    path=Path(tmp)/'image';download_file(item['url'],path);raw=path.read_bytes()
            else:raise CodexError('图片接口结果中没有图像数据')
        if len(raw)>30*1024*1024:raise CodexError('图片结果超过30MB')
        img=Image.open(io.BytesIO(raw));img.verify()
        ext={"PNG":".png","JPEG":".jpg","WEBP":".webp"}.get(img.format)
        if not ext:raise CodexError('图片接口未返回支持的栅格图像')
        return {'images':[{'bytes':raw,'extension':ext,'prompt':item.get('revised_prompt') or prompt}]}
    except CodexError:raise
    except Exception:raise CodexError('生图 API 请求未能完成；请核对供应商记录，未自动重试、切换或再次扣费') from None

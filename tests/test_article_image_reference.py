import base64
import io
import uuid

import httpx
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from content_workbench import article_images, codex_native, image_api
from test_app import client
from test_article_images import png, setup
from test_editor_browser import editor_server


def jpeg():
    out=io.BytesIO()
    Image.new('RGB',(64,48),'#6644cc').save(out,'JPEG')
    return out.getvalue()


def upload(c, headers):
    result=c.post('/api/article-images/upload-reference',files={'file':('role.jpg',jpeg(),'image/jpeg')},headers=headers)
    result.raise_for_status()
    return result.json()['reference_key']


def test_reference_keeps_default_role_unless_explicit_and_survives_preview_to_final(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    calls=[]
    def generate(prompt,refs,**kw):
        calls.append((prompt,[(p.name,p.read_bytes()) for p in refs]))
        return {'images':[{'bytes':png(),'extension':'.png','prompt':prompt}],'message':''}
    monkeypatch.setattr(codex_native,'run_images',generate)
    endpoint='/api/article-images'
    assert client().post(endpoint+'/upload-reference',files={'file':('role.png',png(),'image/png')},headers=h).status_code==401
    assert c.post(endpoint+'/upload-reference',files={'file':('fake.png',b'not a raster','image/png')},headers=h).status_code==400
    key=upload(c,h)
    assert upload(c,h)==key
    url=endpoint+'/uploaded-reference/'+key
    assert client().get(url).status_code==401
    response=c.get(url)
    assert response.headers['cache-control']=='private, no-store'
    reference=response.content
    with Image.open(io.BytesIO(reference)) as image:
        assert image.format=='PNG' and image.size==(64,48)
    base={'title':'演示','article':'真实正文。','notes':'角色拿着一把蓝伞','count':2}
    assert c.post(endpoint+'/prompt-preview',json={**base,'reference_mode':'character'},headers=h).status_code==400
    custom={**base,'reference_key':key,'reference_mode':'character','request_key':str(uuid.uuid4())}
    preview=c.post(endpoint+'/prompt-preview',json=custom,headers=h).json()['prompt']
    assert '唯一身份依据' in preview and base['notes'] in preview
    assert calls==[]
    job=c.post(endpoint,json=custom,headers=h);job.raise_for_status()
    tid=job.json()['id']
    assert calls[-1][1]==[(key+'.png',reference)]
    assert calls[-1][0]==preview
    assert c.post(endpoint,json={**custom,'reference_mode':'style'},headers=h).status_code==409
    final=c.post(f'{endpoint}/{tid}/final',json={'panel':1,'confirm':True,'request_key':str(uuid.uuid4())},headers=h)
    final.raise_for_status()
    child=c.get(f"{endpoint}/{final.json()['id']}").json()
    assert (child['state'],child['reference_key'],child['reference_mode'])==('completed',key,'character')
    assert calls[-1][1][0]==(key+'.png',reference) and len(calls[-1][1])==2
    assert c.post(endpoint+'/cover',json={**custom,'request_key':str(uuid.uuid4())},headers=h).status_code==200
    assert calls[-1][1]==[(key+'.png',reference)] and '封面大图任务' in calls[-1][0]
    assert c.post(endpoint,json={**custom,'request_key':str(uuid.uuid4()),'reference_mode':'style'},headers=h).status_code==200
    assert calls[-1][1][0]==(key+'.png',reference)
    assert any(name=='example-character.png' for name,_ in calls[-1][1]) and '保留用户指定的角色' in calls[-1][0]
    assert c.post(endpoint,json={**base,'request_key':str(uuid.uuid4())},headers=h).status_code==200
    assert not any(name==key+'.png' for name,_ in calls[-1][1])


def test_paid_image_edit_receives_normalized_reference_bytes_after_consent(monkeypatch,tmp_path):
    c,h=setup(monkeypatch,tmp_path)
    monkeypatch.setattr(image_api,'validate_base',lambda _: 'https://images.example.test/v1')
    monkeypatch.setattr(image_api.SecretStore,'get',lambda _self,name:'fixture' if name=='image_api_key' else None)
    key=upload(c,h)
    photo=c.get('/api/article-images/uploaded-reference/'+key).content
    calls=[]
    class FakeClient:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            calls.append((url,kwargs['files'],kwargs['data']))
            return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(png()).decode()}]})
    monkeypatch.setattr(image_api.httpx,'Client',FakeClient)
    payload={'title':'参考图封面','provider':'image-api','reference_key':key,'reference_mode':'character','request_key':str(uuid.uuid4())}
    assert c.post('/api/article-images/cover',json=payload,headers=h).status_code==400
    assert not calls
    result=c.post('/api/article-images/cover',json={**payload,'confirm_api':True},headers=h)
    result.raise_for_status()
    assert c.get(f"/api/article-images/{result.json()['id']}").json()['state']=='completed'
    [(url,files,data)]=calls
    assert url.endswith('/images/edits') and files==[('image[]',(key+'.png',photo,'image/png'))]
    assert '唯一身份依据' in data['prompt']


def test_real_form_upload_preview_generate_and_history(editor_server,monkeypatch):
    base,c=editor_server
    calls=[]
    def generate(prompt,refs,**kw):
        calls.append((prompt,refs[0].read_bytes()))
        return {'images':[{'bytes':png(),'extension':'.png','prompt':prompt}],'message':''}
    monkeypatch.setattr(codex_native,'run_images',generate)
    monkeypatch.setattr(article_images,'launch',article_images.execute)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chromium')
        try:
            page=browser.new_page()
            page.goto(base+'/#/illustrations')
            page.get_by_label('封面标题',exact=True).fill('参考 IP 封面测试')
            page.get_by_label('补充要求（可选）').fill('蓝色手绘')
            uploader=page.get_by_label('上传参考 IP 图（可选）')
            uploader.set_input_files({'name':'fixture.jpg','mimeType':'image/jpeg','buffer':jpeg()})
            thumb=page.get_by_alt_text('本次上传的参考 IP 图')
            expect(thumb).to_be_visible()
            page.get_by_label('参考图怎么用').select_option('character')
            page.get_by_role('button',name='查看完整提示词（不调用模型）').click()
            expect(page.locator('.prep-rule')).to_contain_text('唯一身份依据')
            assert calls==[]
            page.get_by_role('button',name='生成封面大图',exact=True).click()
            expect(page.get_by_alt_text('根据标题生成的封面大图')).to_be_visible()
            assert len(calls)==1 and '蓝色手绘' in calls[0][0] and calls[0][1].startswith(b'\x89PNG')
            page.reload()
            expect(page.get_by_alt_text('本次上传的参考 IP 图')).to_have_count(0)
            page.get_by_text('查看本次输入 · 有参考图',exact=True).click()
            expect(page.get_by_alt_text('该任务使用的参考 IP 图')).to_be_visible()
            assert len(c.get('/api/article-images').json())==1
        finally:browser.close()

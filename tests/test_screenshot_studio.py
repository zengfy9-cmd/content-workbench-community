import base64
import copy
import io
import json
import zipfile
from contextlib import closing

from content_workbench import db, images, cloud_backup
from content_workbench.app import create_app
from content_workbench.config import Settings
from fastapi.testclient import TestClient
from test_article_images import png

URL='http://127.0.0.1:8765'
H={'Origin':URL}


def client():
    s=Settings(login_mode='local',llm_backend='disabled',origins=[URL],allowed_hosts=['127.0.0.1:8765'])
    s.save()
    return TestClient(create_app(s,start_jobs=False),base_url=URL,client=('127.0.0.1',50100))


def upload(c):
    did=c.post('/api/drafts',json={'platform':'wechat','title':'截图文章','body':'原文'},headers=H).json()['id']
    im=c.post(f'/api/drafts/{did}/images',files={'files':('真实截图.png',png(),'image/png')},headers=H).json()[0]
    url=f'/api/images/{im["id"]}'
    body=f'前面的判断。\n\n![图]({url})\n\n结尾。\n\n```prompt\n![仅为代码示例]({url})\n```'
    c.put(f'/api/drafts/{did}',json={'body':body},headers=H).raise_for_status()
    r=c.post('/api/screenshot-projects',json={'draft_id':did,'image_ids':[im['id']]},headers=H)
    r.raise_for_status()
    root='/api/screenshot-projects/'+r.json()['id']
    return did,im,body,root


def write(c,root,path,body,revision=None):
    session=c.get(root+'/session').json()
    if path=='/return-article':
        plan=c.get(root+'/return-plan')
        if plan.is_success:
            body={**body,'confirm':True,'plan_token':plan.json()['plan_token'],'selected_ids':[r['id'] for r in body['images']]}
    return c.post(root+path,json=body,headers={**H,'X-Editor-Token':session['token'],
        'X-Editor-Revision':str(session['revision'] if revision is None else revision)})


def rendered():
    from PIL import Image
    b=io.BytesIO();Image.new('RGB',(80,60),(120,180,100)).save(b,'PNG')
    return 'data:image/png;base64,'+base64.b64encode(b.getvalue()).decode()


def test_editor_authenticated_and_original_renderer_is_preserved():
    c=client();did,im,body,root=upload(c)
    result=c.get(root+'/editor')
    assert result.status_code==200
    assert result.headers['x-frame-options']=='SAMEORIGIN'
    for tool in ('rect','mosaic','arrow','text','number'):
        assert f'data-tool="{tool}"' in result.text
    assert "read('/session?include_project=true')" in result.text
    assert c.get('/api/drafts').headers['x-frame-options']=='DENY'
    assert write(c,root,'/save',c.get(root+'/project.json').json(),revision=0).status_code==409
    assert c.post(root+'/save',json={},headers=H).status_code in (400,403)
    assert c.get(f'/api/images/{im["id"]}').content==png()


def test_save_reopen_keeps_annotations_and_rejects_stale_or_external_sources():
    c=client();_,_,_,root=upload(c)
    p=c.get(root+'/project.json').json()
    p['slides'][0]['annotations']=[{'type':'mosaic','x':0,'y':0,'w':.2,'h':.2,'blockSize':20}]
    assert write(c,root,'/save',p).status_code==200
    c.close();fresh=client()
    assert fresh.get(root+'/project.json').json()['slides'][0]['annotations'][0]['type']=='mosaic'
    assert write(fresh,root,'/save',p,revision=1).status_code==409
    p['slides'][0]['image']='https://example.com/not-a-private-image.png'
    assert write(fresh,root,'/save',p).status_code==400
    p=fresh.get(root+'/project.json').json();p['slides'][0]['image']=rendered()
    assert write(fresh,root,'/save',p).status_code==400
    assert db.row('SELECT COUNT(*) n FROM screenshot_project_history')['n']==1


def test_return_only_matching_images_preserves_text_original_and_final_copy():
    c=client();did,im,body,root=upload(c)
    stamp=c.get(f'/api/drafts/{did}').json()['updated_at']
    c.post(f'/api/drafts/{did}/final-copy',json={'confirm':True,'hash':c.get(f'/api/drafts/{did}/workflow').json()['body_hash'],'expected_updated_at':stamp},headers=H).raise_for_status()
    url=f'/api/images/{im["id"]}'
    edited=body.replace('前面的判断。','我后来补充的判断。')+f'\n\n\\![正文里的普通文字]({url})\n\n![未闭合的图片语法]({url}'
    c.put(f'/api/drafts/{did}',json={'body':edited},headers=H).raise_for_status()
    assert c.get(root+'/return-plan').json()['items'][0]['positions']==[1]
    result=write(c,root,'/return-article',{'images':[{'id':'s001','png':rendered()}]})
    assert result.status_code==200,result.text
    got=c.get(f'/api/drafts/{did}').json()['body']
    new=images.for_draft(did)[-1]['id']
    assert got==edited.replace(f'![图](/api/images/{im["id"]})',f'![图](/api/images/{new})')
    assert c.get(f'/api/images/{im["id"]}').content==png()
    assert c.get(f'/api/final-drafts/{did}').json()['body']==body
    assert db.row('SELECT body FROM draft_versions WHERE draft_id=? ORDER BY id DESC',(did,))['body']==edited
    assert c.get(f'/api/drafts/{did}/workflow').json()['body_ok'] is False
    # Repeated click doesn't keep uploading the identical composed PNG.
    assert write(c,root,'/return-article',{'images':[{'id':'s001','png':rendered()}]}).json()['changed'] is False
    assert len(images.for_draft(did))==2


def test_removed_or_unbound_screenshot_never_reinserts_into_article():
    c=client();did,_,_,root=upload(c)
    c.put(f'/api/drafts/{did}',json={'body':'我删掉了图片，保留新文章。'},headers=H).raise_for_status()
    assert write(c,root,'/return-article',{'images':[{'id':'s001','png':rendered()}]}).status_code==409
    assert c.get(f'/api/drafts/{did}').json()['body']=='我删掉了图片，保留新文章。'


def test_return_requires_confirmation_and_rejects_a_stale_preview():
    c=client();did,_,body,root=upload(c)
    plan=c.get(root+'/return-plan').json()
    session=c.get(root+'/session').json()
    headers={**H,'X-Editor-Token':session['token'],'X-Editor-Revision':str(session['revision'])}
    request={'plan_token':plan['plan_token'],'selected_ids':['s001'],'images':[{'id':'s001','png':rendered()}]}
    assert c.post(root+'/return-article',json=request,headers=headers).status_code==409
    updated='另一个窗口补充内容。\n\n'+body
    c.put(f'/api/drafts/{did}',json={'body':updated},headers=H).raise_for_status()
    assert c.post(root+'/return-article',json={**request,'confirm':True},headers=headers).status_code==409
    assert c.get(f'/api/drafts/{did}').json()['body']==updated
    assert len(images.for_draft(did))==1


def test_export_zip_and_backup_contain_saved_projects():
    c=client();_,_,_,root=upload(c)
    r=write(c,root,'/export',{'images':[{'png':rendered()}],'mapping':[{'id':'s001','reviewed':True}],'single':False})
    assert r.status_code==200,r.text
    blob=c.get(r.json()['download'])
    assert blob.status_code==200
    with zipfile.ZipFile(io.BytesIO(blob.content)) as z:
        assert z.read('001-annotated.png')==base64.b64decode(rendered().split(',')[1])
        assert json.loads(z.read('mapping.json'))[0]['id']=='s001'
    assert c.get(root+'/exports/../../settings.json').status_code!=200
    assert c.get(root+'/exports/missing').status_code==404
    snapshot=cloud_backup.snapshot()
    import sqlite3
    from content_workbench.config import data_dir
    with closing(sqlite3.connect(data_dir()/'cloud-backups'/snapshot['path']/'workbench.db')) as b:
        assert b.execute('SELECT COUNT(*) FROM screenshot_projects').fetchone()[0]==1
        assert b.execute('SELECT COUNT(*) FROM screenshot_exports').fetchone()[0]==1


def test_import_local_project_does_not_import_filesystem_write_access():
    c=client();_,_,_,root=upload(c)
    p=c.get(root+'/project.json').json()
    p['article_path']='C:/not-authorized/article.md'
    r=c.post('/api/screenshot-projects',json={'project':p},headers=H)
    assert r.status_code==200
    other='/api/screenshot-projects/'+r.json()['id']
    assert c.get(other+'/session').json()['article'] is None
    assert write(c,other,'/return-article',{'images':[{'id':'s001','png':rendered()}]}).status_code==409


def test_cloud_project_and_export_require_account_session():
    c=client();_,_,_,root=upload(c)
    s=Settings(login_mode='password',storage_mode='cloud',origins=[URL],allowed_hosts=['127.0.0.1:8765'])
    s.save()
    fresh=TestClient(create_app(s,start_jobs=False),base_url=URL)
    for route in (root+'/editor',root+'/project.json',root+'/session',root+'/exports/nope','/api/screenshot-projects'):
        assert fresh.get(route).status_code==401

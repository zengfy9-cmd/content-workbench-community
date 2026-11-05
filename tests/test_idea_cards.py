import json
import pytest
from content_workbench import db, jobs, llm, prompts, idea_cards, learning
from content_workbench.config import Settings
from test_app import client, login, ORIGIN

H = {'Origin': ORIGIN}


def test_reference_candidates_reuse_analysis_without_writing(monkeypatch):
    from test_benchmarks import setup as reference_setup, run
    c, source=reference_setup()
    source=run(c,source['id'],monkeypatch)
    seen=[]
    def model(prompt,**kw):
        seen.append(prompt)
        assert kw['tools'] is None
        return json.dumps({'candidates':[{'title':'自己的流程对照','angle':'补充本人实测','audience':'创作者','problem':'流程太长','missing_materials':'自己的操作记录'}]})
    monkeypatch.setattr(llm,'run',model)
    request={'quote':'我比较了两种操作流程。','analysis_id':source['analysis']['id']}
    response=c.post(f"/api/benchmarks/{source['id']}/idea-candidates",json=request,headers=H)
    assert response.status_code==200,response.text
    assert '实际流程比较' in seen[0] and '我比较了两种操作流程。' in seen[0]
    assert not db.rows('SELECT * FROM ideas') and not db.rows('SELECT * FROM drafts')
    assert c.post(f"/api/benchmarks/{source['id']}/idea-candidates",json={**request,'quote':'不存在的原文'},headers=H).status_code==400
    assert c.post(f"/api/benchmarks/{source['id']}/idea-candidates",json={**request,'analysis_id':999},headers=H).status_code==409
    assert len(seen)==1


def test_reference_candidate_failure_does_not_save(monkeypatch):
    c=setup()
    bid=c.post('/api/benchmarks',json={'title':'外部','body':'选中原文'},headers=H).json()['id']
    monkeypatch.setattr(llm,'run',lambda *a,**kw:'invalid json')
    assert c.post(f'/api/benchmarks/{bid}/idea-candidates',json={'quote':'选中原文'},headers=H).status_code==502
    assert not db.rows('SELECT * FROM ideas')


def setup():
    c = client(); login(c)
    return c


def add_material(c, title, body):
    return c.post('/api/materials', json={'title':title,'body':body,'extract':False}, headers=H).json()['id']


def test_title_only_and_card_edit_preserve_original_angle():
    c=setup()
    iid=c.post('/api/ideas',json={'title':'只填标题','angle':'用户原有入库理由'},headers=H).json()['id']
    card=c.get('/api/ideas?status=all').json()[0]
    assert card['readiness']=='unreviewed' and card['thesis']==''
    assert card['material_ids']==[]
    patch={'audience':'内容创作者','problem':'转写后不会整理','thesis':'这是我待验证的观点','missing_materials':'一份前后对照','readiness':'needs_material'}
    assert c.patch(f'/api/ideas/{iid}',json=patch,headers=H).status_code==200
    saved=c.get('/api/ideas?status=all').json()[0]
    assert saved['angle']=='用户原有入库理由'
    assert all(saved[k]==v for k,v in patch.items())
    assert not db.rows('SELECT * FROM jobs')


def test_card_invalid_input_is_atomic():
    c=setup();iid=c.post('/api/ideas',json={'title':'卡片'},headers=H).json()['id']
    for bad in [{'readiness':'invented'},{'audience':{}},{'thesis':'x'*3001},{'material_ids':[True]},{'benchmark_ids':[999]}]:
        assert c.patch(f'/api/ideas/{iid}',json={'title':'不能部分保存',**bad},headers=H).status_code==400
    assert idea_cards.require(iid)['title']=='卡片'


def test_preparation_read_is_free_and_unchecking_legacy_material_persists():
    c=setup();mid=add_material(c,'旧素材','旧材料正文')
    iid=c.post('/api/ideas',json={'title':'选题','material_id':mid},headers=H).json()['id']
    prep=c.get(f'/api/ideas/{iid}/preparation').json()
    assert prep['selected']['material_ids']==[mid]
    assert not db.rows('SELECT * FROM jobs')
    c.patch(f'/api/ideas/{iid}',json={'material_ids':[],'benchmark_ids':[]},headers=H)
    assert c.get(f'/api/ideas/{iid}/preparation').json()['selected']['material_ids']==[]


@pytest.mark.parametrize('platform',['wechat','x','xiaohongshu','douyin'])
def test_confirmed_selection_reaches_model_and_snapshot_survives_source_changes(monkeypatch,platform):
    c=setup()
    selected=add_material(c,'选择的素材','选择素材正文：由用户自行核对归属')
    ignored=add_material(c,'未选素材','绝不能发送的未选秘密正文')
    b=c.post('/api/benchmarks',json={'title':'参考文章','body':'参考作者做过某次实验','author':'外部作者'},headers=H).json()['id']
    iid=c.post('/api/ideas',json={'title':'音频整理','material_id':ignored},headers=H).json()['id']
    style={'identity':'测试作者','guide':'文风标记：直接说事情','manual':'个人背景规则','habits':'习惯标记：保留具体例子'}
    monkeypatch.setattr(idea_cards,'style_context',lambda *args:style)
    request={'platform':platform,'preparation':{'audience':'内容创作者','thesis':'未验证的判断','missing_materials':'还缺真实结果','own_facts':'本人测试过一个短音频','material_ids':[selected],'benchmark_ids':[b]}}
    did=c.post(f'/api/ideas/{iid}/draft',json=request,headers=H).json()['id']
    frozen=c.get(f'/api/drafts/{did}').json()['writing_context']
    assert frozen['materials'][0]['id']==selected
    assert c.get(f'/api/drafts/{did}/benchmarks').status_code==200
    c.put(f'/api/materials/{selected}',json={'body':'改掉原材料'},headers=H)
    seen=[]
    def model(prompt,**kw):
        seen.append(prompt)
        assert kw['tools'] is None
        return json.dumps({'title':'测试稿','body':'测试正文，不宣称效果。','notes':'仍缺真实结果'})
    monkeypatch.setattr(llm,'run',model)
    monkeypatch.setattr(prompts,'voice_flags',lambda *args:[])
    jobs.run_job(Settings(),'draft',{'draft_id':did})
    assert len(seen)==1
    for expected in ['选择素材正文','参考作者','内容创作者','还缺真实结果','本人测试过','文风标记','习惯标记']:
        assert expected in seen[0]
    assert '绝不能发送' not in seen[0] and '改掉原材料' not in seen[0]
    assert c.get(f'/api/drafts/{did}').json()['status']=='draft'
    assert not db.rows('SELECT * FROM publications')


def test_missing_or_unready_sources_do_not_create_draft():
    c=setup();iid=c.post('/api/ideas',json={'title':'选题'},headers=H).json()['id']
    mid=add_material(c,'尚未处理','正文')
    with db.tx() as conn:conn.execute("UPDATE materials SET status='failed' WHERE id=?",(mid,))
    for selected in [[999],[mid],[True]]:
        assert c.post(f'/api/ideas/{iid}/draft',json={'platform':'wechat','preparation':{'material_ids':selected}},headers=H).status_code==400
    assert not db.rows('SELECT * FROM drafts')


def test_media_platform_prompt_includes_same_enabled_habits(monkeypatch):
    setup()
    learning.save({'lessons':[{'rule':'共用写法标记','enabled':True,'reviewed':True},{'rule':'禁用习惯标记','enabled':False}]})
    from content_workbench.platforms import writing_prompt
    prompt=writing_prompt('douyin','外部资料')
    assert '共用写法标记' in prompt and '禁用习惯标记' not in prompt

def test_image_upload_persists_with_card_and_preparation():
    import io
    from PIL import Image
    c=setup();buf=io.BytesIO();Image.new('RGB',(12,10),'teal').save(buf,format='PNG')
    response=c.post('/api/idea-images',files={'file':('截图.png',buf.getvalue(),'image/png')},headers=H)
    assert response.status_code==200,response.text
    image=response.json()['id']
    iid=c.post('/api/ideas',json={'title':'图片选题','image_ids':[image]},headers=H).json()['id']
    assert c.get('/api/ideas?status=all').json()[0]['image_ids']==[image]
    assert c.get(f'/api/images/{image}').content==buf.getvalue()
    context=idea_cards.snapshot(iid,{},Settings())
    assert context['images'][0]['id']==image
    assert '未自动识图' in idea_cards.context_prompt(context,'wechat')
    c.patch(f'/api/ideas/{iid}',json={'image_ids':[]},headers=H)
    assert idea_cards.require(iid)['image_ids']==[]
    assert c.get(f'/api/images/{image}').status_code==200
    assert c.post('/api/idea-images',files={'file':('bad.png',b'bad')},headers=H).status_code==400


def test_card_reference_selection_roundtrips_and_can_be_cleared():
    c=setup();bid=c.post('/api/benchmarks',json={'title':'可选对标','body':'原文'},headers=H).json()['id']
    iid=c.post('/api/ideas',json={'title':'关联选题','benchmark_ids':[bid]},headers=H).json()['id']
    assert c.get(f'/api/ideas/{iid}/preparation').json()['selected']['benchmark_ids']==[bid]
    c.patch(f'/api/ideas/{iid}',json={'benchmark_ids':[]},headers=H)
    assert c.get(f'/api/ideas/{iid}/preparation').json()['selected']['benchmark_ids']==[]
    assert c.get(f'/api/benchmarks/{bid}').status_code==200

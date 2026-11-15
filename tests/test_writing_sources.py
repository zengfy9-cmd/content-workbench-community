from content_workbench import ai_rules, db, idea_cards, jobs, writing_sources
from content_workbench.config import Settings
from test_idea_cards import setup, add_material, H


def test_auto_sources_are_relevant_bounded_and_frozen(monkeypatch):
    c = setup()
    mid = add_material(c, '课程教学实测', '我试了课程样片，先演示再解释。')
    add_material(c, '外部课程讨论', '说话人2：课程样片可以这么做。')
    add_material(c, '无关', '火星采矿机器人')
    main = c.post('/api/benchmarks', json={'title':'用户主对标', 'body':'主骨架：问题、过程、结果'}, headers=H).json()['id']
    extra = c.post('/api/benchmarks', json={'title':'课程样片教学参考', 'body':'课程演示参考', 'author':'他人'}, headers=H).json()['id']
    tip = c.post('/api/tips', json={'title':'课程开头', 'content':'课程样片：先让读者看到演示结果', 'category':'金句', 'origin':'他人分享', 'validation':'待验证'}, headers=H)
    assert tip.status_code == 200, tip.text
    tid = tip.json()['id']
    archived = c.post('/api/tips', json={'content':'课程样片已归档钩子','category':'开头'}, headers=H).json()['id']
    c.patch(f'/api/tips/{archived}', json={'archived':True}, headers=H)
    iid = c.post('/api/ideas', json={'title':'课程样片教学', 'benchmark_ids':[main]}, headers=H).json()['id']
    preview = ai_rules.writing_preview(iid, {'benchmark_ids':[main]})
    context = preview['context']; auto = context['auto_sources']
    assert mid in [s['id'] for s in auto['materials']]
    assert [s['id'] for s in auto['benchmarks']] == [extra]
    assert [s['id'] for s in auto['tips']] == [tid]
    assert auto['tips'][0]['origin']=='他人分享' and auto['tips'][0]['validation']=='待验证'
    assert '第一篇为主' in preview['prompt'] and '无法识别说话人' in preview['prompt']
    assert '火星采矿' not in preview['prompt'] and '已归档钩子' not in preview['prompt']
    did = c.post(f'/api/ideas/{iid}/draft', json={'platform':'x','preparation':{'benchmark_ids':[main]}}, headers=H).json()['id']
    frozen = c.get(f'/api/drafts/{did}').json()['writing_context']['auto_sources']
    c.put(f'/api/materials/{mid}', json={'body':'修改后的文字不进入旧快照'}, headers=H)
    seen=[]
    monkeypatch.setattr(jobs.llm,'run',lambda prompt,**kw:seen.append(prompt) or '{"title":"测试","body":"测试正文"}')
    monkeypatch.setattr(jobs.prompts,'voice_flags',lambda *args:[])
    jobs._draft(Settings(),{'draft_id':did})
    assert '我试了课程样片' in seen[0] and '修改后的文字' not in seen[0]
    assert c.get(f'/api/drafts/{did}').json()['writing_context']['auto_sources']==frozen
    assert len(seen)==1


def test_no_matches_and_selected_sources_deduplicated():
    c=setup()
    mid=add_material(c,'课程演示','课程演示原文')
    iid=c.post('/api/ideas',json={'title':'课程演示','material_ids':[mid]},headers=H).json()['id']
    context=idea_cards.snapshot(iid,{},Settings())
    assert context['auto_sources']['materials']==[]
    assert writing_sources.rank([{'id':1,'title':'课程','body':'课程演示'}],'火星采矿机器人',5)==[]
    assert writing_sources.rank([{'id':1,'title':'课程','body':'课程演示'}],'',5)==[]

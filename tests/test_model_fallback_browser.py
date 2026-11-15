import json

import pytest
from playwright.sync_api import sync_playwright, expect

from content_workbench import db, jobs, llm, media_imports as media
from content_workbench.config import SecretStore
from test_editor_browser import editor_server


@pytest.mark.parametrize('decision,width', [('approve',1280),('decline',390)])
def test_task_permission_is_visible_and_requires_explicit_api_consent(editor_server,decision,width):
    base, client = editor_server
    SecretStore().set('deepseek_api_key','test-key')
    mid = media.new('待接替的测试素材','文字')
    media.write(mid,raw='保留的原始正文。',stage=3,distiller='workbench')
    media.queue(mid)
    row = media.require(mid)
    jobs._set(row['job_id'],'failed','Codex 额度暂不可用')
    media.write(mid,state='failed',error='Codex 额度暂不可用')
    jobs._mark_failure('media_import',{'media_id':mid},llm.ModelBackendUnavailable('Codex 额度暂不可用',backend='codex-cli',model='test-model',task='write'))
    before = db.row('SELECT COUNT(*) n FROM jobs')['n']
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chromium')
        page = browser.new_page(viewport={'width':width,'height':900})
        try:
            page.goto(base+'/#/today')
            notice = page.get_by_label('模型接替授权')
            expect(notice).to_be_visible()
            box=notice.bounding_box()
            assert box['x']>=0 and box['x']+box['width']<=width
            assert db.row('SELECT COUNT(*) n FROM jobs')['n']==before
            page.get_by_role('button',name='查看并决定',exact=True).click()
            dialog = page.get_by_role('dialog')
            expect(dialog.get_by_text('待接替的测试素材',exact=True)).to_be_visible()
            expect(dialog.get_by_text('同意后，会将本项已取得的正文发给 DeepSeek 官方 API，继续未完成的文字整理或研究。内容可能分段处理，按实际 API 调用计费。',exact=True)).to_be_visible()
            assert dialog.evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
            if decision=='approve':
                dialog.get_by_role('button',name='允许本项使用 DeepSeek API',exact=True).click()
                expect(page.get_by_text('已授权 DeepSeek 接替本项，原文和文件继续保留',exact=True)).to_be_visible()
                latest=db.row('SELECT payload FROM jobs ORDER BY id DESC')
                assert json.loads(latest['payload'])['model_route']['backend']=='deepseek-api'
                assert db.row('SELECT COUNT(*) n FROM jobs')['n']==before+1
            else:
                dialog.get_by_role('button',name='暂不使用 DeepSeek',exact=True).click()
                expect(page.get_by_text('本项未使用 DeepSeek，可保留原件稍后再试',exact=True)).to_be_visible()
                assert db.row('SELECT COUNT(*) n FROM jobs')['n']==before
            page.reload()
            expect(page.get_by_label('主导航').first).to_be_attached()
            expect(page.get_by_label('模型接替授权')).to_have_count(0)
            assert media.require(mid)['raw']=='保留的原始正文。'
        finally:
            browser.close()

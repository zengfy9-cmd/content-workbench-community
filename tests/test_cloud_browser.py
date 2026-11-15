import pytest
from playwright.sync_api import sync_playwright,expect
from content_workbench import model_api,codex_native
from content_workbench.config import SecretStore,ensure_setup_code
from test_editor_browser import editor_server  # noqa: F401
from test_article_images import png


@pytest.mark.parametrize('editor_server',[{'login_mode':'password','storage_mode':'cloud','writing_model':'claude-opus-5-5','model_connector_path':'builtin:daitu','llm_backend':'deepseek-api'}],indirect=True)
def test_two_browsers_save_login_and_reopen_same_cloud_content(editor_server,monkeypatch):
    base,client=editor_server
    SecretStore().set('daitu_api_key','test-cloud-daitu');SecretStore().set('deepseek_api_key','test-cloud-deepseek')
    monkeypatch.setattr(codex_native,'Client',lambda *a,**k:(_ for _ in ()).throw(AssertionError('No Codex is installed')))
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chromium')
        first=browser.new_context(viewport={'width':1450,'height':1000});page=first.new_page()
        try:
            page.goto(base+'/#/?setup='+ensure_setup_code())
            expect(page.get_by_label('初始化码',exact=True)).not_to_be_empty()
            assert 'setup=' not in page.url
            page.get_by_label('账号',exact=True).fill('cloud-owner')
            page.get_by_label('密码',exact=True).fill('browser test password 2026')
            page.get_by_label('再次输入密码',exact=True).fill('browser test password 2026')
            page.get_by_role('button',name='创建账号并进入工作台',exact=True).click()
            expect(page.get_by_role('button',name='修改工作台名称',exact=True)).to_be_visible()
            # Browser's authenticated API session creates an actual test draft.
            did=page.evaluate("async()=>{const r=await fetch('/api/drafts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'跨电脑保存测试',platform:'wechat',body:'这是需要云端保存的正文。'})});return (await r.json()).id}")
            page.goto(f'{base}/#/drafts/{did}')
            editor=page.locator('.rich-body');expect(editor).to_contain_text('这是需要云端保存的正文。')
            editor.locator('p').first.click();page.keyboard.press('End');page.keyboard.insert_text(' 另一台电脑也能看到。')
            page.locator('.rich-toolbar input[type=file]').set_input_files({'name':'cloud-test.png','mimeType':'image/png','buffer':png()})
            expect(editor.locator('img')).to_have_count(1)
            page.get_by_role('button',name='保存',exact=True).click()
            expect(page.get_by_text('已保存到云端',exact=False).first).to_be_visible()
            first.close()  # Old computer/browser is gone, no cookies carried over.
            second=browser.new_context();fresh=second.new_page();fresh.goto(f'{base}/#/drafts/{did}')
            expect(fresh.get_by_role('button',name='登录工作台',exact=True)).to_be_visible()
            fresh.get_by_label('账号',exact=True).fill('cloud-owner');fresh.get_by_label('密码',exact=True).fill('browser test password 2026')
            fresh.get_by_role('button',name='登录工作台',exact=True).click()
            expect(fresh.locator('.rich-body')).to_contain_text('另一台电脑也能看到。')
            expect(fresh.locator('.rich-body img')).to_have_count(1)
            assert fresh.locator('.rich-body img').evaluate('(image)=>image.complete && image.naturalWidth>0')
            fresh.goto(base+'/#/settings')
            expect(fresh.get_by_label('写稿模型',exact=True).first).to_have_value('claude-opus-5-5')
            second.close()
        finally:browser.close()

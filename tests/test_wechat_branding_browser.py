"""Real browser: apply -> edit article numbering -> copy -> reopen, using private temp data."""
import io

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright, expect

from content_workbench import images
from test_editor_browser import editor_server


@pytest.mark.parametrize('editor_server', [{"wechat_branding": {
    "enabled": True, "name": "示例作者", "card_image_id": 1, "first_number": 0,
    "cta": "欢迎点赞、在看、转发，我们下篇见。",
}}], indirect=True)
def test_automatic_branding_has_centered_copy_and_stable_number(editor_server):
    base, client = editor_server
    data = io.BytesIO(); Image.new('RGB', (400, 150), '#173432').save(data, 'PNG')
    assert images.save(None, '临时名片', data.getvalue())['id'] == 1
    body = '## 正文标题\n\n这是自己的文章正文。'
    did = client.post('/api/drafts', json={'platform': 'wechat', 'title': '临时验收文章', 'body': body}).json()['id']
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chromium')
        context = browser.new_context(permissions=['clipboard-read', 'clipboard-write'])
        page = context.new_page()
        try:
            page.goto(f'{base}/#/drafts/{did}')
            page.get_by_role('button', name='一键排版', exact=True).click()
            expect(page.get_by_label('本篇原创编号')).to_be_visible()
            expect(page.get_by_role('button', name='复制排版', exact=True)).to_be_disabled()
            page.get_by_label('本篇原创编号').fill('6')
            page.get_by_role('button', name='保存编号', exact=True).click()
            opening = page.locator('.phone-body [data-workbench-brand="opening"]')
            footer = page.locator('.phone-body [data-workbench-brand="footer"]')
            expect(opening).to_have_text('这是「示例作者」的第 6 篇 AI 原创。')
            expect(page.get_by_label('本篇原创编号')).to_have_value('6')
            page.get_by_label('本篇原创编号').fill('12')
            expect(page.get_by_role('button', name='复制排版', exact=True)).to_be_disabled()
            page.get_by_role('button', name='保存编号', exact=True).click()
            expect(opening).to_have_text('这是「示例作者」的第 12 篇 AI 原创。')
            expect(footer).to_have_count(1)
            assert footer.locator('img').evaluate('(el) => el.complete && el.naturalWidth > 0')
            assert footer.locator('[data-workbench-brand="cta"]').evaluate('(el) => getComputedStyle(el).textAlign') == 'center'
            page.get_by_role('button', name='复制排版', exact=True).click()
            expect(page.get_by_text('已复制排版，可粘贴到公众号编辑器', exact=True)).to_be_visible()
            copied = page.evaluate('''async () => { for(const item of await navigator.clipboard.read()) {
              if(item.types.includes('text/html')) return await (await item.getType('text/html')).text(); } }''')
            assert copied.count('data-workbench-brand="footer"') == 1
            assert '这是「示例作者」的第 12 篇 AI 原创。' in copied
            assert 'text-align:center' in copied
            page.get_by_role('button', name='保存', exact=True).click()
            page.wait_for_timeout(700)
            saved = client.get(f'/api/drafts/{did}').json()
            assert saved['body'] == body
            page.reload()
            expect(page.locator('.rich-body')).to_be_visible()
            page.get_by_role('button', name='一键排版', exact=True).click()
            expect(opening).to_have_text('这是「示例作者」的第 12 篇 AI 原创。')
            expect(page.get_by_label('本篇原创编号')).to_have_value('12')
            expect(footer).to_have_count(1)
            assert '示例作者' not in page.locator('.rich-body').inner_text()
        finally:
            browser.close()

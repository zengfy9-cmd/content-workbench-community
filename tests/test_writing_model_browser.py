import json

from playwright.sync_api import sync_playwright, expect

from content_workbench import db
from test_editor_browser import editor_server


def test_choose_model_before_drafting_and_on_failed_retry(editor_server):
    base, client = editor_server
    iid = client.post('/api/ideas', json={'title': '模型选择测试'}).json()['id']
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chromium')
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(base + '/#/ideas')
            page.get_by_role('button', name='写成公众号', exact=True).click()
            assert client.get(f'/api/ideas/{iid}/preparation').status_code == 200
            assert client.get('/api/writing/models').status_code == 200
            dialog = page.get_by_role('dialog')
            picker = dialog.get_by_label('本次起草模型', exact=True)
            try:
                expect(picker).to_be_enabled()
            except AssertionError:
                raise AssertionError({'page_errors':errors, 'body':page.locator('body').inner_text()})
            picker.select_option('deepseek-v4-pro')
            dialog.get_by_role('checkbox', name='我已核对方向和材料缺口').check()
            dialog.get_by_role('button', name='确认方向，写初稿', exact=True).click()
            expect(page).to_have_url(__import__('re').compile(r'/#/drafts/\d+$'))
            first = db.row("SELECT * FROM jobs WHERE kind='draft' ORDER BY id DESC")
            payload = json.loads(first['payload'])
            assert payload['writing_model'] == 'deepseek-v4-pro'
            did = payload['draft_id']
            with db.tx() as conn:
                conn.execute("UPDATE jobs SET state='failed' WHERE id=?", (first['id'],))
                conn.execute("UPDATE drafts SET status='failed',error='测试：额度不足',updated_at=? WHERE id=?", (db.now(),did))
            page.reload()
            retry = page.get_by_role('button', name='按所选模型重新起草', exact=True)
            expect(retry).to_be_visible()
            picker = page.get_by_label('本次起草模型', exact=True)
            expect(picker).to_be_enabled()
            picker.select_option('claude-opus-5-5')
            retry.click()
            expect(page.get_by_text('已按所选模型重新起草', exact=True)).to_be_visible()
            latest = db.row("SELECT payload FROM jobs WHERE kind='draft' ORDER BY id DESC")
            assert json.loads(latest['payload']) == {'draft_id': did, 'writing_model':'claude-opus-5-5'}
            assert client.get(f'/api/drafts/{did}').json()['body'] == ''
            assert client.get(f'/api/drafts/{did}').json()['writing_context'] is not None
        finally:
            browser.close()

"""Exercise the real built editor with temporary local data; no models/accounts."""
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.sync_api import sync_playwright, expect

from content_workbench.app import create_app
from content_workbench.config import Settings


@pytest.fixture
def editor_server():
    assert (Path(__file__).parents[1] / 'web/dist/index.html').exists(), 'Build web before browser regression tests'
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    settings = Settings(login_mode='local', llm_backend='disabled', feishu_auto_sync=False,
                        feishu_auto_archive=False, origins=[base], allowed_hosts=[f'127.0.0.1:{port}'])
    settings.save()
    app = create_app(settings, start_jobs=False)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started: break
            time.sleep(.05)
        assert server.started
        with httpx.Client(base_url=base, headers={'Origin': base}, trust_env=False) as client:
            yield base, client
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        assert not thread.is_alive()


def select_text(page, text):
    page.evaluate('''text => {
      const root=document.querySelector('.rich-body'); const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
      let node; while(node=walker.nextNode()) { const start=node.textContent.indexOf(text); if(start<0)continue;
        root.focus(); const range=document.createRange();range.setStart(node,start);range.setEnd(node,start+text.length);
        const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'));return; }
      throw new Error('Test selection missing');
    }''', text)
    page.wait_for_timeout(100)


def test_editor_save_reopen_theme_alignment_prompt_and_clipboard(editor_server):
    base, client = editor_server
    body = '开头。\n\n测试署名\n\n请帮我安装测试工作台。\n\n结尾。'
    response = client.post('/api/drafts', json={'platform': 'wechat', 'title': '排版回归临时稿', 'body': body})
    response.raise_for_status()
    did = response.json()['id']
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chromium')
        context = browser.new_context(permissions=['clipboard-read', 'clipboard-write'], viewport={'width': 1500, 'height': 1000})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        try:
            page.goto(f'{base}/#/drafts/{did}')
            expect(page.locator('.rich-body')).to_contain_text('测试署名')
            select_text(page, '测试署名')
            page.get_by_role('button', name='选中段落居中', exact=True).click()
            select_text(page, '请帮我安装测试工作台。')
            page.get_by_role('button', name='提示词块', exact=True).click()
            expect(page.locator('.prompt-block')).to_have_count(1)
            # Literal Markdown, Windows paths and internal fences must round-trip unchanged.
            prompt = '# 提示词标题\nC:\\资料\\文件.txt\n```python\n**原样文字**\n```\n:::center\n:::'
            page.locator('.prompt-content').click()
            page.evaluate('''async text=>{
              await navigator.clipboard.writeText(text);
              const el=document.querySelector('.prompt-content [data-node-view-content-react]')||document.querySelector('.prompt-content');
              const sel=window.getSelection();const r=document.createRange();r.selectNodeContents(el);sel.removeAllRanges();sel.addRange(r);
              document.dispatchEvent(new Event('selectionchange'));
            }''', prompt)
            page.keyboard.press('Control+V')
            page.get_by_role('button', name='复制提示词', exact=True).click()
            assert page.evaluate('navigator.clipboard.readText()').replace('\r\n', '\n') == prompt
            page.get_by_role('button', name='保存', exact=True).click()
            page.wait_for_timeout(600)
            stored = client.get(f'/api/drafts/{did}').json()['body']
            assert ':::center\n测试署名' in stored and '````prompt' in stored
            page.reload()
            expect(page.locator('.prompt-content')).to_have_text(prompt)
            page.get_by_role('button', name='一键排版', exact=True).click()
            expect(page.locator('.phone-body')).to_contain_text('测试署名')
            for theme in ('green-white-clean', 'ink-blue-editorial', 'sand-gold-journal'):
                page.locator('#wechat-layout-theme').select_option(theme)
                expect(page.locator('.phone-body p').filter(has_text='测试署名')).to_have_css('text-align', 'center')
                expect(page.locator('.phone-body pre[data-prompt]')).to_have_text(prompt + '\n')
            page.locator('.phone-body').get_by_role('button', name='复制提示词', exact=True).click()
            assert page.evaluate('navigator.clipboard.readText()').replace('\r\n', '\n') == prompt
            page.get_by_role('button', name='复制排版', exact=True).click()
            expect(page.get_by_role('status').filter(has_text='已复制排版，可粘贴到公众号编辑器')).to_be_visible()
            exported = page.evaluate('''async()=>{const items=await navigator.clipboard.read();return items[0].getType('text/html').then(b=>b.text())}''')
            assert '<button' not in exported and 'text-align:center' in exported
            assert client.get(f'/api/drafts/{did}').json()['body'] == stored
            assert not errors
        finally:
            browser.close()
            client.delete(f'/api/drafts/{did}').raise_for_status()


def test_continuous_and_trailing_shift_enter_have_no_visible_slash(editor_server):
    base, client = editor_server
    response = client.post('/api/drafts', json={'platform': 'wechat', 'title': '换行临时稿', 'body': '起始文字'})
    response.raise_for_status()
    did = response.json()['id']
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chromium')
        page = browser.new_page()
        try:
            page.goto(f'{base}/#/drafts/{did}')
            editor = page.locator('.rich-body')
            expect(editor).to_contain_text('起始文字')
            editor.click(); page.keyboard.press('Control+End')
            page.keyboard.press('Shift+Enter'); page.keyboard.press('Shift+Enter')
            page.keyboard.insert_text('下一段文字。')
            page.keyboard.press('Shift+Enter')
            page.get_by_role('button', name='保存', exact=True).click()
            page.wait_for_timeout(600)
            assert '\\' not in client.get(f'/api/drafts/{did}').json()['body']
            page.reload()
            expect(editor).to_contain_text('下一段文字。')
            assert '\\' not in editor.inner_text()
            page.get_by_role('button', name='一键排版', exact=True).click()
            expect(page.locator('.phone-body')).to_contain_text('下一段文字。')
            assert '\\' not in page.locator('.phone-body').inner_text()
        finally:
            browser.close()
            client.delete(f'/api/drafts/{did}').raise_for_status()

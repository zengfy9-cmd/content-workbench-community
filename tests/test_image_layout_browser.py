"""Real browser -> built editor -> API -> temporary SQLite, without model calls."""
import uuid

from playwright.sync_api import sync_playwright, expect

from content_workbench import article_images, codex_native
from test_article_images import png
from test_editor_browser import editor_server  # noqa: F401: imported pytest fixture


def fake_images(monkeypatch,tmp_path):
    refs=[]
    for name in ['style.png','example-pair.png','example-character.png']:
        path=tmp_path/name;path.write_bytes(png());refs.append(path)
    monkeypatch.setattr(article_images,'references',lambda:refs.copy())
    monkeypatch.setattr(article_images,'launch',article_images.execute)
    monkeypatch.setattr(codex_native,'run_images',lambda *a,**kw:{'images':[{'bytes':png(),'extension':'.png','prompt':'browser fixture'}]})


def open_position_editor(dialog):
    """The manual position form lives in a collapsed details block."""
    dialog.locator('summary').filter(has_text='或指定位置').click()


def test_generated_big_image_opens_review_drag_edit_and_confirm(editor_server,monkeypatch,tmp_path):
    fake_images(monkeypatch,tmp_path)
    base,client=editor_server
    body='第一段讲输入。\n\n（配图）\n\n第二段讲判断。\n\n第三段讲复盘。'
    did=client.post('/api/drafts',json={'title':'浏览器配图测试','platform':'wechat','body':body}).json()['id']
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chromium')
        page=browser.new_page(viewport={'width':1500,'height':1100})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        try:
            page.goto(f'{base}/#/illustrations')
            page.get_by_role('button',name='正文配图',exact=True).click()
            page.get_by_label('载入已有稿件',exact=True).select_option(str(did))
            expect(page.get_by_label('正文',exact=True)).to_have_value(body)
            page.get_by_role('button',name='生成整组预览',exact=True).click()
            page.get_by_role('button',name='01',exact=True).click()
            page.get_by_role('button',name='生成选中的 1 张',exact=True).click()
            dialog=page.get_by_role('dialog',name='预览插图排版',exact=True)
            expect(dialog).to_be_visible()
            expect(dialog.locator('.rich-body img')).to_have_count(1)
            expect(dialog.locator('.phone-body img')).to_have_count(1)
            page.screenshot(path=str(tmp_path/'layout-preview.png'))
            assert client.get(f'/api/drafts/{did}').json()['body']==body
            dialog.get_by_role('button',name='关闭预览',exact=True).click()
            expect(dialog).to_have_count(0)
            assert client.get(f'/api/drafts/{did}').json()['body']==body
            page.get_by_role('button',name='预览插图排版',exact=True).click()
            open_position_editor(dialog)
            dialog.get_by_label('图片放置位置',exact=True).fill('第3段后')
            dialog.get_by_role('button',name='放到这个位置',exact=True).click()
            page.wait_for_function("document.querySelector('.rich-body').innerHTML.indexOf('第三段') < document.querySelector('.rich-body').innerHTML.indexOf('<img')")
            # Real HTML drag events through ProseMirror, not a mocked move API.
            image=dialog.locator('.rich-body img')
            target=dialog.locator('.rich-body p').filter(has_text='第一段讲输入').first
            before=dialog.locator('.rich-body').inner_html()
            image.drag_to(target,target_position={'x':2,'y':2})
            page.wait_for_function("old => document.querySelector('.rich-body').innerHTML !== old",arg=before)
            expect(image).to_have_count(1)
            assert dialog.locator('.rich-body').inner_html().index('<img')<dialog.locator('.rich-body').inner_html().index('第三段')
            target.click();page.keyboard.press('End');page.keyboard.insert_text(' 手动补充。')
            expect(dialog.locator('.phone-body')).to_contain_text('手动补充。')
            save=dialog.get_by_role('button',name='确认排版，保存到原稿',exact=True)
            expect(save).to_be_enabled();save.click();expect(dialog).to_have_count(0)
            saved=client.get(f'/api/drafts/{did}').json()
            assert '手动补充。' in saved['body'] and saved['body'].count('/api/images/')==1
            assert len(saved['images'])==1 and saved['cover_wechat'] is None
            page.goto(f'{base}/#/drafts/{did}')
            expect(page.locator('.rich-body img')).to_have_count(1)
            expect(page.locator('.rich-body')).to_contain_text('手动补充。')
            assert not errors
        finally:browser.close()


def test_no_marker_drag_from_attachment_and_cover_confirmation(editor_server,monkeypatch,tmp_path):
    fake_images(monkeypatch,tmp_path)
    base,client=editor_server
    body='开头的原文。\n\n中间的说明。\n\n最后一段。'
    did=client.post('/api/drafts',json={'title':'无标记测试','platform':'wechat','body':body}).json()['id']
    pid=client.post('/api/article-images',json={'title':'测试','article':body,'draft_id':did,'count':2,'request_key':str(uuid.uuid4())}).json()['id']
    client.post(f'/api/article-images/{pid}/final',json={'panel':1,'confirm':True,'request_key':str(uuid.uuid4())}).raise_for_status()
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chromium')
        page=browser.new_page(viewport={'width':1500,'height':1100})
        try:
            page.goto(f'{base}/#/illustrations')
            page.get_by_role('button',name='预览插图排版',exact=True).click()
            dialog=page.get_by_role('dialog',name='预览插图排版',exact=True)
            expect(dialog).to_be_visible();expect(dialog.locator('.rich-body img')).to_have_count(0)
            expect(dialog.get_by_role('button',name='确认排版，保存到原稿',exact=True)).to_be_disabled()
            expect(dialog).to_contain_text('未找到明确的（配图）位置')
            source=dialog.locator('.image-layout-asset').first
            target=dialog.locator('.rich-body p').filter(has_text='中间的说明').first
            source.drag_to(target,target_position={'x':2,'y':2})
            expect(dialog.locator('.rich-body img')).to_have_count(1)
            expect(dialog.locator('.phone-body img')).to_have_count(1)
            assert client.get(f'/api/drafts/{did}').json()['body']==body
            dialog.get_by_role('button',name='确认排版，保存到原稿',exact=True).click()
            expect(dialog).to_have_count(0)
            # Cover generation uses its own explicit checkbox. The cover setting
            # stays empty until this new preview is confirmed.
            page.get_by_role('button',name='封面大图',exact=True).click()
            page.get_by_label('载入已有稿件',exact=True).select_option(str(did))
            expect(page.get_by_label('封面标题',exact=True)).to_have_value('无标记测试')
            page.get_by_role('button',name='生成封面大图',exact=True).click()
            expect(dialog).to_be_visible()
            expect(dialog.get_by_role('checkbox')).to_be_checked()
            assert client.get(f'/api/drafts/{did}').json()['cover_wechat'] is None
            expect(dialog.locator('.phone-body img')).to_have_count(2)
            expect(dialog.locator('.rich-body img')).to_have_count(2)
            dialog.get_by_role('button',name='确认排版，保存到原稿',exact=True).click()
            expect(dialog).to_have_count(0)
            saved=client.get(f'/api/drafts/{did}').json()
            assert saved['cover_wechat'] and saved['body'].count('/api/images/')==2 and len(saved['images'])==2
        finally:browser.close()


def test_confirm_use_cover_from_result_card_reopens_as_first_body_image(editor_server,monkeypatch,tmp_path):
    fake_images(monkeypatch,tmp_path);base,client=editor_server
    body='原来的正文第一段。\n\n原来的正文结尾。'
    did=client.post('/api/drafts',json={'title':'确认封面直接入稿','platform':'wechat','body':body}).json()['id']
    tid=client.post('/api/article-images/cover',json={'title':'确认封面直接入稿','draft_id':did,'request_key':str(uuid.uuid4())}).json()['id']
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chromium');page=browser.new_page(viewport={'width':1450,'height':1000})
        try:
            page.goto(base+'/#/illustrations')
            expect(page.get_by_text('图片已保存，尚未插入正文。点击下方确认即可使用。',exact=True)).to_be_visible()
            page.get_by_role('button',name='确认使用，插入原稿',exact=True).click()
            expect(page.get_by_role('button',name='已插入原稿',exact=True)).to_be_disabled()
            saved=client.get(f'/api/drafts/{did}').json();assert saved['body'].endswith(body) and saved['cover_wechat']
            assert saved['body'].count('/api/images/')==1
            page.goto(f'{base}/#/drafts/{did}');expect(page.locator('.rich-body img')).to_have_count(1)
            assert page.locator('.rich-body').inner_html().index('<img')<page.locator('.rich-body').inner_html().index('原来的正文第一段')
            page.reload();expect(page.locator('.rich-body img')).to_have_count(1)
        finally:browser.close()


def test_confirm_without_marker_opens_position_editor_instead_of_success(editor_server,monkeypatch,tmp_path):
    fake_images(monkeypatch,tmp_path);base,client=editor_server
    body='第一段没有位置标记。\n\n第二段继续说明。'
    did=client.post('/api/drafts',json={'title':'确认配图选位置','platform':'wechat','body':body}).json()['id']
    pid=client.post('/api/article-images',json={'title':'配图','article':body,'draft_id':did,'count':2,'request_key':str(uuid.uuid4())}).json()['id']
    fid=client.post(f'/api/article-images/{pid}/final',json={'panel':1,'confirm':True,'request_key':str(uuid.uuid4())}).json()['id']
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chromium');page=browser.new_page(viewport={'width':1450,'height':1000})
        try:
            page.goto(base+'/#/illustrations');page.get_by_role('button',name='确认使用，插入原稿',exact=True).click()
            dialog=page.get_by_role('dialog',name='预览插图排版',exact=True);expect(dialog).to_be_visible()
            assert client.get(f'/api/drafts/{did}').json()['body']==body
            open_position_editor(dialog)
            dialog.get_by_label('图片放置位置',exact=True).fill('第1段后');dialog.get_by_role('button',name='放到这个位置',exact=True).click()
            save=dialog.get_by_role('button',name='确认排版，保存到原稿',exact=True);expect(save).to_be_enabled();save.click()
            expect(dialog).to_have_count(0);expect(page.get_by_role('button',name='已插入原稿',exact=True)).to_be_disabled()
            assert client.get(f'/api/drafts/{did}').json()['body'].count('/api/images/')==1
        finally:browser.close()

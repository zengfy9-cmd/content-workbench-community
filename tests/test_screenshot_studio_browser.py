from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from test_editor_browser import editor_server
from test_article_images import png


def test_embedded_canvas_edit_save_export_and_return(editor_server):
    base,c=editor_server
    did=c.post('/api/drafts',json={'platform':'wechat','title':'截图画布实测','body':'开头。'}).json()['id']
    im=c.post(f'/api/drafts/{did}/images',files={'files':('screenshot.png',png(),'image/png')}).json()[0]
    other=c.post(f'/api/drafts/{did}/images',files={'files':('second.png',png(),'image/png')}).json()[0]
    first=f'/api/images/{im["id"]}'
    second=f'/api/images/{other["id"]}'
    body=f'开头。\n\n![第一张]({first})\n\n中间段。\n\n![第二张]({second})\n\n结尾。'
    c.put(f'/api/drafts/{did}',json={'body':body}).raise_for_status()
    c.post(f'/api/drafts/{did}/final-copy',json={'confirm':True,'expected_updated_at':c.get(f'/api/drafts/{did}').json()['updated_at'],
        'hash':c.get(f'/api/drafts/{did}/workflow').json()['body_hash']}).raise_for_status()
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chromium')
        page=browser.new_page(viewport={'width':1700,'height':1050})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        try:
            page.goto(f'{base}/#/drafts/{did}')
            page.get_by_role('button',name='截图标注',exact=True).click()
            expect(page.get_by_role('heading',name='截图工作室',exact=True)).to_be_visible()
            for item in page.locator('.screenshot-candidate input').all():item.check()
            page.get_by_role('button',name='编辑选中的 2 张截图',exact=True).click()
            frame=page.frame_locator('iframe.screenshot-editor')
            expect(frame.get_by_role('button',name='框选',exact=True)).to_be_visible()
            expect(frame.locator('#status')).to_contain_text('已载入')
            # Exercise real canvas pointer events, then persist through the editor's own save.
            frame.get_by_role('button',name='框选',exact=True).click()
            rect=frame.locator('#canvas').bounding_box()
            x,y=rect['x']+rect['width']*.4,rect['y']+rect['height']*.4
            page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+60,y+35,steps=5);page.mouse.up()
            expect(frame.locator('#operationState')).to_contain_text('未保存更改')
            expect(frame.locator('#operationState')).to_contain_text('截图项目已保存')
            project_id=page.url.split('project=')[1]
            root=f'/api/screenshot-projects/{project_id}'
            state=c.get(root+'/project.json').json()
            assert state['slides'][0]['annotations'][0]['type']=='rect'
            # The original editor's export dialog is usable inside the authenticated frame.
            frame.get_by_role('button',name='导出单张',exact=True).click()
            expect(frame.locator('#exportHeading')).to_have_text('已保存 1 张图片到工作台')
            expect(frame.get_by_role('link',name='下载 PNG',exact=True)).to_be_visible()
            frame.locator('#closeExport').click()
            expect(frame.locator('#operationState')).to_contain_text('已导出图片')
            expect(frame.locator('#operationState')).to_contain_text('尚未回填文章')
            frame.get_by_role('button',name='再导入回文章 ↗',exact=True).click()
            modal=frame.get_by_role('dialog',name='确认回填文章')
            expect(modal).to_contain_text('截图画布实测')
            expect(modal.get_by_label('第 1 张图片 · screenshot.png')).to_be_checked()
            expect(modal.get_by_label('第 2 张图片 · second.png')).not_to_be_checked()
            page.screenshot(path=Path(__file__).parents[1]/'.verification/after-workbench-screenshot.png')
            assert c.get(f'/api/drafts/{did}').json()['body']==body
            modal.get_by_role('button',name='取消',exact=True).click()
            frame.get_by_role('button',name='再导入回文章 ↗',exact=True).click()
            modal.get_by_role('button',name='确认替换所选图片',exact=True).click()
            expect(page.get_by_role('link',name='查看回填后的原稿',exact=True)).to_be_visible(timeout=15000)
            current=c.get(f'/api/drafts/{did}').json()
            new=c.get(f'/api/drafts/{did}').json()['images'][-1]['id']
            assert current['body']==body.replace(f'![第一张]({first})',f'![第一张](/api/images/{new})')
            assert c.get(second).content==png()
            assert c.get(f'/api/images/{im["id"]}').content==png()
            assert c.get(f'/api/final-drafts/{did}').json()['body']==body
            page.reload()
            frame=page.frame_locator('iframe.screenshot-editor')
            expect(frame.locator('#status')).to_contain_text('已载入')
            expect(frame.locator('#operationState')).to_contain_text('已回填「截图画布实测」· 1/2 张')
            expect(frame.locator('#operationState')).to_contain_text('已导出图片')
            assert c.get(root+'/project.json').json()['slides'][0]['annotations']==state['slides'][0]['annotations']
            assert not errors
        finally:
            browser.close()


def test_standalone_upload_is_persistent_and_cannot_write_an_unbound_article(editor_server):
    base,c=editor_server
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chromium');page=browser.new_page(viewport={'width':1500,'height':950})
        try:
            page.goto(f'{base}/#/screenshots')
            page.get_by_label('上传截图',exact=True).set_input_files({'name':'截图.png','mimeType':'image/png','buffer':png()})
            frame=page.frame_locator('iframe.screenshot-editor')
            expect(frame.locator('#status')).to_contain_text('已载入')
            expect(frame.locator('#returnArticle')).to_be_disabled()
            expect(frame.locator('#returnNote')).to_have_text('独立项目 · 可编辑和导出，未绑定原稿')
            page.get_by_role('link',name='截图项目',exact=True).click()
            expect(page.get_by_role('link',name='截图.png 独立截图项目 继续编辑 →',exact=True)).to_be_visible()
        finally:
            browser.close()

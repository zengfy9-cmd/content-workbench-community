from pathlib import Path
from playwright.sync_api import sync_playwright, expect

from content_workbench import control_center, db
from test_article_images import png
from test_editor_browser import editor_server


ARTIFACTS=Path(__file__).parents[1]/'.verification'


def confirm(c,did):
    response=c.post(f'/api/drafts/{did}/final-copy',json={
        'confirm':True,
        'hash':c.get(f'/api/drafts/{did}/workflow').json()['body_hash'],
        'expected_updated_at':c.get(f'/api/drafts/{did}').json()['updated_at'],
    })
    response.raise_for_status()
    return response.json()


def test_final_fulltext_and_history_on_desktop_and_mobile(editor_server):
    base,c=editor_server
    did=c.post('/api/drafts',json={'title':'验收全文｜从初版开始','body':'占位后换成图文。','platform':'wechat'}).json()['id']
    img=c.post(f'/api/drafts/{did}/images',files={'files':('样例.png',png(),'image/png')}).json()[0]
    original=f'## 第一节\n\n第一段原文。\n下一行保留。\n\n![演示图](/api/images/{img["id"]})\n\n1. 第一项\n2. 第二项\n\n'+('这是用于验证手机长文阅读的段落。\n\n'*26)+'这是旧版全文末尾。'
    c.put(f'/api/drafts/{did}',json={'body':original}).raise_for_status()
    assert confirm(c,did)['version']==1
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chromium')
        context=browser.new_context(viewport={'width':1440,'height':920},permissions=['clipboard-read','clipboard-write'])
        page=context.new_page()
        try:
            page.goto(base+'/#/final-drafts')
            search=page.get_by_label('搜索成稿标题')
            search.fill('验收全文')
            page.get_by_label('筛选成稿平台',exact=True).select_option('wechat')
            page.get_by_role('link',name='验收全文｜从初版开始',exact=True).click()
            expect(page.get_by_role('heading',name='验收全文｜从初版开始',exact=True)).to_be_visible()
            expect(page.get_by_role('heading',name='第一节',exact=True)).to_be_visible()
            expect(page.get_by_text('已确认 · V1',exact=True)).to_be_visible()
            expect(page.locator('.final-reader time')).to_contain_text('北京时间')
            expect(page.locator('.final-reader-body ol li')).to_have_count(2)
            expect(page.locator('.final-reader-body br')).to_have_count(1)
            photo=page.get_by_role('img',name='演示图')
            expect(photo).to_be_visible()
            assert photo.evaluate('(img)=>img.complete && img.naturalWidth>0')
            expect(page.locator('.final-reader-body')).to_contain_text('这是旧版全文末尾。')
            page.get_by_role('button',name='复制全文',exact=True).click()
            assert page.evaluate('navigator.clipboard.readText()').replace('\r\n','\n')=='验收全文｜从初版开始\n\n'+original
            page.screenshot(path=ARTIFACTS/'after-workbench-final.png')
            page.get_by_role('link',name='← 返回成稿箱').click()
            expect(search).to_have_value('验收全文')
            expect(page.get_by_label('筛选成稿平台',exact=True)).to_have_value('wechat')
            c.put(f'/api/drafts/{did}',json={'body':'原稿现在是 B，尚未重新确认。'}).raise_for_status()
            c.delete(f'/api/images/{img["id"]}').raise_for_status()
            page.get_by_role('link',name='验收全文｜从初版开始',exact=True).click()
            expect(page.locator('.final-reader-body')).to_contain_text('这是旧版全文末尾。')
            assert c.get(f'/api/final-drafts/{did}/images/{img["id"]}').content==png()
            expect(page.locator('.final-reader-body')).not_to_contain_text('原稿现在是 B')
            assert confirm(c,did)['version']==2
            page.reload()
            expect(page.get_by_text('已确认 · V2',exact=True)).to_be_visible()
            expect(page.locator('.final-reader-body')).to_have_text('原稿现在是 B，尚未重新确认。')
            page.get_by_text('确认版本信息 · 2 个版本',exact=True).click()
            page.get_by_role('link',name='V1 · ',exact=False).click()
            expect(page.get_by_text('已确认 · V1',exact=True)).to_be_visible()
            expect(page.locator('.final-reader-body')).to_contain_text('这是旧版全文末尾。')
            page.set_viewport_size({'width':390,'height':844})
            page.goto(base+'/#/final-drafts')
            page.get_by_role('link',name='验收全文｜从初版开始',exact=True).click()
            page.get_by_text('确认版本信息 · 2 个版本',exact=True).click()
            page.get_by_role('link',name='V1 · ',exact=False).click()
            expect(page.get_by_text('已确认 · V1',exact=True)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
            assert photo.bounding_box()['width']<=390
            page.screenshot(path=ARTIFACTS/'after-workbench-final-mobile.png')
            page.get_by_text('这是旧版全文末尾。',exact=True).scroll_into_view_if_needed()
            back=page.get_by_role('link',name='← 返回成稿箱')
            expect(back).to_be_in_viewport()
            back.click()
            expect(page.get_by_role('heading',name='成稿箱',exact=True)).to_be_visible()
        finally:
            browser.close()


def test_control_quick_lookup_uses_real_sources_and_handles_empty_results(editor_server):
    base,c=editor_server
    did=c.post('/api/drafts',json={'title':'验收短稿原文','body':'确认时保留的正文。','platform':'douyin'}).json()['id']
    confirm(c,did)
    c.post('/api/plans',json={'title':'验证今天的任务列表','day':control_center._today()}).raise_for_status()
    with db.tx() as conn:
        mid=conn.execute("""INSERT INTO materials(source,title,body,created_at)
            VALUES('note','复盘原始记录','先记录原话指纹，再提出建议。\n\n我的判断：先查上下文。',?)""",(db.now(),)).lastrowid
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chromium')
        page=browser.new_page(viewport={'width':1550,'height':940})
        try:
            page.goto(base+'/#/control')
            page.get_by_role('button',name='查素材原话',exact=True).click()
            page.get_by_label('素材关键词',exact=True).fill('原话指纹')
            page.get_by_role('button',name='查询',exact=True).click()
            result=page.locator('.control-quote')
            expect(result).to_contain_text('复盘原始记录')
            expect(result.locator('blockquote')).to_have_text('先记录原话指纹，再提出建议。')
            expect(result).to_contain_text('我的判断：先查上下文。')
            expect(result).to_contain_text('收集于')
            page.screenshot(path=ARTIFACTS/'after-workbench-control.png')
            page.get_by_role('link',name=f'素材 #{mid}',exact=True).click()
            expect(page.get_by_role('heading',name='复盘原始记录',exact=True)).to_be_visible()
            page.goto(base+'/#/control')
            page.get_by_role('button',name='今天的任务',exact=True).click()
            expect(page.locator('.control-answer')).to_contain_text('验证今天的任务列表')
            expect(page.locator('.control-sources a')).to_have_attribute('href','#/today')
            page.get_by_role('button',name='查已确认成稿',exact=True).click()
            page.get_by_label('成稿标题关键词',exact=True).fill('验收短稿')
            page.get_by_role('button',name='查询',exact=True).click()
            page.get_by_role('region',name='资料快捷查询').get_by_role('link',name='验收短稿原文',exact=True).click()
            expect(page.locator('.final-reader-body')).to_have_text('确认时保留的正文。')
            page.goto(base+'/#/control')
            page.get_by_role('button',name='查素材原话',exact=True).click()
            page.get_by_label('素材关键词',exact=True).fill('这是一串库里没有的字')
            page.get_by_role('button',name='查询',exact=True).click()
            expect(page.locator('.control-answer')).to_contain_text('没找到')
            page.get_by_role('button',name='重新查询',exact=True).click()
            expect(page.get_by_label('素材关键词',exact=True)).to_have_value('')
            assert not db.rows("SELECT id FROM jobs WHERE kind='control_chat'")
        finally:
            browser.close()

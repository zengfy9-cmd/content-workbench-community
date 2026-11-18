from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from test_editor_browser import editor_server
from content_workbench import comments


def test_review_then_variant_keeps_large_editor_and_original(editor_server):
 base,c=editor_server
 did=c.post('/api/drafts',json={'platform':'wechat','title':'流程验收原稿','body':'我的真实正文，保留宽大的改稿区域。'}).json()['id']
 cid=comments.add(did,'gary','这里先核实，再确认正文',quote='真实正文')
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='chromium')
  page=browser.new_page(viewport={'width':1600,'height':1000});errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  try:
   page.goto(f'{base}/#/drafts/{did}')
   expect(page.locator('.rich-body')).to_contain_text('我的真实正文')
   assert page.locator('.rich-body').bounding_box()['width']>650
   confirm=page.get_by_role('button',name='确认正文，存入成稿箱',exact=True)
   expect(page.get_by_text('打开批注面板 · 1 条未解决')).to_be_visible()
   expect(confirm).to_be_disabled()
   page.screenshot(path=Path(__file__).parents[1]/'.verification/after-workbench-review.png')
   page.locator('.review-blockers').get_by_role('button',name='这里先核实，再确认正文',exact=False).click()
   card=page.locator(f'[data-thread="{cid}"]')
   expect(card).to_contain_text('这里先核实，再确认正文')
   expect(card).to_have_class('thread-card active')
   expect(page.locator(f'.cm-anchor.active[data-cid="{cid}"]')).to_have_text('真实正文')
   card.get_by_role('button',name='解决',exact=True).click()
   expect(confirm).to_be_enabled(timeout=2500)
   expect(page.get_by_text('没有未解决批注或待改标记。',exact=True)).to_be_visible(timeout=8000)
   page.get_by_role('button',name='确认正文，存入成稿箱',exact=True).click()
   expect(page.locator('.draft-workflow .pill').first).to_have_text('正文已确认')
   page.get_by_role('button',name='预览排版',exact=True).click()
   expect(page.locator('.phone-body')).to_contain_text('我的真实正文')
   page.get_by_role('button',name='确认排版审核',exact=True).click()
   expect(page.locator('.draft-workflow').get_by_text('排版已确认',exact=True)).to_be_visible()
   page.get_by_role('button',name='推到草稿箱',exact=True).click()
   delivery=page.get_by_role('dialog')
   expect(delivery.get_by_role('button',name='推到公众号草稿箱',exact=True)).to_be_visible()
   expect(delivery.get_by_text('X 长文草稿',exact=True)).to_have_count(0)
   expect(delivery.get_by_text('星球草稿箱',exact=True)).to_have_count(0)
   delivery.get_by_role('button',name='再改改',exact=True).click()
   page.get_by_role('button',name='制作其他版本／合成长文',exact=True).click()
   page.get_by_role('button',name='建立新稿件',exact=True).click()
   page.wait_for_url(lambda url:f'#/drafts/{did}' not in url)
   expect(page.locator('.rich-body')).to_contain_text('我的真实正文')
   expect(page.locator('.draft-workflow .pill').first).to_have_text('正文待审核')
   assert c.get(f'/api/drafts/{did}').json()['body']=='我的真实正文，保留宽大的改稿区域。'
   page.screenshot(path=str(Path(__file__).parents[1]/'.verification/workflow-editor.png'),full_page=True)
   assert not errors
  finally:browser.close()


def test_control_and_mobile_navigation_are_real_without_model_call(editor_server):
 base,c=editor_server
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='chromium');page=browser.new_page(viewport={'width':1500,'height':1000})
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  try:
   page.goto(f'{base}/#/control')
   expect(page.get_by_role('heading',name='中控',exact=True)).to_be_visible()
   expect(page.get_by_text('GPT-6.1 Sol · 高 · 本机 Codex 账号额度',exact=True)).to_be_visible()
   expect(page.get_by_role('heading',name='最近收到的对标',exact=True)).to_be_visible()
   page.screenshot(path=str(Path(__file__).parents[1]/'.verification/workflow-control.png'),full_page=True)
   page.set_viewport_size({'width':390,'height':844})
   page.goto(f'{base}/#/today')
   page.locator('.mobile-bar').get_by_role('button',name='中控',exact=True).click()
   expect(page.get_by_role('heading',name='中控',exact=True)).to_be_visible()
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
   assert not errors
  finally:browser.close()


def test_control_displays_verbatim_quote_and_small_source_link(editor_server):
 from content_workbench import db
 base,c=editor_server
 with db.tx() as conn:
  mid=conn.execute("INSERT INTO materials(source,title,body,created_at) VALUES('note','验收素材','原话有上下文，也保留 ** 星号。',?)",(db.now(),)).lastrowid
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='chromium');page=browser.new_page(viewport={'width':1200,'height':900})
  try:
   page.goto(f'{base}/#/control')
   page.get_by_label('交代任务').fill('上下文。我的素材里面有这个词的句子给我调出来')
   page.get_by_role('button',name='发送').click()
   expect(page.locator('.control-quote blockquote')).to_have_text('原话有上下文，也保留 ** 星号。')
   expect(page.get_by_role('link',name=f'素材 #{mid}')).to_have_attribute('href',f'#/materials/{mid}')
   expect(page.get_by_text('本次依据、模型与提示词')).to_have_count(0)
   assert not db.rows("SELECT 1 FROM jobs WHERE kind='control_chat'")
  finally:browser.close()


def test_short_content_goes_to_separate_final_box_without_layout(editor_server):
 base,c=editor_server
 did=c.post('/api/drafts',json={'platform':'douyin','title':'短文案验收','body':'已确认的口播正文。'}).json()['id']
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='chromium');page=browser.new_page(viewport={'width':1440,'height':900})
  try:
   page.goto(f'{base}/#/drafts/{did}')
   expect(page.get_by_role('button',name='确认排版审核',exact=True)).to_have_count(0)
   expect(page.get_by_role('button',name='记录发布',exact=True)).to_have_count(0)
   expect(page.get_by_role('button',name='制作其他版本／合成长文',exact=True)).to_have_count(0)
   page.get_by_text('更多稿件操作',exact=True).click()
   expect(page.get_by_role('button',name='记录发布',exact=True)).to_be_visible()
   page.get_by_text('更多稿件操作',exact=True).click()
   page.get_by_role('button',name='确认正文，存入成稿箱',exact=True).click()
   expect(page.get_by_role('link',name='已存成稿箱',exact=True)).to_be_visible()
   expect(page.get_by_role('link',name='打开本篇成稿',exact=True)).to_be_visible()
   page.locator('.rich-body p').first.click()
   page.keyboard.press('End')
   page.keyboard.insert_text('这段留在草稿。')
   expect(page.get_by_role('button',name='确认正文，更新成稿箱',exact=True)).to_be_visible()
   page.get_by_role('button',name='保存',exact=True).click()
   page.locator('.sidebar').get_by_role('button',name='成稿箱',exact=True).click()
   expect(page.get_by_role('heading',name='成稿箱',exact=True)).to_be_visible()
   page.get_by_label('搜索成稿标题').fill('短文案')
   page.get_by_role('link',name='短文案验收',exact=True).click()
   reader=page.locator('.final-reader')
   expect(reader).to_contain_text('已确认的口播正文。')
   expect(reader).not_to_contain_text('这段留在草稿。')
   reader.get_by_text('用本机 Codex 继续制作',exact=True).click()
   expect(page.get_by_role('button',name='剪真人口播',exact=True)).to_be_visible()
   page.screenshot(path=str(Path(__file__).parents[1]/'.verification/final-copy-preview.png'),full_page=True)
   assert c.get(f'/api/drafts/{did}').json()['publications']==[]
  finally:browser.close()

def test_simplified_navigation_preserves_contextual_entries(editor_server):
 base,c=editor_server
 did=c.post('/api/drafts',json={'platform':'wechat','title':'尚未做视频的稿件','body':'一篇长文。'}).json()['id']
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='chromium');page=browser.new_page(viewport={'width':1440,'height':900})
  try:
   page.goto(f'{base}/#/today')
   sidebar=page.locator('.sidebar')
   for label in ('视频制作','发布记录','内容灵感技巧','改稿习惯'):
    expect(sidebar.get_by_role('button',name=label,exact=True)).to_have_count(0)
   for label in ('对标库','制图工作台','成稿箱'):
    expect(sidebar.get_by_role('button',name=label,exact=True)).to_be_visible()
   for heading in ('今日任务','明日任务','今日热点'):
    expect(page.get_by_role('heading',name=heading,exact=False)).to_be_visible()
   page.get_by_role('button',name='灵感区 ›',exact=True).click()
   expect(page.get_by_role('heading',name='内容灵感技巧',exact=True)).to_be_visible()
   sidebar.get_by_text('收集内容',exact=True).click()
   sidebar.get_by_role('button',name='导入内容',exact=True).click()
   expect(page).to_have_url(f'{base}/#/imports')
   sidebar.get_by_text('收集内容',exact=True).click()
   sidebar.get_by_role('button',name='记一笔',exact=True).click()
   capture=page.get_by_role('dialog').filter(has_text='记一笔 / 添加素材')
   expect(capture).to_be_visible()
   expect(page).to_have_url(f'{base}/#/imports')
   capture.get_by_role('button',name='关闭',exact=True).click()
   expect(capture).to_have_count(0)
   sidebar.get_by_role('button',name='设置',exact=True).click()
   expect(page.get_by_role('link',name='管理改稿习惯',exact=True)).to_have_attribute('href','#/habits')
   page.get_by_role('link',name='查看旧项目',exact=True).click()
   expect(page.get_by_role('heading',name='旧视频项目',exact=True)).to_be_visible()
   expect(page.get_by_text('尚未做视频的稿件',exact=True)).to_have_count(0)
   page.goto(f'{base}/#/videos/{did}')
   expect(page.get_by_text('新视频从稿件接续',exact=True)).to_be_visible()
   page.set_viewport_size({'width':390,'height':844})
   page.goto(f'{base}/#/final-drafts')
   page.get_by_role('link',name='发布记录',exact=True).click()
   expect(page.get_by_role('heading',name='发布记录与复盘',exact=True)).to_be_visible()
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
  finally:browser.close()


def test_control_pastes_screenshot_and_persists_it_before_model_runs(editor_server):
 import base64
 from test_article_images import png
 base,c=editor_server
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='chromium');page=browser.new_page()
  try:
   page.goto(f'{base}/#/control')
   entry=page.get_by_label('交代任务')
   entry.evaluate("""(element, encoded)=>{
     const dt=new DataTransfer(), bytes=Uint8Array.from(atob(encoded), c=>c.charCodeAt(0))
     dt.items.add(new File([bytes],'clipboard.png',{type:'image/png'}))
     element.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}))
   }""",base64.b64encode(png()).decode())
   expect(page.get_by_alt_text('待发送的截图 1')).to_be_visible()
   page.get_by_role('button',name='发送',exact=True).click()
   expect(page.get_by_alt_text('已发送的截图 1')).to_be_visible()
   turn=c.get('/api/control').json()['turns'][-1]
   assert c.get(f"/api/control/{turn['id']}/images/1").content==png()
  finally:browser.close()

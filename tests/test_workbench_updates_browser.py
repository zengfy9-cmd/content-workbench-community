from playwright.sync_api import sync_playwright,expect
from test_editor_browser import editor_server

def test_settings_rules_and_multigroup_ui_and_resizer(editor_server):
 base,c=editor_server
 iid=c.post('/api/ideas',json={'title':'用于界面检查的多组选题'}).json()['id']
 for name in ['AI实操','新手入门']:c.post('/api/content-groups',json={'name':name}).raise_for_status()
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='chromium');page=browser.new_page(viewport={'width':1600,'height':1000})
  try:
   page.goto(base+'/#/ideas');page.get_by_role('button',name='分组选题：用于界面检查的多组选题',exact=True).click()
   dialog=page.get_by_role('dialog');dialog.get_by_label('AI实操',exact=True).check();dialog.get_by_label('新手入门',exact=True).check();dialog.get_by_role('button',name='保存分组',exact=True).click()
   expect(dialog).not_to_be_visible();assert len(c.get('/api/ideas').json()[0]['group_ids'])==2
   page.reload();expect(page.locator('.idea-head').first).to_contain_text('AI实操');expect(page.locator('.idea-head').first).to_contain_text('新手入门')
   page.goto(base+'/#/settings');expect(page.get_by_label('生图 API Key',exact=True)).to_be_visible()
   page.get_by_label('工作准则正文',exact=True).fill('界面修改后保存的长期写作规则。');page.get_by_role('button',name='保存准则',exact=True).click()
   expect(page.get_by_text('准则已保存，下次新任务生效；历史任务保留原提示词',exact=True)).to_be_visible()
   assert '界面修改后' in c.get('/api/ai-rules').json()['writing']['content']
   page.goto(base+'/#/benchmarks');sep=page.get_by_role('separator',name='对标列表宽度',exact=True);box=sep.bounding_box();assert box
   before=page.locator('#benchmark-library').bounding_box()['width']
   page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down();page.mouse.move(box['x']+box['width']/2+320,box['y']+box['height']/2,steps=15);page.mouse.up()
   after=page.locator('#benchmark-library').bounding_box()['width'];assert after>before+200 and after>360
   page.reload();assert page.locator('#benchmark-library').bounding_box()['width']>=after-2
   assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
   page.set_viewport_size({'width':390,'height':844});page.goto(base+'/#/settings');expect(page.get_by_label('生图 API Key',exact=True)).to_be_visible();assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
  finally:browser.close()
